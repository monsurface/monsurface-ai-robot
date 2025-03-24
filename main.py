import os
import openai
import sqlite3
import requests
import gspread
import pytz
from flask import Flask, request, abort
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ✅ 讀取環境變數
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
DROPBOX_CREDENTIAL_URL = os.getenv("DROPBOX_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")
DB_LOCAL_FILE = "materials.db"
CREDENTIAL_LOCAL_FILE = "credentials.json"

# ✅ instruction_text
instruction_text = """🍀瑰貝鈺AI建材小幫手☘️

1️⃣ 查詢建材資訊：「品牌 ABC 型號 123」或「ABC 123」
2️⃣ 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog
3️⃣ 技術資訊：https://portaly.cc/Monsurface/pages/technical
4️⃣ 傳送門：https://portaly.cc/Monsurface
"""

# ✅ 下載 Dropbox 的 credentials.json & materials.db
def download_file(url, save_path):
    try:
        r = requests.get(url)
        if r.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(r.content)
            print(f"✅ 成功下載：{save_path}")
    except Exception as e:
        print(f"❌ 下載失敗：{save_path}，錯誤：{e}")

download_file(DROPBOX_CREDENTIAL_URL, CREDENTIAL_LOCAL_FILE)
download_file(DROPBOX_DB_URL, DB_LOCAL_FILE)

# ✅ 授權 Google Sheet
credentials = Credentials.from_service_account_file(CREDENTIAL_LOCAL_FILE, scopes=[
    "https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
client = gspread.authorize(credentials)

# ✅ 權限檢查
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

# ✅ 查詢資料庫
def search_materials_from_db(keyword: str, limit: int = 5):
    try:
        conn = sqlite3.connect(DB_LOCAL_FILE)
        cur = conn.cursor()
        print(f"✅ 搜尋關鍵字：{keyword}")
        result = []
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            try:
                cur.execute(f"""
                    SELECT * FROM "{table}"
                    WHERE 品牌 LIKE ? OR 系列 LIKE ? OR 款式 LIKE ? OR 型號 LIKE ?
                          OR 花色名稱 LIKE ? OR 表面處理 LIKE ? OR 尺寸 LIKE ? OR 說明 LIKE ?
                """, (f"%{keyword}%",)*8)
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description]
                for row in rows:
                    result.append(dict(zip(columns, row)))
            except Exception as e:
                print(f"⚠️ 表格 {table} 錯誤：{e}")
        conn.close()
        return result if result else None
    except Exception as e:
        print(f"❌ 查詢錯誤: {e}")
        return None

# ✅ 呼叫 GPT
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

# ✅ Line Webhook 設定
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
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

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ✅ 啟動應用
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
