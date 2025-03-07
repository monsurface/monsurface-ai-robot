from flask import Flask, request
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging import TextMessage
import os

app = Flask(__name__)

# 設定 LINE API 金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise ValueError("請確保環境變數 LINE_CHANNEL_ACCESS_TOKEN 和 LINE_CHANNEL_SECRET 設定正確！")

# 初始化 LINE API
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=["GET"])
def home():
    return "Line Bot 啟動成功！"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    print(f"📥 收到 Webhook 請求: {body}")  # Debug 記錄請求內容

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"❌ Webhook 處理錯誤: {e}")
        return "Error", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    reply_token = event.reply_token

    print(f"📥 收到訊息: {user_message}")
    print(f"🔑 Reply Token: {reply_token}")

    if not reply_token:
        print("⚠️ 錯誤: `reply_token` 為空，無法回覆訊息")
        return

    if not user_message:
        print("⚠️ 錯誤: 使用者訊息為空")
        return

    # 確保 `messages` 參數格式正確
    reply_message = [TextMessage(text=f"你說了：{user_message}")]

    try:
        line_bot_api.reply_message(
            reply_token=reply_token,
            messages=reply_message  # ✅ 確保 `messages` 是 `TextMessage` 物件的列表
        )
        print(f"✅ 成功回覆訊息: {reply_message[0].text}")

    except Exception as e:
        print(f"❌ 回覆訊息失敗: {e}")

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 啟動 Flask 伺服器，監聽 Port {port}")
    serve(app, host="0.0.0.0", port=port)
