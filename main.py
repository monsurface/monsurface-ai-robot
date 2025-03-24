from flask import Flask, request
import gspread
import requests
import openai
import os
import pytz
import sqlite3
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

# 說明訊息
instruction_text = """
❓請輸入建材相關問題，例如：
- HK-561 是什麼品牌？
- 有沒有摩拉頓的 KC 系列？
- 科定 KD-8888 有什麼顏色？
"""

# 環境設定
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

# 檔案路徑
LOCAL_FILE_PATH = "credentials.json"
LOCAL_DB_PATH = "materials.db"

# Flask App
app = Flask(__name__)

# 下載 Dropbox 憑證與資料庫
def download_file(url, path):
    r = requests.get(url)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        print(f"✅ 成功下載: {path}")
    else:
        print(f"❌ 下載失敗: {path}，狀態碼: {r.status_code}")

download_file(DROPBOX_URL, LOCAL_FILE_PATH)
download_file(DROPBOX_DB_URL, LOCAL_DB_PATH)

# 授權 Google Sheets
credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

# LINE Bot 設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ✅ 權限驗證
def check_user_permission(user_id):
    try:
        sheet = client.open_by_key(SECURITY_SHEET_ID).sheet1
        data = sheet.get_all_records()
        for idx, row in enumerate(data, start=2):
            if row["Line User ID"].strip() == user_id:
                if row["是否有權限"].strip() == "是":
                    count = int(row["使用次數"]) + 1
                    sheet.update_cell(idx, 3, count)
                    t = datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
                    sheet.update_cell(idx, 4, t)
                    return True
                return False
        sheet.append_row([user_id, "否", 0, datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")])
        return False
    except Exception as e:
        print(f"❌ 權限錯誤: {e}")
        return False

# ✅ 撈取所有建材資料
def load_all_materials():
    conn = sqlite3.connect("materials.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cur.fetchall()]
    all_data = []
    for table in tables:
        try:
            cur.execute(f"SELECT * FROM {table}")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            for row in rows:
                all_data.append(dict(zip(cols, row)))
        except Exception as e:
            print(f"⚠️ 無法讀取資料表 {table}: {e}")
    conn.close()
    return all_data

# ✅ GPT 查詢（智慧解析）
def ask_chatgpt(user_question, materials_data):
    prompt = f"""
你是一位台灣建材查詢小幫手，能讀懂使用者的建材問題，並從下列建材資料中挑出最相關的項目，清楚條列回答。

使用者問題如下：
「{user_question}」

以下是建材資料庫（每筆為一筆建材資訊）：
{materials_data}

請回答使用者的問題，如找不到對應項目，請回答：「{instruction_text}」
"""

    client = openai.Client(api_key=OPENAI_API_KEY)
    for model in ["gpt-3.5-turbo", "gpt-3.5-turbo-0125"]:
        try:
            res = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一位建材查詢專家"},
                    {"role": "user", "content": prompt}
                ]
            )
            return res.choices[0].message.content
        except Exception as e:
            print(f"⚠️ ChatGPT 回答錯誤: {e}")
            continue
    return "⚠️ 抱歉，目前無法取得建材資訊"

# ✅ LINE webhook callback
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"❌ webhook 錯誤: {e}")
        return "error", 400
    return "ok", 200

# ✅ 處理使用者訊息
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    print(f"✅ 使用者 {user_id} 訊息：{msg}")

    if not check_user_permission(user_id):
        reply = "❌ 您沒有查詢權限，請聯絡管理員"
    elif msg in ["熱門主推", "技術資訊", "瑰貝鈺傳送門"]:
        if msg == "熱門主推":
            reply = "📌 熱門建材：https://portaly.cc/Monsurface/pages/hot_catalog"
        elif msg == "技術資訊":
            reply = "🔧 技術資訊：https://portaly.cc/Monsurface/pages/technical"
        else:
            reply = "🌐 傳送門：https://portaly.cc/Monsurface"
    else:
        all_materials = load_all_materials()
        reply = ask_chatgpt(msg, all_materials)

    try:
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )
        print("📤 回覆成功")
    except Exception as e:
        print(f"❌ 回覆失敗: {e}")

# ✅ 主程式啟動
if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 啟動中（智慧資料版本）...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))