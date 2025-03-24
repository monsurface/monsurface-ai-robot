from flask import Flask, request
import gspread
import requests
import openai
import os
import pytz
from datetime import datetime
from google.oauth2.service_account import Credentials
from rapidfuzz import process
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage
import re

instruction_text = """
🍀瑠貝鏱AI建材小幫手服務指南☘️

1⃣f3f0 查詢建材資訊：
請輸入品牌與型號，
例如：「品牌 ABC 型號 123」，
或「ABC 123」皆可。

可查詢品牌：
Formica富美家、Lavi樂維、
Donacai多娜彩、萊適寶、松耐特、
AICA愛卡、Melatone摩拉頓、
科彰、吉祥、華旗、華槶、
KEDING科定

2⃣f3f0 獲取熱門建材推薦：
請輸入「熱門主推」
或利用以下連結
https://portaly.cc/Monsurface/pages/hot_catalog
查看主打建材資訊。

3⃣f3f0 查詢技術資訊：
請輸入「技術資訊」
或利用以下連結
https://portaly.cc/Monsurface/pages/technical
查看建材品牌的技術資料。

4⃣f3f0 瑰貝鈺傳送門：
利用以下連結
https://portaly.cc/Monsurface
查看各品牌綜合資訊。

"""

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

LOCAL_FILE_PATH = "credentials.json"
LOCAL_DB_PATH = "materials.db"

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

credentials = Credentials.from_service_account_file(
    LOCAL_FILE_PATH,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)
client = gspread.authorize(credentials)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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

import sqlite3

def search_materials(keyword: str, limit: int = 5):
    try:
        conn = sqlite3.connect("materials.db")
        cur = conn.cursor()

        # 取得所有資料表名稱
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]

        results = []
        for table in tables:
            try:
                cur.execute(f"""
                    SELECT * FROM {table}
                    WHERE 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ? OR 花色名稱 LIKE ?
                       OR 表面處理 LIKE ? OR 說明 LIKE ? OR 品牌 LIKE ?
                    LIMIT ?
                """, (f"%{keyword}%",)*7 + (limit,))
                rows = cur.fetchall()
                if rows:
                    columns = [desc[0] for desc in cur.description]
                    results.extend([dict(zip(columns, row)) for row in rows])
            except Exception as e:
                print(f"⚠️ 查詢 {table} 發生錯誤: {e}")
                continue
        conn.close()
        return results if results else None
    except Exception as e:
        print(f"❌ 資料庫查詢錯誤: {e}")
        return None

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

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    print(f"✅ 使用者 {user_id} 訊息：{msg}")

    if not check_user_permission(user_id):
        reply = "❌ 您沒有查詢權限，請聯絡管理員"
    elif msg == "熱門主推":
        reply = "📌 熱門建材資訊：https://portaly.cc/Monsurface/pages/hot_catalog"
    elif msg == "技術資訊":
        reply = "🔧 技術資訊：https://portaly.cc/Monsurface/pages/technical"
    elif msg == "瑰貝鈺傳送門":
        reply = "🚪 傳送門：https://portaly.cc/Monsurface"
    else:
        result = search_materials(msg)
        reply = ask_chatgpt(msg, result)

    try:
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        ))
        print("📤 回覆成功")
    except Exception as e:
        print(f"❌ 回覆失敗: {e}")

if __name__ == "__main__":
    from waitress import serve
    print("🚀 LINE Bot 啟動中 (穩定版)...")
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
