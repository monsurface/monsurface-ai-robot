from flask import Flask, request
import gspread
import requests
import openai
import os
import pytz
from datetime import datetime
from google.oauth2.service_account import Credentials
from rapidfuzz import process
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage
import re

instruction_text = """
🍀瑰貝鈺AI建材小幫手服務指南☘️

🔹 **請依照以下方式查詢建材資訊：**
1️⃣ 查詢建材資訊：請輸入品牌與型號，例如：「品牌 ABC 型號 123」。
2️⃣ 獲取熱門建材推薦：請輸入「熱門主推」，即可查看最新主打建材資訊。
3️⃣ 查詢技術資訊：請輸入「技術資訊」，獲取所有建材品牌的技術資料連結。
4️⃣ 瑰貝鈺傳送門：https://portaly.cc/Monsurface 各品牌綜合資訊。

📌 **請用以上方式輸入，以獲取精準資訊！**
"""

app = Flask(__name__)

# ✅ 讀取環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")  # ✅ 權限管理 Google Sheet

# ✅ 各品牌對應 Google Sheet ID
BRAND_SHEETS = {
    "富美家": os.getenv("SPREADSHEET_ID_A"),  # 總表
    "新日綠建材": os.getenv("SPREADSHEET_ID_B"),
    "鉅莊-樂維LAVI": os.getenv("SPREADSHEET_ID_C"),
    "愛卡AICA-愛克板": os.getenv("SPREADSHEET_ID_D"),
    "松華-松耐特及系列品牌": os.getenv("SPREADSHEET_ID_E"),
    "吉祥": os.getenv("SPREADSHEET_ID_F"),
    "華旗": os.getenv("SPREADSHEET_ID_G"),
    "科彰": os.getenv("SPREADSHEET_ID_H"),
    "華槶線板": os.getenv("SPREADSHEET_ID_I"),
    "魔拉頓 Melatone": os.getenv("SPREADSHEET_ID_J"),
    "利明礦石軟片": os.getenv("SPREADSHEET_ID_K"),
    "科友-KD": os.getenv("SPREADSHEET_ID_L"),
}

# ✅ 「富美家」品牌有額外的子表
SUBSHEET_IDS = {
    "富美家A": os.getenv("SPREADSHEET_ID_A_A"),
    "富美家B": os.getenv("SPREADSHEET_ID_A_B"),
    "富美家C": os.getenv("SPREADSHEET_ID_A_C"),
    "富美家D": os.getenv("SPREADSHEET_ID_A_D"),
    "富美家E": os.getenv("SPREADSHEET_ID_A_E"),
}

# ✅ 品牌名稱別名（用於模糊匹配）
BRAND_ALIASES = {
    "富美家": ["富美家", "Formica"],
    "愛卡AICA-愛克板": ["愛卡", "AICA", "愛克板"],
    "鉅莊-樂維LAVI": ["鉅莊", "樂維", "LAVI"],
    "松華-松耐特及系列品牌": ["松華", "松耐特", "萊適寶", "松華板"],
    "魔拉頓 Melatone": ["魔拉頓", "Melatone"],
}

# ✅ **下載 Google API 憑證**
LOCAL_FILE_PATH = "credentials.json"

def download_credentials():
    response = requests.get(DROPBOX_URL)
    if response.status_code == 200:
        with open(LOCAL_FILE_PATH, "wb") as file:
            file.write(response.content)
        print("✅ credentials.json 下載成功！")
    else:
        raise FileNotFoundError(f"❌ 下載失敗，HTTP 狀態碼: {response.status_code}")

download_credentials()

# ✅ **讀取 Google Sheets API 憑證**
credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

