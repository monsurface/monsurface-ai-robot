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

search_text = """
🔍 AI建材查詢方式：
1️⃣ 輸入型號：例如 8830
2️⃣ 輸入結合品牌與型號：例如 富美家的7378G
3️⃣ 輸入結合品牌與花色：例如 樂維的白色
4️⃣ 輸入相關要求(如無法顯示則無符合此要求的建材)：例如 給我有耐燃一級的板材 
因資料庫資料較多，建材查詢約需5-10秒，感謝您的耐心!
可查詢品牌：
Formica富美家、Lavi樂維、
Donacai多娜彩、萊適寶、松耐特、
AICA愛卡、Melatone摩拉頓、
科彰、吉祥、華旗、華槶
"""

instruction_text = """
🍀瑰貝鈺AI建材小幫手，請從下方選單開始您需要的服務☘️
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

def extract_brand_from_keywords(keywords):
    for kw in keywords:
        for brand in KNOWN_BRANDS:
            if brand in kw:
                return brand
    return None

def extract_intent_and_keywords(user_question):
    prompt = f"""
你是一位建材助理，請從使用者的問題中提取：
1. 使用者的查詢意圖（例如：查型號、找品牌系列、比較顏色等）
2. 查詢關鍵字（以關聯性為主，不用過度精簡，例如：「富美家亮白」可以包含「富美家」、「亮白」、「白色」）
3. 如果使用者的問題只對應到預設回覆（如「建材查詢」、「建材總表」、「熱門主推」、「技術資訊」、「傳送門」），則不需要解析，直接交由主程式處理即可。
請注意：
- 請將同一句話中具有關聯的詞組一併保留，例如：「富美家白色」、「富美家亮白」、「白色木紋」
- 如果使用者提到顏色，請保留原詞，也加入可能的組合詞（如：「白色」+「富美家」＝「富美家白色」）
- 使用者輸入「白色」也請同時提取「白」、「亮白」等可能相似詞
- 關鍵字可以重疊，例如同時保留「白色」、「亮白」、「富美家白色」等
- 請勿自行推論成資料庫裡存在的名稱，只需提取語意組合
- 請盡可能萃取多組關鍵字詞組，包括簡稱、部分詞、品牌搭配的完整詞
- 品牌輸入錯字（如富美加、富美佳）時，也請嘗試保留近似品牌詞
- 品牌名稱別名
  富美家 = Formica,
  愛卡AICA-愛克板 = 愛卡, AICA, 愛克板, 愛克,
  鉅莊-樂維LAVI = 鉅莊, 樂維, LAVI, 多娜彩, Donacai,
  松華-松耐特及系列品牌 = 松華, 松耐特, 萊適寶,
  魔拉頓 Melatone = 魔拉頓, Melatone, Magicor, 魔科板

請回傳 JSON 格式如下：
{{
  "意圖": "...",
  "關鍵字": ["...", "...", "..."]
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

def lookup_full_materials(models_and_tables):
    conn = sqlite3.connect(LOCAL_DB_PATH)
    results = []
    for 型號, 來源表 in models_and_tables:
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{來源表}" WHERE 型號 = ?', conn, params=(型號,))
            if df.empty:
                # fallback: try LIKE
                df = pd.read_sql_query(f'SELECT * FROM "{來源表}" WHERE 型號 LIKE ?', conn, params=(f"%{型號}%",))
                if df.empty:
                    print(f"⚠️ 查詢失敗：找不到型號 {型號} 於資料表 {來源表}")
                else:
                    print(f"✅ 模糊查詢成功：{型號} → {len(df)} 筆於 {來源表}")
            else:
                print(f"✅ 精準查詢成功：{型號} 於 {來源表}")

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

    elif msg in ["AI建材查詢", "查建材", "查詢建材"]:
        reply = search_text

    elif msg in ["建材總表"]:
        reply = "🗄️ 建材總表：https://reurl.cc/1K2vGY"

    elif msg in ["熱門主推"]:
        reply = "📌 熱門主推：https://portaly.cc/Monsurface/pages/hot_catalog"

    elif msg in ["技術資訊"]:
        reply = "🔧 技術資訊：https://portaly.cc/Monsurface/pages/technical"

    elif msg in ["傳送門", "瑰貝鈺傳送門"]:
        reply = "🌐 傳送門：https://portaly.cc/Monsurface"
        
    else:
        parsed = extract_intent_and_keywords(msg)
        keywords = parsed.get("關鍵字", [])
        if not keywords:
            reply = instruction_text
        
        else:
            # fallback 查詢摘要表
            conn = sqlite3.connect(LOCAL_DB_PATH)
            cur = conn.cursor()
            conditions = ["摘要 LIKE ? OR 型號 LIKE ? OR 花色 LIKE ?" for _ in keywords]
            query = f"SELECT 型號, 來源表 FROM materials_summary WHERE {' AND '.join(['(' + c + ')' for c in conditions])} LIMIT 5"
            values = []
            for kw in keywords:
                values.extend([f"%{kw}%"] * 3)
            cur.execute(query, values)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                print("⚠️ 查無任何符合條件的型號")
                reply = instruction_text
            else:
                full_data = lookup_full_materials(rows)
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
    print("🚀 LINE Bot 啟動中（智慧摘要查詢版本 + fallback log）...")
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
