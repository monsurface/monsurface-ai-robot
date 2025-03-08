from flask import Flask, request
import gspread
import requests
from google.oauth2.service_account import Credentials
import openai
import os
from fuzzywuzzy import process
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

# ✅ 設定品牌對應的 Google Sheet ID
BRAND_SHEETS = {
    "富美家": os.getenv("SPREADSHEET_ID_A"),
    "新日綠建材": os.getenv("SPREADSHEET_ID_B"),
    "鉅莊-樂維LAVI": os.getenv("SPREADSHEET_ID_C"),
    "愛卡AICA-愛克板": os.getenv("SPREADSHEET_ID_D"),
    "松華-松耐特": os.getenv("SPREADSHEET_ID_E"),
    "吉祥": os.getenv("SPREADSHEET_ID_F"),
    "華旗": os.getenv("SPREADSHEET_ID_G"),
    "科彰": os.getenv("SPREADSHEET_ID_H"),
    "華槶線板": os.getenv("SPREADSHEET_ID_I"),
    "魔拉頓": os.getenv("SPREADSHEET_ID_J"),
    "利明礦石軟片": os.getenv("SPREADSHEET_ID_K"),
    "熱門主推": os.getenv("SPREADSHEET_ID_L"),
}

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

# ✅ 讀取特定品牌的 Google Sheet 數據
def get_brand_sheet_data(brand):
    """根據品牌名稱讀取對應的 Google Sheet 數據"""
    sheet_id = BRAND_SHEETS.get(brand)

    if not sheet_id:
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            sheet_name = sheet.title  
            print(f"📂 讀取分頁：{sheet_name}")

            try:
                data = sheet.get_all_records(expected_headers=[])  
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁錯誤：{e}")
                continue  

            if not data:
                print(f"⚠️ 警告：{sheet_name} 分頁是空的，跳過處理。")
                continue

            all_data[sheet_name] = data

        if not all_data:
            print("❌ 錯誤：該品牌的 Google Sheets 沒有可用數據！")
            return None

        print("✅ 品牌 Google Sheets 讀取完成！")
        return all_data

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗，錯誤原因：{e}")
        return None

# ✅ 設定 OpenAI API
openai.api_key = OPENAI_API_KEY
import openai

def fuzzy_match_brand(user_input):
    """嘗試找到最接近的品牌名稱"""
    brand_match, score = process.extractOne(user_input, BRAND_SHEETS.keys())
    return brand_match if score >= 80 else None  

def is_relevant_question(user_question):
    """讓 ChatGPT 判斷問題是否與建材相關"""
    prompt = f"""
    以下是使用者的問題：「{user_question}」
    這個問題是否與建材、品牌、型號、花色或技術文件相關？請回答「是」或「否」。
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0125",
        messages=[{"role": "system", "content": "你是一位建材專家，請判斷問題是否與建材相關。"},
                  {"role": "user", "content": prompt}]
    )

    return "是" in response["choices"][0]["message"]["content"]

def ask_chatgpt(user_question, formatted_text):
    """讓 ChatGPT 讀取 Google Sheets 內容並條列式回答用戶問題"""

    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}

    用戶的問題是：「{user_question}」
    請根據建材資料提供的型號，完整詳細列點，且全部使用繁體中文。
    如果問題與建材無關，請回答：「這個問題與建材無關，我無法解答。」。
    """

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0125",
        messages=[{"role": "system", "content": "你是一位建材專家，專門回答與建材相關的問題。"},
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
    return "✅ LINE Bot 啟動成功！"

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

    if not is_relevant_question(user_message):
        reply_text = "🚀 這裡可以幫助您查詢：\n🏠 品牌 + 型號資訊\n🎨 型號花色\n🔍 相近花色\n📄 技術文件\n\n請問您想查詢哪一項？"
    else:
        brand_found = fuzzy_match_brand(user_message)
        reply_text = f"請提供品牌名稱，例如：「富美家」，才能查詢資料。" if not brand_found else ask_chatgpt(user_message, "資料庫內容")

    reply_message = ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])
    line_bot_api.reply_message(reply_message)

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

