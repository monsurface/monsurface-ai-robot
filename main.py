from flask import Flask, request
import gspread
import requests
from google.oauth2.service_account import Credentials
import os
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import TextMessage

app = Flask(__name__)

# ✅ 讀取環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")  # Dropbox 下載 `credentials.json`
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # Google Sheets ID

# ✅ 下載 Google API 憑證
LOCAL_FILE_PATH = "credentials.json"

def download_credentials():
    """從 Dropbox 下載 credentials.json"""
    response = requests.get(DROPBOX_URL)
    if response.status_code == 200:
        with open(LOCAL_FILE_PATH, "wb") as file:
            file.write(response.content)
        print("✅ credentials.json 下載成功！")
    else:
        raise FileNotFoundError(f"❌ 下載失敗，HTTP 狀態碼: {response.status_code}")

download_credentials()

# ✅ 讀取 Google Sheets API 憑證
credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

# ✅ 讀取 Google Sheets 數據
def get_all_sheets_data():
    """讀取 Google Sheets 內所有分頁的數據"""
    try:
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            sheet_name = sheet.title  # 取得分頁名稱
            print(f"📂 讀取分頁：{sheet_name}")

            try:
                data = sheet.get_all_records(expected_headers=[])  # 讀取所有行，避免表頭錯誤
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁時發生錯誤：{e}")
                continue  # 跳過這個分頁

            if not data:
                print(f"⚠️ 警告：{sheet_name} 分頁是空的，跳過處理。")
                continue

            # 儲存數據
            all_data[sheet_name] = data

        if not all_data:
            print("❌ 錯誤：Google Sheets 沒有任何可用數據！請檢查表單內容。")
            return None

        print("✅ Google Sheets 讀取完成！")
        return all_data

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗，錯誤原因：{e}")
        return None

# ✅ 設定 LINE Bot
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=["GET"])
def home():
    return "Line Bot 啟動成功！"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"Webhook Error: {e}")
        return "Error", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        # **測試：固定回覆訊息**
        reply_message = "✅ LINE BOT 測試成功！你的訊息是：" + event.message.text

        # **確保正確傳遞 TextMessage**
        text_message = TextMessage(text=reply_message)

        line_bot_api.reply_message(
            reply_token=event.reply_token,
            messages=[text_message]
        )
        print("✅ LINE Bot 訊息發送成功！")
    except Exception as e:
        print(f"❌ LINE Bot 回覆錯誤: {e}")

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    serve(app, host="0.0.0.0", port=port)
