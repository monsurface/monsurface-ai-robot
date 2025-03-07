from flask import Flask, request
import gspread
import requests
from google.oauth2.service_account import Credentials
import openai
import os
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import TextMessage

app = Flask(__name__)

# ✅ 讀取環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
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

def get_all_sheets_data():
    """讀取 Google Sheets 內所有分頁的數據"""
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    all_data = {}

    for sheet in spreadsheet.worksheets():
        sheet_name = sheet.title  # 取得分頁名稱
        data = sheet.get_all_records()  # 取得所有數據
        all_data[sheet_name] = data

    return all_data

# ✅ 設定 OpenAI API
openai.api_key = OPENAI_API_KEY

def ask_chatgpt(user_question):
    """讓 ChatGPT 讀取 Google Sheets 內容並回答用戶問題"""
    knowledge_base = get_all_sheets_data()  # 每次都讀取最新的資料
    formatted_text = "這是最新的建材資料庫，包括多個類別：\n"

    for sheet_name, records in knowledge_base.items():
        formatted_text += f"\n📂 分類：{sheet_name}\n"
        for row in records:
            formatted_text += f"型號：{row['型號']}，材質：{row['材質']}，顏色：{row['顏色']}，價格：{row['價格']} 元。\n"

    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}

    用戶的問題是：「{user_question}」
    請根據建材資料回答問題，並提供詳細解釋。
    如果問題與建材無關，請回答「這個問題與建材無關，我無法解答。」。
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": "你是一個建材專家，可以根據建材資料回答問題。"},
                  {"role": "user", "content": prompt}]
    )

    return response["choices"][0]["message"]["content"]

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
    user_message = event.message.text.strip()

    # 讓 ChatGPT 根據 Google Sheets 內容回答問題
    reply_message = ask_chatgpt(user_message)

    line_bot_api.reply_message(
        reply_token=event.reply_token,
        messages=[TextMessage(text=reply_message)]
    )

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    serve(app, host="0.0.0.0", port=port)

