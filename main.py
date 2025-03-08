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

# ✅ 品牌名稱別名（用於更精準的匹配）
BRAND_ALIASES = {
    "富美家": ["富美家", "Formica"],
    "愛卡AICA-愛克板": ["愛卡", "AICA", "愛克板"],
    "鉅莊-樂維LAVI": ["鉅莊", "樂維", "LAVI"],
    "魔拉頓 Melatone": ["魔拉頓", "Melatone"],
}

# ✅ 下載 Google API 憑證
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

# ✅ 讀取 Google Sheets API 憑證
credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

def fuzzy_match_brand(user_input):
    """嘗試找到最接近的品牌名稱"""
    all_brand_names = list(BRAND_SHEETS.keys()) + [alias for aliases in BRAND_ALIASES.values() for alias in aliases]
    best_match, score = process.extractOne(user_input, all_brand_names)
    
    if score >= 70:
        # 找到對應的正式品牌名稱
        for brand, aliases in BRAND_ALIASES.items():
            if best_match in aliases:
                return brand
        return best_match  # 如果匹配的是原始名稱，直接返回
    return None

def get_sheets_data(brand):
    """根據品牌讀取對應的 Google Sheets 數據"""
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
            except Exception as e:
                print(f"❌ 讀取 {sheet_name} 分頁時發生錯誤：{e}")
                continue  

            if not data:
                print(f"⚠️ {sheet_name} 分頁是空的，跳過處理。")
                continue

            all_data[sheet_name] = data

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
    請提供完整的建材資訊，列點詳細回答，且全部使用繁體中文。
    如果問題與建材無關，請回答：
    「請提供您的建材品牌和型號以做查詢。」。
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

    brand = fuzzy_match_brand(user_message)
    reply_text = f"匹配到的品牌：{brand}" if brand else "⚠️ 未找到匹配品牌，請提供完整品牌名稱。"

    reply_message = ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)])
    line_bot_api.reply_message(reply_message)