def check_user_permission(user_id):
    """🔍 檢查使用者是否有權限，並更新使用次數與最後查詢時間（台灣時間）"""
    try:
        security_sheet = client.open_by_key(SECURITY_SHEET_ID).sheet1
        data = security_sheet.get_all_records()

        for index, row in enumerate(data, start=2):  # **start=2 是因為 Google Sheets 第一行是標題**
            if row["Line User ID"].strip() == user_id:
                has_permission = row["是否有權限"].strip() == "是"
                usage_count = int(row["使用次數"]) if row["使用次數"] else 0  # 轉換為數字，若為空則設為 0

                if has_permission:
                    # ✅ **更新使用次數**
                    new_usage_count = usage_count + 1
                    security_sheet.update_cell(index, 3, new_usage_count)  # C列（使用次數）

                    # ✅ **設定台灣時間**
                    taiwan_tz = pytz.timezone("Asia/Taipei")
                    current_time = datetime.now(taiwan_tz).strftime("%Y-%m-%d %H:%M:%S")
                    security_sheet.update_cell(index, 4, current_time)  # D列（最後查詢時間）

                    print(f"🔍 找到使用者 {user_id}，權限：{row['是否有權限']}，使用次數更新為 {new_usage_count}")
                    print(f"🕒 更新最後查詢時間（台灣時間）：{current_time}")
                    return True  # ✅ **有權限，允許查詢**
                else:
                    print(f"🚫 使用者 {user_id} 沒有權限")
                    return False  # ❌ **無權限，禁止查詢**

        print(f"⚠️ 使用者 {user_id} 未登錄於權限表")
        return False  # ❌ **找不到使用者，禁止查詢**

    except Exception as e:
        print(f"❌ 讀取權限表失敗：{e}")
        return False

def find_model_in_main_sheet(model):
    """
    ✅ 在「富美家」總表 (SPREADSHEET_ID_A) 查詢該型號，確認它是否存在，並找到對應的子表名稱
    """
    spreadsheet_id = BRAND_SHEETS.get("富美家")  # 取得總表 ID
    if not spreadsheet_id:
        print("❌ 找不到 富美家 的 Google Sheets ID")
        return None  # 總表不存在

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
        sheet = spreadsheet.worksheet("總表")  # 確保「總表」存在

        data = sheet.get_all_records()  # 讀取所有數據

        for row in data:
            sheet_model = str(row.get("型號", "")).strip()  # 避免 KeyError 並去除空格
            subsheet_name = str(row.get("子表", "")).strip()  # 避免 KeyError 並去除空格

            if model.strip() == sheet_model:  # **確保比對時不受空格影響**
                if subsheet_name and f"富美家{subsheet_name}" in SUBSHEET_IDS:
                    print(f"🔍 找到型號 {model}，對應子表：富美家{subsheet_name}")
                    return f"富美家{subsheet_name}"

        print(f"⚠️ 在富美家總表內找不到型號 {model}")
        return None  # 若查無資料，回傳 None

    except Exception as e:
        print(f"❌ 讀取富美家總表時發生錯誤：{e}")
        return None

# 測試「富美家」某型號是否能正確找到子表
test_model = "8574NM"  # 這個型號應該存在於總表
subsheet_result = find_model_in_main_sheet(test_model)
print(f"📌 測試結果：{subsheet_result}")

def get_sheets_data_from_subsheet(subsheet_key, model):
    """
    ✅ 讀取對應的子表 (SPREADSHEET_ID_A_X) 的詳細資料
    """
    sheet_id = SUBSHEET_IDS.get(subsheet_key)

    if not sheet_id:
        print(f"⚠️ 無法找到對應的子表 ID：{subsheet_key}")
        return None

    try:
        # 連接到 Google Sheets
        spreadsheet = client.open_by_key(sheet_id)
        sheet = spreadsheet.worksheet("子表")  # 確保所有子表名稱為「子表」

        # 讀取所有資料
        data = sheet.get_all_records()

        print(f"🔍 正在 {subsheet_key} 中查找型號：{model}")

        for row in data:
            sheet_model = str(row.get("型號", "")).strip()
            if sheet_model == model.strip():
                print(f"✅ 在 {subsheet_key} 找到型號 {model}")
                return row  # 回傳該型號的所有詳細資訊

        print(f"⚠️ 型號 {model} 不在 {subsheet_key} 中")
        return None  # 若查無資料，回傳 None

    except Exception as e:
        print(f"❌ 讀取子表 {subsheet_key} 失敗: {e}")
        return None

