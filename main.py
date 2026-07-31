import os
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# Google Calendar API 相關套件
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Groq API 相關套件
from groq import Groq

app = FastAPI()

# ---------------- 設定區（請替換成你的金鑰） ----------------
LINE_CHANNEL_SECRET = "e63b924743da67de49adca55357f23e1"
LINE_CHANNEL_ACCESS_TOKEN = "aVvMaK1cAo0z45PG4Nv57VStzVuD7B3Sfo93g7NVUvCrrwjwM39vuoyUgGlo8wqwICT5Wo93ACLDDFDcb8+Vn3yOg6QyUpzMM9H3VYl4oK4bqONpBHX/l1r83H2RNLpM2tI5kSYr9V+xoTHTONurKgdB04t89/1O/w1cDnyilFU="
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
# LINE API 初始化
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Groq API 初始化
groq_client = Groq(api_key=GROQ_API_KEY)

# Google Calendar API 初始化
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'credentials.json'

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
calendar_service = build('calendar', 'v3', credentials=credentials)
# 如果是用次要日曆，這裡填入該日曆的 ID；預設用 'primary'（但需確定服務帳號已獲授權）
CALENDAR_ID = 'a42520626@gmail.com' 

# ---------------- 節次對照表 ----------------
PERIOD_TIMES = {
    1: ("08:10", "09:00"),
    2: ("09:10", "10:00"),
    3: ("10:10", "11:00"),
    4: ("11:10", "12:00"),
    5: ("13:10", "14:00"),
    6: ("14:10", "15:00"),
    7: ("15:10", "16:00"),
    8: ("16:10", "17:00"),
}

# ---------------- 核心邏輯：AI 解析 ----------------
def parse_user_intent(user_text: str):
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    prompt = f"""
    今天是 {today_str}。請分析使用者的輸入，判斷他是要「新增行程 (add)」還是「查詢行程 (query)」。
    如果使用者講到「第幾節」，請參考節次時間對照：
    第1節: 08:10-09:00, 第2節: 09:10-10:00, 第3節: 10:10-11:00, 第4節: 11:10-12:00
    第5節: 13:10-14:00, 第6節: 14:10-15:00, 第7節: 15:10-16:00, 第8節: 16:10-17:00

    請嚴格只輸出 JSON 格式，不要加任何 Markdown 標記，格式如下：
    新增範例：
    {{"action": "add", "summary": "機動學", "start_time": "2026-05-10T10:10:00+08:00", "end_time": "2026-05-10T11:00:00+08:00"}}
    
    查詢範例：
    {{"action": "query", "target_date": "2026-05-10"}}

    使用者輸入："{user_text}"
    """
    
    response = groq_client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": prompt}],
    temperature=0
)
    clean_text = response.choices[0].message.content.replace("```json", "").replace("```", "").strip()
    return json.loads(clean_text)

# ---------------- Google Calendar 操作 ----------------
def add_calendar_event(summary, start_iso, end_iso):
    event = {
        'summary': summary,
        'start': {'dateTime': start_iso, 'timeZone': 'Asia/Taipei'},
        'end': {'dateTime': end_iso, 'timeZone': 'Asia/Taipei'},
    }
    calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    return f"已為你登記：{summary}\n時間：{start_iso[5:16].replace('T', ' ')}"

def query_calendar_events(target_date_str):
    start_time = f"{target_date_str}T00:00:00Z"
    end_time = f"{target_date_str}T23:59:59Z"
    
    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID, timeMin=start_time, timeMax=end_time,
        singleEvents=True, orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])
    
    if not events:
        return f"{target_date_str} 目前沒有排任何行程，整天都有空喔！"
    
    reply = f"【{target_date_str} 行程預覽】\n"
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        time_str = start[11:16] if 'T' in start else "整天"
        reply += f"• {time_str} - {event.get('summary', '無標題')}\n"
    return reply

# ---------------- LINE Webhook 接收點 ----------------
@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        handler.handle(body_str, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    try:
        parsed = parse_user_intent(user_msg)
        if parsed["action"] == "add":
            reply_text = add_calendar_event(parsed["summary"], parsed["start_time"], parsed["end_time"])
        elif parsed["action"] == "query":
            reply_text = query_calendar_events(parsed["target_date"])
        else:
            reply_text = "抱歉，我聽不懂這個指令，請再試一次！"
    except Exception as e:
        reply_text = f"處理時發生錯誤：{str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
