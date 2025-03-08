from flask import Flask, request
import gspread
import requests
from google.oauth2.service_account import Credentials
import openai
import os
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

app = Flask(__name__)

# ✅ 讀取環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")  # Dropbox 下載 `credentials.json`
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # Google Sheets ID

# ✅ 下載 Google API 應證
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

# ✅ 讀取 Google Sheets API 應證
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
                data = sheet.get_all_records(expected_headers=[])  # 讀取所有行
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁時發生錯誤：{e}")
                continue  # 跳過這個分頁

            if not data:
                print(f"⚠️ 警告：{sheet_name} 分頁是空的，跳過處理。")
                continue

            all_data[sheet_name] = data

        if not all_data:
            print("❌ 錯誤：Google Sheets 沒有任何可用數據！")
            return None

        print("✅ Google Sheets 讀取完成！")
        return all_data

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗，錯誤原因：{e}")
        return None


# ✅ 設定 OpenAI API
openai.api_key = OPENAI_API_KEY

def ask_chatgpt(user_question):
    """讓 ChatGPT 讀取 Google Sheets 內容並回答用戶問題"""
    knowledge_base = get_all_sheets_data()
    if not knowledge_base:
        return "❌ 目前無法讀取建材資料，請稍後再試。"

    formatted_text = "📚 這是最新的建材資料庫，包含所有詳細資訊：\n"
    for sheet_name, records in knowledge_base.items():
        formatted_text += f"\n📂 分類：{sheet_name}\n"
        for row in records:
            formatted_text += "\n- " + "\n- ".join([f"{key}：{value}" for key, value in row.items()]) + "\n"

    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}
    用戶的問題是：「{user_question}」
    請根據建材資料以條列式回答問題。
    如果問題與建材無關，請回答：「這個問題與建材無關，我無法解答。」。
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "你是一位建材專家，專門回答與建材相關的問題。"},
            {"role": "user", "content": prompt}
        ]
    )

    return response["choices"][0]["message"]["content"]

# ✅ 設定 LINE Bot
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
    user_message = event.message.text.strip()
    reply_token = event.reply_token
    reply_text = ask_chatgpt(user_message)
    reply_message = ReplyMessageRequest(
        reply_token=reply_token,
        messages=[TextMessage(text=reply_text)]
    )
    line_bot_api.reply_message(reply_message)

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    serve(app, host="0.0.0.0", port=port)
