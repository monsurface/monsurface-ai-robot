import os
import openai
import sqlite3
import gspread
import requests
import pytz
from flask import Flask, request
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

# === Flask App ===
app = Flask(__name__)

# === 環境變數 ===
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")
LOCAL_FILE_PATH = "credentials.json"
DB_PATH = "materials.db"

# === 設定 LINE Messaging API (v3) ===
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === 說明文字 ===
instruction_text = """🍀瑰貝鈺AI建材小幫手☘️

1⃣⃣ 查詢建材資訊：「品牌 ABC 型號 123」或「ABC 123」
2⃣⃣ 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog
3⃣⃣ 技術資訊：https://portaly.cc/Monsurface/pages/technical
4⃣⃣ 傳送門：https://portaly.cc/Monsurface
"""

# === 下載 Dropbox 憑證與 DB ===
def download_file(url, local_path):
    r = requests.get(url)
    if r.status_code == 200:
        with open(local_path, "wb") as f:
            f.write(r.content)
        print(f"✅ 成功下載: {local_path}")
    else:
        print(f"❌ 下載失敗: {local_path}")

download_file(DROPBOX_URL, DB_PATH)
download_file(os.getenv("CREDENTIAL_URL"), LOCAL_FILE_PATH)

# === 授權 Google Sheets ===
credentials = Credentials.from_service_account_file(LOCAL_FILE_PATH, scopes=[
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(credentials)

# === 權限驗證 ===
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

# === 查詢資料庫 ===
def search_materials_from_db(keyword: str, limit: int = 5):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        print(f"🔍 正在搜尋關鍵字：{keyword}")
        cur.execute("""
            SELECT * FROM materials
            WHERE 品牌 LIKE ? OR 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ?
               OR 花色名稱 LIKE ? OR 表面處理 LIKE ? OR 說明 LIKE ?
            LIMIT ?
        """, (f"%{keyword}%%",)*7 + (limit,))
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        conn.close()
        return [dict(zip(columns, row)) for row in rows] if rows else None
    except Exception as e:
        print(f"❌ 資料庫查詢錯誤: {e}")
        return None

# === 串 GPT 回答 ===
def ask_chatgpt(user_question, matched_materials=None):
    prompt = f"你是建材專家，請用繁體中文條列式回答使用者問題：「{user_question}」\n\n"
    if matched_materials:
        prompt += "以下為查到的建材資料：\n"
        for m in matched_materials:
            for k, v in m.items():
                prompt += f"- {k}: {v}\n"
            prompt += "\n"
    else:
        prompt += instruction_text
    client = openai.Client(api_key=OPENAI_API_KEY)
    for model in ["gpt-3.5-turbo", "gpt-3.5-turbo-0125"]:
        try:
            res = client.chat.completions.create(model=model, messages=[
                {"role": "system", "content": "你是建材查詢小幫手"},
                {"role": "user", "content": prompt}
            ])
            return res.choices[0].message.content
        except:
            continue
    return "⚠️ 抱歉，目前無法取得建材資訊"

# === Webhook 接收點 ===
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"❌ Webhook 處理錯誤: {e}")
        return "Error", 400
    return "OK", 200

# === 處理訊息事件 ===
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()

    if not check_user_permission(user_id):
        reply = "❌ 您沒有查詢權限，請聯絡管理員"
    elif msg == "熱門主推":
        reply = "📌 熱門建材資訊：https://portaly.cc/Monsurface/pages/hot_catalog"
    elif msg == "技術資訊":
        reply = "🔧 技術資訊：https://portaly.cc/Monsurface/pages/technical"
    elif msg == "瑰貝鈺傳送門":
        reply = "🚪 傳送門：https://portaly.cc/Monsurface"
    else:
        result = search_materials_from_db(msg)
        reply = ask_chatgpt(msg, result)

    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        )
    )

# === 使用 waitress 啟動伺服器 ===
if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 正式啟動 (v3 + production mode)...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
