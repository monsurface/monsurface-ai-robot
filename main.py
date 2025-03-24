import os
import sqlite3
import openai
import requests
import gspread
import pytz

from fastapi import FastAPI, Request
from mangum import Mangum
from datetime import datetime
from google.oauth2.service_account import Credentials

from linebot.v3.webhook import WebhookParser
from linebot.v3.messaging import MessagingApi, Configuration
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage, ReplyMessageRequest

# === FastAPI + Mangum ===
app = FastAPI()
handler = Mangum(app)

# === LINE SDK 設定 ===
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
parser = WebhookParser(os.getenv("LINE_CHANNEL_SECRET"))
line_api = MessagingApi(configuration)

# === Dropbox 檔案 ===
DROPBOX_CRED_URL = os.getenv("DROPBOX_URL")           # credentials.json
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")          # materials.db

# === 本地路徑 ===
LOCAL_CRED = "credentials.json"
LOCAL_DB = "materials.db"

instruction_text = """🍀瑰貝鈺AI建材小幫手☘️

1️⃣ 查詢建材資訊：「品牌 ABC 型號 123」或「ABC 123」
2️⃣ 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog
3️⃣ 技術資訊：https://portaly.cc/Monsurface/pages/technical
4️⃣ 傳送門：https://portaly.cc/Monsurface
"""

# === 檔案下載函式 ===
def download_file(url, local_filename):
    print(f"📥 準備下載: {local_filename}...")
    response = requests.get(url)
    if response.status_code == 200:
        with open(local_filename, "wb") as f:
            f.write(response.content)
        print(f"✅ 成功下載: {local_filename}")
    else:
        print(f"❌ 無法下載 {local_filename}, 狀態碼: {response.status_code}")

# 下載 credentials.json 與 materials.db
download_file(DROPBOX_CRED_URL, LOCAL_CRED)
download_file(DROPBOX_DB_URL, LOCAL_DB)

# === Google Sheets 驗證 ===
credentials = Credentials.from_service_account_file(LOCAL_CRED, scopes=[
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
])
client = gspread.authorize(credentials)
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

# === 權限檢查 ===
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
        # 新使用者加入
        sheet.append_row([user_id, "否", 0, datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")])
        return False
    except Exception as e:
        print(f"❌ 權限檢查錯誤: {e}")
        return False

# === 多資料表查詢 ===
def search_materials(keyword: str, limit: int = 5):
    try:
        conn = sqlite3.connect(LOCAL_DB)
        cursor = conn.cursor()

        print(f"🔍 正在搜尋關鍵字：{keyword}")
        matched = []
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            try:
                cursor.execute(f"""
                    SELECT * FROM {table}
                    WHERE 品牌 LIKE ? OR 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ?
                       OR 花色名稱 LIKE ? OR 表面處理 LIKE ? OR 尺寸 LIKE ? OR 說明 LIKE ?
                """, (f"%{keyword}%",)*8)
                rows = cursor.fetchall()
                if rows:
                    columns = [desc[0] for desc in cursor.description]
                    matched += [dict(zip(columns, row)) for row in rows]
                    if len(matched) >= limit:
                        break
            except Exception as e:
                print(f"⚠️ 查詢表 {table} 時發生錯誤：{e}")
        conn.close()
        return matched if matched else None
    except Exception as e:
        print(f"❌ 查詢錯誤：{e}")
        return None

# === GPT 回答 ===
def ask_chatgpt(user_question, matched_data=None):
    prompt = f"你是建材專家，請用繁體中文條列式回答使用者問題：「{user_question}」\n\n"
    if matched_data:
        prompt += "以下為查到的建材資料：\n"
        for m in matched_data:
            for k, v in m.items():
                prompt += f"- {k}: {v}\n"
            prompt += "\n"
    else:
        prompt += instruction_text

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    for model in ["gpt-3.5-turbo", "gpt-3.5-turbo-0125"]:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是建材查詢小幫手"},
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ GPT 呼叫失敗：{e}")
    return "⚠️ 抱歉，目前無法取得建材資訊"

# === LINE Webhook ===
@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    try:
        events = parser.parse(body.decode("utf-8"), signature)
    except Exception as e:
        print(f"❌ Webhook 錯誤：{e}")
        return "Invalid", 400

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            await handle_message(event)
    return "OK", 200

# === LINE 訊息處理 ===
async def handle_message(event: MessageEvent):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    print(f"✅ 使用者 {user_id} 訊息：{msg}")

    if not check_user_permission(user_id):
        reply = "❌ 您尚未有權限查詢建材，請聯絡管理員。"
    elif msg == "熱門主推":
        reply = "📌 熱門建材：https://portaly.cc/Monsurface/pages/hot_catalog"
    elif msg == "技術資訊":
        reply = "🔧 技術資料：https://portaly.cc/Monsurface/pages/technical"
    elif msg == "瑰貝鈺傳送門":
        reply = "🚪 傳送門：https://portaly.cc/Monsurface"
    else:
        result = search_materials(msg)
        reply = ask_chatgpt(msg, result)

    try:
        await line_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )
        print("📤 回覆成功")
    except Exception as e:
        print(f"❌ 回覆失敗：{e}")
