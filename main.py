from flask import Flask, request
import gspread
import requests
import openai
import os
from google.oauth2.service_account import Credentials
from rapidfuzz import process
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

app = Flask(__name__)

# ✅ 讀取環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")  # ✅ 權限管理 Google Sheet

# ✅ 各品牌對應 Google Sheet ID
BRAND_SHEETS = {
    "富美家": os.getenv("SPREADSHEET_ID_A"),
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
    "熱門主推": os.getenv("SPREADSHEET_ID_L"),
}

# ✅ 品牌名稱別名（用於模糊匹配）
BRAND_ALIASES = {
    "富美家": ["富美家", "Formica"],
    "愛卡AICA-愛克板": ["愛卡", "AICA", "愛克板"],
    "鉅莊-樂維LAVI": ["鉅莊", "樂維", "LAVI"],
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
    """🔍 檢查使用者是否有權限"""
    try:
        security_sheet = client.open_by_key(SECURITY_SHEET_ID).sheet1
        data = security_sheet.get_all_records()

        for row in data:
            if row["Line User ID"].strip() == user_id:
                print(f"🔍 找到使用者 {user_id}，權限：{row['是否有權限']}")
                return row["是否有權限"].strip() == "是"
                
        print(f"⚠️ 使用者 {user_id} 未登錄於權限表")
        return False

    except Exception as e:
        print(f"❌ 讀取權限表失敗：{e}")
        return False

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
    """📊 根據品牌讀取對應的 Google Sheets 數據"""
    sheet_id = BRAND_SHEETS.get(brand)
    if not sheet_id:
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            raw_data = sheet.get_all_records(expected_headers=[])
            formatted_data = {str(i): row for i, row in enumerate(raw_data) if isinstance(row, dict)}

            all_data[sheet.title] = formatted_data

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
    如果無法查詢到建材資訊，請回答：
    「⚠️ 請提供品牌名稱，例如：『品牌 abc 型號 123』，查詢建材資訊。」。
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
    """處理使用者傳送的訊息"""
    user_id = event.source.user_id  
    if not check_user_permission(user_id):
        reply_text = "❌ 您沒有查詢權限，請聯絡管理員開通權限。"
    else:
        user_message = " ".join(event.message.text.strip().split())
        matched_brand = fuzzy_match_brand(user_message)
        sheet_data = get_sheets_data(matched_brand) if matched_brand else None
        reply_text = ask_chatgpt(user_message, sheet_data) if sheet_data else "⚠️ 無法找到建材資訊。"

    line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 伺服器啟動中...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))