def fuzzy_match_brand(user_input):
    """🔍 嘗試匹配最接近的品牌名稱"""
    all_brand_names = list(BRAND_SHEETS.keys()) + [alias for aliases in BRAND_ALIASES.values() for alias in aliases]
    match_result = process.extractOne(user_input, all_brand_names)

    if match_result:
        best_match, score = match_result[:2]  
        print(f"🔍 匹配品牌：{best_match}（匹配度：{score}）")
        if score >= 70:
            for brand, aliases in BRAND_ALIASES.items():
                if best_match in aliases:
                    return brand
            return best_match
    return None

def get_sheets_data(brand):
    """📊 根據品牌讀取對應的 Google Sheets 數據，並確保型號格式統一"""
    sheet_id = BRAND_SHEETS.get(brand)
    if not sheet_id:
        print(f"❌ 找不到品牌 {brand} 對應的 Google Sheets ID")
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            raw_data = sheet.get_all_records(expected_headers=[])  # 讀取分頁資料

            # ✅ **確保型號統一清理格式**
            formatted_data = {
                str(row.get("型號", "")).strip(): {k.strip(): str(v).strip() for k, v in row.items()}
                for row in raw_data
                if isinstance(row, dict) and "型號" in row
            }

            if formatted_data:
                all_data[sheet.title] = formatted_data  # ✅ **按分頁名稱儲存**
                print(f"📄 讀取 {brand} 的分頁 [{sheet.title}]，共 {len(formatted_data)} 筆型號")

        if not all_data:
            print(f"⚠️ {brand} 的 Google Sheets 內沒有任何數據！")
        else:
            # **列出所有可用型號（前 10 筆）**
            all_models = [model for sheet in all_data.values() for model in sheet.keys()]
            print(f"✅ {brand} 數據讀取成功，共 {len(all_models)} 筆型號")
            print(f"📌 {brand} 內的可用型號（前 10 筆）：{all_models[:10]}")

        return all_data if all_data else None

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗：{e}")
        return None

