from flask import Flask, request
import os
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, TextMessage
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

# ✅ 讀取環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# ✅ 設定 LINE Bot
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/", methods=["GET"])
def home():
    return "✅ LINE Bot 啟動成功！"

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"❌ Webhook Error: {e}")
        return "Error", 400

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    try:
        # **強制確保回應格式為 `TextMessage`**
        user_message = event.message.text.strip()
        reply_message = f"✅ LINE BOT 測試成功！你的訊息是：「{user_message}」"
        text_message = TextMessage(text=reply_message)  # 確保是 TextMessage 物件

        line_bot_api.reply_message(
            reply_token=event.reply_token,
            messages=[text_message]  # 必須是 `TextMessage`
        )

        print(f"✅ 成功回應 LINE 訊息：「{reply_message}」")

    except Exception as e:
        print(f"❌ LINE Bot 回覆錯誤: {e}")

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    serve(app, host="0.0.0.0", port=port)
