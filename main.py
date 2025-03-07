from flask import Flask, request
import os
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.messaging.models import TextMessage

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
    user_message = event.message.text.strip()
    reply_token = event.reply_token  # 取得 reply_token

    print(f"📩 收到訊息：{user_message}")
    print(f"🔑 Reply Token: {reply_token}")

    if not reply_token:
        print("⚠️ 錯誤：`reply_token` 為空，無法回覆訊息")
        return

    if not user_message:
        print("⚠️ 錯誤：使用者訊息為空")
        return

    # ✅ **使用 `ReplyMessageRequest` 來構建正確的回覆格式**
    reply_message = ReplyMessageRequest(
        reply_token=reply_token,
        messages=[TextMessage(text=f"✅ 你說了：{user_message}")]
    )

    try:
        line_bot_api.reply_message(reply_message)
        print(f"✅ 成功回應 LINE 訊息：「{user_message}」")

    except Exception as e:
        print(f"❌ LINE Bot 回覆錯誤: {e}")

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))
    serve(app, host="0.0.0.0", port=port)
