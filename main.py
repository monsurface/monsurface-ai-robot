import os
import sqlite3
import openai
import gspread
import requests
from fastapi import FastAPI, Request
from mangum import Mangum
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging.models import TextMessage
import pytz

# === 環境變數 ===
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
DROPBOX_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

# === 資料檔案名稱 ===
DB_FILE = "materials.db"
CREDENTIAL_FILE = "credentials.json"

# === FastAPI App ===
app = FastAPI()
handler = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
line_bot_api = MessagingApi(ApiClient(configuration))
asgi_handler = Mangum(app)  # for Railway 等 serverless

# === Instruction Text ===
instruction_text = """🍀瑰貝鈺AI建材小幫手☘️

1️⃣ 查詢建材資訊：「品牌 ABC 型號 123」或「ABC 123」
2️⃣ 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog
3️⃣ 技術資訊：https://portaly.cc/Monsurface/pages/technical
4️⃣ 傳送門：https://portaly.cc/Monsurface
"""

# === 下載工具 ===
def download_file(url, local_path):
    r = requests.get(url)
    if r.status_code == 200:
        with open(local_path, "wb") as f:
            f.write(r.content)
        print(f"✅ 成功下載: {local_path}")
    else:
        raise Exception(f"❌ 下載失敗: {url}")

download_file(DROPBOX_URL, CREDENTIAL_FILE)
download_file(DROPBOX_DB_URL, DB_FILE)

# === Google Sheets 驗證 ===
credentials = Credentials.from_service_account_file(
    CREDENTIAL_FILE,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

def check_user_permission(user_id):
    try:
        sheet = client.open_by_key(SECURITY_SHEET_ID).sheet1
        data = sheet.get_all_records()
        for idx, row in enumerate(data, start=2):
            if row["Line User ID"].strip() == user_id:
                if row["是否有權限"].strip() == "是":
                    count = int(row["使用次數"]) + 1
                    now = datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
                    sheet.update_cell(idx, 3, count)
                    sheet.update_cell(idx, 4, now)
                    return True
                return False
        # 不存在，建立新行
        sheet.append_row([user_id, "否", 0, datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")])
        return False
    except Exception as e:
        print(f"❌ 權限檢查錯誤: {e}")
        return False

# === 搜尋 DB 所有分頁 ===
def search_materials_from_all_tables(keyword: str, limit_per_table: int = 3):
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        results = []

        for table in tables:
            try:
                cur.execute(f"""
                    SELECT * FROM "{table}"
                    WHERE 品牌 LIKE ? OR 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ? OR 花色名稱 LIKE ? OR 表面處理 LIKE ? OR 說明 LIKE ?
                    LIMIT ?
                """, (f"%{keyword}%",)*7 + (limit_per_table,))
                rows = cur.fetchall()
                if rows:
                    columns = [desc[0] for desc in cur.description]
                    for row in rows:
                        results.append(dict(zip(columns, row)))
            except Exception as e:
                print(f"⚠️ 查詢資料表 {table} 失敗：{e}")
        conn.close()
        return results if results else None
    except Exception as e:
        print(f"❌ 全資料表查詢失敗: {e}")
        return None

# === GPT 回應 ===
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
            res = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是建材查詢小幫手"},
                    {"role": "user", "content": prompt}
                ]
            )
            return res.choices[0].message.content
        except:
            continue
    return "⚠️ 抱歉，目前無法取得建材資訊"

# === Webhook 入口 ===
@app.post("/callback")
async def callback(req: Request):
    signature = req.headers.get("x-line-signature")
    body = await req.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        return "Error", 400
    return "OK", 200

# === 處理訊息 ===
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
        result = search_materials_from_all_tables(msg)
        reply = ask_chatgpt(msg, result)

    line_bot_api.reply_message(ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[TextMessage(text=reply)]
    ))
