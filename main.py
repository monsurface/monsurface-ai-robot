import os
import openai
import sqlite3
import requests
import gspread
import pytz
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from mangum import Mangum
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.messaging.models import TextMessage

# === 初始化 App 與 LINE 設定 ===
app = FastAPI()
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
line_api = MessagingApi(ApiClient(configuration))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DROPBOX_URL = os.getenv("DROPBOX_URL")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

# === 檔案下載 ===
def download_file(url, filename):
    print(f"📥 準備下載：{filename}...")
    r = requests.get(url)
    if r.status_code == 200:
        with open(filename, "wb") as f:
            f.write(r.content)
        print(f"✅ 成功下載：{filename}")
    else:
        raise Exception(f"❌ 無法下載 {filename}，HTTP 狀態碼: {r.status_code}")

download_file(DROPBOX_URL, "credentials.json")
download_file(DROPBOX_DB_URL, "materials.db")

# === Google Sheet 授權 ===
credentials = Credentials.from_service_account_file("credentials.json", scopes=[
    "https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(credentials)

# === 權限檢查 ===
def check_user_permission(user_id):
    try:
        print(f"🔒 檢查權限 for: {user_id}")
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
        # 新使用者加入紀錄
        sheet.append_row([user_id, "否", 0, datetime.now(pytz.timezone("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")])
        return False
    except Exception as e:
        print(f"❌ 權限查詢錯誤: {e}")
        return False

# === 查詢資料庫 ===
def search_materials(keyword: str, limit: int = 5):
    try:
        conn = sqlite3.connect("materials.db")
        cur = conn.cursor()
        print(f"🔍 搜尋關鍵字：{keyword}")

        # 動態查詢每個資料表
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        print(f"📄 資料表列表：{tables}")

        results = []
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table} WHERE 品牌 LIKE ? OR 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ? OR 花色名稱 LIKE ? OR 表面處理 LIKE ? OR 說明 LIKE ? LIMIT ?", (f"%{keyword}%",)*7 + (limit,))
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description]
                for row in rows:
                    results.append(dict(zip(columns, row)))
            except Exception as e:
                print(f"⚠️ 資料表 {table} 查詢錯誤：{e}")
        conn.close()
        return results if results else None
    except Exception as e:
        print(f"❌ DB 查詢錯誤：{e}")
        return None

# === ChatGPT 回答 ===
def ask_chatgpt(question, matched_data=None):
    prompt = f"你是建材專家，請用繁體中文條列式回答：「{question}」\n\n"
    if matched_data:
        prompt += "查詢到的建材資料如下：\n"
        for m in matched_data:
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
                {"role": "user", "content": prompt},
            ])
            return res.choices[0].message.content
        except Exception as e:
            print(f"❌ GPT 呼叫錯誤（{model}）：{e}")
            continue
    return "⚠️ 抱歉，目前無法查詢建材資訊"

# === Instruction 預設文字 ===
instruction_text = """🍀瑰貝鈺AI建材小幫手☘️\n\n1️⃣ 查詢建材資訊：「品牌 ABC 型號 123」或「ABC 123」\n2️⃣ 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog\n3️⃣ 技術資訊：https://portaly.cc/Monsurface/pages/technical\n4️⃣ 傳送門：https://portaly.cc/Monsurface"""

# === LINE Webhook 接收 ===
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("x-line-signature")
    body = await request.body()
    print("📨 收到 LINE Webhook")
    try:
        handler.handle(body.decode("utf-8"), signature)
    except Exception as e:
        print(f"❌ Webhook 錯誤: {e}")
        raise HTTPException(status_code=400, detail="Webhook Error")
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    print(f"✅ 使用者 {user_id} 訊息：{msg}")

    if not check_user_permission(user_id):
        reply = "❌ 您尚未有權限查詢建材，請聯絡管理員。"
    elif msg == "熱門主推":
        reply = "📌 熱門建材：https://portaly.cc/Monsurface/pages/hot_catalog"
    elif msg == "技術資訊":
        reply = "🧰 技術資料：https://portaly.cc/Monsurface/pages/technical"
    elif msg == "瑰貝鈺傳送門":
        reply = "🚪 傳送門：https://portaly.cc/Monsurface"
    else:
        result = search_materials(msg)
        reply = ask_chatgpt(msg, result)

    try:
        line_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        ))
        print("📤 回覆成功")
    except Exception as e:
        print(f"❌ 回覆失敗：{e}")

# === Railway 部署 ===
handler = Mangum(app)
