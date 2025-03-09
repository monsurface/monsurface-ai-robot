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

# ✅ **讀取環境變數**
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")  # **權限 Google Sheets ID**

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

# ✅ **檢查使用者是否有權限**
def check_user_permission(user_id):
    """檢查 user_id 是否有權限，只有權限是『是』才回傳 True"""
    try:
        security_sheet = client.open_by_key(SECURITY_SHEET_ID).sheet1  
        data = security_sheet.get_all_records()

        for row in data:
            if row["Line User ID"].strip() == user_id:
                print(f"🔍 找到使用者 {user_id}，權限: {row['是否有權限'].strip()}")
                if row["是否有權限"].strip() == "是":
                    return True  # ✅ **權限是「是」，回傳 True**
        
        print(f"⛔️ 使用者 {user_id} 無權限")
        return False  # ❌ **沒有權限，回傳 False**

    except Exception as e:
        print(f"❌ 讀取權限 Google Sheets 失敗: {e}")
        return False

# ✅ **品牌模糊匹配**
def fuzzy_match_brand(user_input):
    all_brand_names = list(BRAND_SHEETS.keys()) + [alias for aliases in BRAND_ALIASES.values() for alias in aliases]
    match_result = process.extractOne(user_input, all_brand_names)

    if match_result:
        best_match, score = match_result[:2]
        if score >= 70:
            for brand, aliases in BRAND_ALIASES.items():
                if best_match in aliases:
                    return brand
            return best_match
    return None

# ✅ **設定 LINE Bot**
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=["POST"])
def callback():
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
    """📩 **處理 LINE 訊息，先檢查權限，權限通過才執行查詢**"""
    user_id = event.source.user_id
    reply_token = event.reply_token  

    # **✅ 權限檢查**
    if not check_user_permission(user_id):
        reply_text = "⚠️ 您沒有查詢權限，請聯繫管理員開通權限。"
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)]))
        return  

    # ✅ **處理使用者輸入**
    user_message = " ".join(event.message.text.strip().split())  # **去除多餘空格**
    print(f"📩 收到訊息：{user_message}")

    matched_brand = fuzzy_match_brand(user_message)

    if matched_brand:
        print(f"✅ 確認品牌：{matched_brand}")
        reply_text = f"🔍 品牌 **{matched_brand}** 資訊查詢成功！"
    else:
        reply_text = "⚠️ 請提供品牌名稱，例如：『品牌 abc 型號 123』，查詢建材資訊。"

    # ✅ **回應使用者**
    line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)]))

if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 伺服器啟動中...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))