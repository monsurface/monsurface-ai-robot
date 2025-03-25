from flask import Flask, request
import gspread
import requests
import openai
import os
import pytz
import sqlite3
import pandas as pd
from datetime import datetime
from google.oauth2.service_account import Credentials
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

instruction_text = """
🍀瑰貝鈺AI建材小幫手服務指南☘️

1️⃣ 查詢建材資訊：
可直接輸入部分/完整型號或關鍵字

目前資料庫可查詢品牌：
Formica富美家、Lavi樂維、
Donacai多娜彩、萊適寶、松耐特、
AICA愛卡、Melatone摩拉頓、
科彰、吉祥、華旗、華槶、

2️⃣ 獲取熱門建材推薦：
請輸入「熱門主推」
或利用以下連結
https://portaly.cc/Monsurface/pages/hot_catalog
查看主打建材資訊。

3️⃣ 查詢技術資訊：
請輸入「技術資訊」
或利用以下連結
https://portaly.cc/Monsurface/pages/technical
查看建材品牌的技術資料。

4️⃣ 瑰貝鈺傳送門：
利用以下連結
https://portaly.cc/Monsurface
查看各品牌綜合資訊。
"""

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
DROPBOX_URL = os.getenv("DROPBOX_URL")
DROPBOX_DB_URL = os.getenv("DROPBOX_DB_URL")
SECURITY_SHEET_ID = os.getenv("SECURITY_SHEET_ID")

LOCAL_FILE_PATH = "credentials.json"
LOCAL_DB_PATH = "materials.db"

app = Flask(__name__)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

KNOWN_BRANDS = ['富美家', 'LAVI', '摩拉頓', '松華', 'AICA', '華旗', '華槶', 'GoodWare', 'KOCHANG']

def download_file(url, path):
    r = requests.get(url)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
        print(f"✅ 成功下載: {path}")
    else:
        print(f"❌ 下載失敗: {path}，狀態碼: {r.status_code}")

def check_user_permission(user_id):
    try:
        credentials = Credentials.from_service_account_file(
            LOCAL_FILE_PATH,
            scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(credentials)
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

def extract_intent_and_keywords(user_question):
    prompt = f"""
你是一位建材助理，請從使用者的問題中提取：
1. 查詢意圖（例如：查型號資訊、找品牌系列、比較顏色等）
2. 相關關鍵字（以字串陣列格式呈現）

請注意：
- 若出現「白色」、「黑色」、「灰色」、「木紋」、「亮白」、「亮灰」、「淺灰」、「深灰」、「霧面」、「亮面」等，請轉換為精簡查詢詞：白、黑、灰、木、亮、霧等
- 若出現品牌名稱的錯字（如「富美家」誤寫為「富美」、「富美佳」、「formoca」），請轉換為正確品牌名（富美家）
- 若品牌有綽號或簡寫（如「AICA」、「愛卡」），請轉換為資料庫中正式品牌名稱
- 請去除語助詞與無用字（如「我想找」、「是否有」、「請問」）

請用 JSON 格式回傳，如下：
{{
  "意圖": "...",
  "關鍵字": ["...", "..."]
}}

使用者問題如下：
「{user_question}」
"""
    client = openai.Client(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是建材意圖識別助手"},
                {"role": "user", "content": prompt}
            ]
        )
        result = res.choices[0].message.content.strip()
        return eval(result)
    except Exception as e:
        print(f"❌ 意圖擷取錯誤: {e}")
        return {"意圖": "未知", "關鍵字": []}

def search_summary_by_keywords(keywords):
    conn = sqlite3.connect(LOCAL_DB_PATH)
    cur = conn.cursor()

    # 每個關鍵字都要至少出現在三欄之一，使用 AND 交集查詢
    conditions = ["(" + " OR ".join([f"品牌 LIKE ?", f"型號 LIKE ?", f"花色 LIKE ?", f"摘要 LIKE ?"]) + ")" for _ in keywords]
    query = f"""
        SELECT 型號, 來源表 FROM materials_summary
        WHERE {' AND '.join(conditions)}
        LIMIT 5
    """
    values = []
    for kw in keywords:
        values.extend([f"%{kw}%"] * 4)

    cur.execute(query, values)
    rows = cur.fetchall()
    conn.close()
    return rows

def lookup_full_materials(models_and_tables):
    conn = sqlite3.connect(LOCAL_DB_PATH)
    results = []
    for 型號, 來源表 in models_and_tables:
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{來源表}" WHERE 型號 = ?', conn, params=(型號,))
            for _, row in df.iterrows():
                results.append(dict(row))
        except Exception as e:
            print(f"⚠️ 無法查詢 {來源表} 的 {型號}: {e}")
    conn.close()
    return results

def generate_response(user_question, matched_materials):
    prompt = f"""
你是一位專業建材助理，請根據使用者的問題與下方建材資料，條列出所有符合的建材型號完整資訊。
使用者問題：
「{user_question}」
建材資料如下（每筆為一個建材）：
{matched_materials}
請逐筆條列說明，若找不到任何資料，請回答：
「{instruction_text}」
"""
    client = openai.Client(api_key=OPENAI_API_KEY)
    try:
        res = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是建材說明專家"},
                {"role": "user", "content": prompt}
            ]
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ 回覆產生錯誤: {e}")
        return "⚠️ 資訊量太大，請限縮單一型號或關鍵字"

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
    elif msg in ["熱門主推", "技術資訊", "瑰貝鈺傳送門"]:
        if msg == "熱門主推":
            reply = "📌 熱門建材：https://portaly.cc/Monsurface/pages/hot_catalog"
        elif msg == "技術資訊":
            reply = "🔧 技術資訊：https://portaly.cc/Monsurface/pages/technical"
        else:
            reply = "🌐 傳送門：https://portaly.cc/Monsurface"
    else:
        parsed = extract_intent_and_keywords(msg)
        keywords = parsed.get("關鍵字", [])
        if not keywords:
            reply = instruction_text
        else:
            model_refs = search_summary_by_keywords(keywords)
            if not model_refs:
                reply = instruction_text
            else:
                full_data = lookup_full_materials(model_refs)
                reply = generate_response(msg, full_data)

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

if __name__ == "__main__":
    download_file(DROPBOX_URL, LOCAL_FILE_PATH)
    download_file(DROPBOX_DB_URL, LOCAL_DB_PATH)
    print("🚀 LINE Bot 啟動中（智慧摘要查詢版本）...")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
