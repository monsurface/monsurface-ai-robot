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
    "愛卡AICA-愛克板": os.getenv("SPREADSHEET_ID_D"),
}

# ✅ 品牌名稱別名（增加匹配範圍）
BRAND_ALIASES = {
    "富美家": ["富美家", "Formica"],
    "愛卡AICA-愛克板": ["愛卡", "AICA", "愛克板"],
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
    print(f"⚠️ 未找到匹配的品牌")
    return None

def clean_model_number(user_input, brand):
    """🎯 清理型號，使其無視空格與格式錯誤"""
    brand_aliases = [brand] + BRAND_ALIASES.get(brand, [])
    for alias in brand_aliases:
        if user_input.startswith(alias):
            return user_input.replace(alias, "").strip().replace(" ", "")  
    return user_input.strip().replace(" ", "")

def get_sheets_data(brand, model_number, retry=True):
    """📊 讀取 Google Sheets 數據，若查不到則重試"""
    sheet_id = BRAND_SHEETS.get(brand)
    if not sheet_id:
        return None

    try:
        spreadsheet = client.open_by_key(sheet_id)
        all_data = {}

        for sheet in spreadsheet.worksheets():
            sheet_name = sheet.title
            raw_data = sheet.get_all_records(expected_headers=[])

            for row in raw_data:
                for key, value in row.items():
                    clean_key = key.strip().replace(" ", "")  
                    clean_value = str(value).strip().replace(" ", "")  
                    if clean_value == model_number:
                        all_data[sheet_name] = row  

        if not all_data and retry:
            print("🔄 嘗試重新讀取 Google Sheets 數據...")
            return get_sheets_data(brand, model_number, retry=False)  # **第二次查找**

        return all_data if all_data else None

    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗：{e}")
        return None

def ask_chatgpt(user_question, formatted_text):
    """🔹 ChatGPT AI 回答"""
    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}

    用戶的問題是：「{user_question}」
    請提供完整詳細的建材資訊，並使用條列式格式回答。
    """

    models_to_try = ["gpt-3.5-turbo", "gpt-3.5-turbo-0125", "gpt-3.5-turbo-16k"]
    openai.api_key = OPENAI_API_KEY  

    for model in models_to_try:
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=[{"role": "system", "content": "你是一位建材專家，專門回答與建材相關的問題。"},
                          {"role": "user", "content": prompt}]
            )

            if response and "choices" in response and response.choices:
                return response["choices"][0]["message"]["content"]

        except openai.error.OpenAIError:
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
    except Exception:
        return "Error", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """📩 處理 LINE 使用者輸入"""
    user_message = " ".join(event.message.text.strip().split())  
    reply_token = event.reply_token  

    matched_brand = fuzzy_match_brand(user_message)
    if not matched_brand:
        reply_text = "⚠️ 請提供品牌名稱，例如：『富美家 8874NM』。"
    else:
        model_number = clean_model_number(user_message, matched_brand)
        sheet_data = get_sheets_data(matched_brand, model_number)

        if sheet_data:
            formatted_text = "\n".join(f"{key}: {value}" for key, value in sheet_data.items())
            reply_text = ask_chatgpt(user_message, formatted_text)
        else:
            reply_text = f"⚠️ 無法找到 **{matched_brand} {model_number}**，請確認型號是否正確。"

    line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)]))
