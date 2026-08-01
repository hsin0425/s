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
pending_actions = {}  # 記錄每個使用者「正在等待選擇要刪哪筆」的候選清單

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
    current_year = datetime.now().year

    prompt = f"""
    今天是 {today_str}。使用者輸入的格式通常是「日期 時間 事件」，例如：
    8/5 14:00 拿包裹
    或用「第幾節」表示時間，例如：
    5/10 第3節 機動學
    第1節: 08:10-09:00, 第2節: 09:10-10:00, 第3節: 10:10-11:00, 第4節: 11:10-12:00
    第5節: 13:10-14:00, 第6節: 14:10-15:00, 第7節: 15:10-16:00, 第8節: 16:10-17:00

    如果使用者沒寫年份，預設是 {current_year} 年。

    請判斷使用者是「新增行程 (add)」、「查詢行程 (query)」還是「刪除行程 (delete)」。
    刪除的輸入範例：刪除8/5、刪除8/5拿包裹、取消8/5的行程

    請嚴格只輸出以下三種 JSON 格式之一，不要加任何 Markdown 標記：

    新增範例：
    {{"action": "add", "summary": "拿包裹", "start_time": "2026-08-05T14:00:00+08:00", "end_time": "2026-08-05T15:00:00+08:00"}}

    查詢範例：
    {{"action": "query", "target_date": "2026-08-05"}}

    刪除範例（keyword 沒有指定就填 null）：
    {{"action": "delete", "target_date": "2026-08-05", "keyword": "拿包裹"}}

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
def find_calendar_events(target_date_str, keyword=None):
    start_time = f"{target_date_str}T00:00:00Z"
    end_time = f"{target_date_str}T23:59:59Z"

    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID, timeMin=start_time, timeMax=end_time,
        singleEvents=True, orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if keyword:
        events = [e for e in events if keyword in e.get('summary', '')]
    return events

def delete_calendar_event(event_id):
    calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    
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
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()

    try:
        # 如果這位使用者上一輪正在等待「選擇要刪哪一筆」
        if user_id in pending_actions:
            candidates = pending_actions[user_id]
            if user_msg in ["全部", "all"]:
                for ev in candidates:
                    delete_calendar_event(ev["id"])
                reply_text = f"已刪除全部 {len(candidates)} 筆行程！"
                del pending_actions[user_id]
            elif user_msg.isdigit() and 1 <= int(user_msg) <= len(candidates):
                ev = candidates[int(user_msg) - 1]
                delete_calendar_event(ev["id"])
                reply_text = f"已刪除：{ev['summary']}"
                del pending_actions[user_id]
            else:
                reply_text = "請回覆「全部」，或行程前面的數字編號喔！"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        parsed = parse_user_intent(user_msg)

        if parsed["action"] == "add":
            reply_text = add_calendar_event(parsed["summary"], parsed["start_time"], parsed["end_time"])

        elif parsed["action"] == "query":
            reply_text = query_calendar_events(parsed["target_date"])

        elif parsed["action"] == "delete":
            events = find_calendar_events(parsed["target_date"], parsed.get("keyword"))
            if not events:
                reply_text = f"{parsed['target_date']} 沒有找到符合的行程喔！"
            elif len(events) == 1:
                delete_calendar_event(events[0]["id"])
                reply_text = f"已刪除：{events[0].get('summary', '無標題')}"
            else:
                lines = [f"{parsed['target_date']} 有多筆行程，回覆數字刪除其中一筆，或回覆「全部」刪除全部："]
                candidates = []
                for i, ev in enumerate(events, 1):
                    start = ev["start"].get("dateTime", ev["start"].get("date"))
                    time_str = start[11:16] if "T" in start else "整天"
                    summary = ev.get("summary", "無標題")
                    lines.append(f"{i}. {time_str} {summary}")
                    candidates.append({"id": ev["id"], "summary": summary})
                pending_actions[user_id] = candidates
                reply_text = "\n".join(lines)

        else:
            reply_text = "抱歉，我聽不懂這個指令，請再試一次！"

    except Exception as e:
        reply_text = f"處理時發生錯誤：{str(e)}"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
