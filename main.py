from flask import Flask, request
import gspread
import requests
import openai
import os
import datetime
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
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")  # 🔹 權限表 Google Sheets ID

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

def check_user_permission(user_id, user_name):
    """檢查 Google Sheets 權限"""
    try:
        sheet = client.open_by_key(os.getenv("SECURITY_SHEET_ID")).sheet1
        data = sheet.get_all_records()
        
        for row in data:
            if row["Line User ID"].strip() == user_id:
                print(f"🔍 找到使用者 {user_name}，權限：{row['是否有權限']}")
                return row["是否有權限"].strip() == "是"
        
        # 如果找不到，新增該使用者，並預設為「否」
        sheet.append_row([user_id, user_name, 1, "無", "否"])
        print(f"⚠️ 新增使用者 {user_name} 至權限表，預設無權限")
        return False
    
    except Exception as e:
        print(f"❌ 讀取權限表錯誤：{e}")
        return False

def ask_chatgpt(user_question, formatted_text):
    """🔹 ChatGPT AI 回答"""
    prompt = f"""
    你是一位建材專家，以下是最新的建材資料庫：
    {formatted_text}

    用戶的問題是：「{user_question}」
    用戶會提供類似品牌abc 型號123的資訊，
    或是abc 123的資訊，abc是品牌，123是資訊，
    根據用戶提供的品牌和型號，
    提供完整的建材資訊，列點詳細回答，並全部使用繁體中文。
    如果問題與建材無關，請回答：
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
    except Exception:
        return "Error", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """📩 處理 LINE 使用者輸入"""
    user_message = " ".join(event.message.text.strip().split())
    user_id = event.source.user_id
    user_name = event.source.type

    reply_token = event.reply_token  

    if not check_user_permission(user_id, user_name):
        reply_text = "⚠️ 你沒有查詢權限，請聯絡管理員開通權限。"
    else:
        matched_brand = fuzzy_match_brand(user_message)

        if matched_brand:
            sheet_data = get_sheets_data(matched_brand)
            if sheet_data:
                formatted_text = "\n".join(f"{key}: {value}" for key, value in sheet_data.items())
                reply_text = ask_chatgpt(user_message.replace(" ", ""), formatted_text)
            else:
                reply_text = f"⚠️ 無法找到 **{matched_brand}** 的建材資訊。"
        else:
            reply_text = "⚠️ 請提供品牌名稱，例如：『富美家 8874NM』，才能查詢建材資訊。"

    line_bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=reply_text)]))

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    print("🚀 LINE Bot 伺服器啟動中...")
    serve(app, host="0.0.0.0", port=port)