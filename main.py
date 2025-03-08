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

# ✅ **完整品牌對應 Google Sheet ID**
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

# ✅ 品牌名稱別名（增加匹配範圍）
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

def fuzzy_match_brand(user_input):
    """🔍 嘗試匹配最接近的品牌名稱"""
    all_brand_names = list(BRAND_SHEETS.keys()) + [alias for aliases in BRAND_ALIASES.values() for alias in aliases]
    best_match, score = process.extractOne(user_input, all_brand_names)

    print(f"🔍 匹配品牌：{best_match}（匹配度：{score}）")  # Debug 訊息

    if score >= 70:  # ✅ **降低匹配門檻**
        for brand, aliases in BRAND_ALIASES.items():
            if best_match in aliases:
                return brand
        return best_match
    return None

def get_sheets_data(brand):
    """📊 根據品牌讀取對應的 Google Sheets 數據"""
    sheet_id = BRAND_SHEETS.get(brand)
    if not sheet_id:
        print(f"⚠️ 品牌 {brand} 沒有對應的 Google Sheets ID")
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            sheet_name = sheet.title
            print(f"📂 讀取分頁：{sheet_name}")

            try:
                data = sheet.get_all_records(expected_headers=[])
                if not data:
                    print(f"⚠️ {sheet_name} 分頁是空的，跳過處理。")
                    continue
                all_data[sheet_name] = data
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁時發生錯誤：{e}")
                continue  

        if all_data:
            print("✅ 成功讀取 Google Sheets！")
            return all_data
        else:
            print("⚠️ 該品牌的 Google Sheets 沒有可用數據")
            return None

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗：{e}")
        return None

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
    user_message = event.message.text.strip()
    reply_token = event.reply_token  

    print(f"📩 收到訊息：{user_message}")  # Debug 訊息

    # ✅ **品牌識別 & 型號查詢**
    matched_brand = fuzzy_match_brand(user_message)

    if matched_brand:
        print(f"✅ 確認品牌：{matched_brand}")
        sheet_data = get_sheets_data(matched_brand)

        if sheet_data:
            formatted_text = "\n".join(f"{key}: {value}" for key, value in sheet_data.items())
            reply_text = f"🔍 品牌 **{matched_brand}** 資訊：\n{formatted_text[:500]}..."  # 限制長度
        else:
            reply_text = f"⚠️ 目前無法取得 **{matched_brand}** 的建材資訊。"
    else:
        reply_text = "⚠️ 請提供品牌名稱，例如：『富美家 1234 型號』，才能查詢建材資訊。"

    print(f"📤 回應使用者：{reply_text}")  # Debug 訊息

    # ✅ **回應 LINE 訊息**
    reply_message = ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])

    try:
        line_bot_api.reply_message(reply_message)
        print(f"✅ 成功回應 LINE 訊息")
    except Exception as e:
        print(f"❌ LINE Bot 回覆錯誤: {e}")

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    print("🚀 LINE Bot 伺服器啟動中...")
    serve(app, host="0.0.0.0", port=port)