def ask_chatgpt(user_question, formatted_text):
    """讓 ChatGPT 讀取 Google Sheets 內容並條列式回答用戶問題"""
    
    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}

    用戶的問題是：「{user_question}」
    請根據提供的品牌與型號，完整提供建材資訊，使用繁體中文並以條列式回答。
    如果無法查詢到建材資訊，請回答「{instruction_text}」
    """

    models_to_try = ["gpt-3.5-turbo", "gpt-3.5-turbo-0125", "gpt-3.5-turbo-16k"]
    client = openai.Client(api_key=OPENAI_API_KEY)

    for model in models_to_try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": "你是一位建材專家，專門回答與建材相關的問題。"},
                          {"role": "user", "content": prompt}]
            )

            if response and response.choices:
                return response.choices[0].message.content

        except openai.OpenAIError as e:
            print(f"⚠️ OpenAI API 錯誤: {str(e)}，嘗試下一個模型...")
            continue  

    return "⚠️ 抱歉，目前無法取得建材資訊，請稍後再試。"

# ✅ **設定 LINE Bot**
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=["POST"])
def callback():
    """處理 LINE Webhook 事件"""
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        return "Error", 400

    return "OK", 200
    
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """📩 處理使用者傳送的訊息"""
    user_id = event.source.user_id  

    # ✅ **檢查使用者權限**
    if not check_user_permission(user_id):
        reply_text = "❌ 您沒有查詢權限，請聯絡管理員開通權限。"

    else:
        user_message = " ".join(event.message.text.strip().split())

        # ✅ **「熱門主推」與「技術資訊」的快捷連結**
        if user_message == "熱門主推":
            hot_sheet_url = os.getenv("HOT_SHEET_URL", "⚠️ 未設定熱門主推連結")
            reply_text = f"📌 **熱門主推建材資訊**\n請點擊以下連結查看：\n{hot_sheet_url}"

        elif user_message == "技術資訊":
            tech_sheet_url = os.getenv("TECH_SHEET_URL", "⚠️ 未設定技術資訊連結")
            reply_text = f"🔧 **技術資訊總覽**\n請點擊以下連結查看：\n{tech_sheet_url}"

        else:
            # ✅ **解析品牌與型號**
            words = user_message.split()

            if len(words) == 2:
                # **格式 1：「品牌 型號」**
                brand, model = words[0], words[1]

            elif len(words) == 4 and words[0] == "品牌" and words[2] == "型號":
                # **格式 2：「品牌 富美家 型號 8574NM」**
                brand, model = words[1], words[3]

            else:
                reply_text = "⚠️ 請提供完整品牌與型號，例如：\n『品牌 富美家 型號 8574NM』\n或『富美家 8574NM』"
                line_bot_api.reply_message(
                    ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
                )
                return  # ⛔ **輸入格式錯誤，直接返回**

            print(f"🔍 解析輸入：品牌 = {brand}, 型號 = {model}")

            # ✅ **檢查品牌是否存在於 BRAND_SHEETS**
            matched_brand = fuzzy_match_brand(brand)

            if not matched_brand:
                reply_text = f"⚠️ 找不到品牌 **{brand}**，請確認品牌名稱是否正確。"
            else:
                print(f"✅ 成功匹配品牌：{matched_brand}")

                # ✅ **「富美家」的特殊處理：使用「總表 → 子表」查詢**
                if matched_brand == "富美家":
                    print(f"🔍 進入『富美家』總表查詢，型號：{model}")
                    subsheet_key = find_model_in_main_sheet(model)

                    if subsheet_key and subsheet_key in SUBSHEET_IDS:
                        print(f"✅ 找到子表：{subsheet_key}，開始查詢 {model}")
                        sheet_data = get_sheets_data_from_subsheet(subsheet_key, model)

                        if sheet_data:
                            formatted_text = "\n".join(f"{key}: {value}" for key, value in sheet_data.items())
                            reply_text = ask_chatgpt(user_message, formatted_text)
                        else:
                            reply_text = f"⚠️ 找不到 **{matched_brand} {model}** 的詳細資料，請確認型號是否正確。"
                    else:
                        reply_text = f"⚠️ 找不到 **{matched_brand} {model}**，請確認型號是否正確。"

                else:
                    # ✅ **處理其他品牌（不需要查找子表）**
                    print(f"🔍 進入查詢品牌 {matched_brand}，型號 {model}")

                    sheet_data = get_sheets_data(matched_brand)

                    if not sheet_data:
                        print(f"⚠️ 無法獲取 {matched_brand} 的數據，請檢查 Google Sheets 設定")
                        reply_text = f"⚠️ 找不到品牌 **{matched_brand}**，請確認品牌名稱是否正確。"
                    else:
                        print(f"✅ {matched_brand} 數據載入成功，共 {len(sheet_data)} 個分頁")
                        found_model = False  # 用於檢查是否找到型號

                        for sheet_name, models in sheet_data.items():
                            if model in models:
                                found_model = True
                                formatted_text = "\n".join(f"{key}: {value}" for key, value in models[model].items())
                                reply_text = ask_chatgpt(user_message, formatted_text)
                                print(f"✅ 在 {sheet_name} 找到型號 {model}")
                                break  # 找到後立即結束迴圈

                        if not found_model:
                            print(f"⚠️ {matched_brand} 內找不到型號 {model}")
                            reply_text = f"⚠️ 找不到 **{matched_brand} {model}**，請確認型號是否正確。"

    # ✅ **回應使用者**
    line_bot_api.reply_message(
        ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)])
    )


if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 伺服器啟動中...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
