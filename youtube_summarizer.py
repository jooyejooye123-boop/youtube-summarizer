import os
import requests
from flask import Flask, request, jsonify
import yt_dlp
from dotenv import load_dotenv

# .env 파일에 저장된 환경 변수(API 키, 토큰 등)를 읽어옵니다.
load_dotenv()

app = Flask(__name__)

# 환경 변수에서 안전하게 값 가져오기
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

COOKIE_PATH = "./cookies.txt"

def send_telegram(text):
    """지정된 텔레그램 채팅방으로 메시지를 보냅니다."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"텔레그램 메시지 전송 실패: {e}")

def get_last_telegram_message():
    """[백업용] Make.com에서 파라미터가 누락되었을 때 텔레그램 방에서 직접 링크를 파싱합니다."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        response = requests.get(url).json()
        if response.get("ok") and response.get("result"):
            last_msg = response["result"][-1]["message"]["text"]
            if "youtube.com" in last_msg or "youtu.be" in last_msg:
                return last_msg
    except Exception as e:
        print(f"텔레그램 메시지 직접 파싱 에러: {e}")
    return None

@app.route('/webhook', methods=['GET', 'POST'])
def handle_webhook():
    # 1. Make.com이 주소 뒤에 ?url={{1.message.text}} 형태로 넘겨준 값을 가장 먼저 읽습니다.
    video_url = request.args.get('url')
    
    # 2. 만약 파라미터가 비어있다면 백업 규칙으로 텔레그램 방 최신 메시지를 긁어옵니다.
    if not video_url:
        video_url = get_last_telegram_message()
    
    # 3. 주소가 끝까지 없다면 에러를 리턴합니다.
    if not video_url:
        return jsonify({"status": "fail", "message": "No valid YouTube URL provided"}), 400
        
    send_telegram("📱 핸드폰 웹훅 수신! 자막을 추출하고 요약을 시작합니다...")
    
    # yt-dlp 기본 옵션 설정 (쿠키 적용 및 자막만 다운로드)
    ydl_opts = {
        'cookiefile': COOKIE_PATH, 
        'skip_download': True, 
        'writesubtitles': True, 
        'writeautomaticsub': True, 
        'subtitlesformat': 'srt', 
        'outtmpl': 'temp_trans'
    }
    
    try:
        # 유튜브 자막 다운로드 실행
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            
        # 생성된 srt 자막 파일 찾기
        srt_path = [f for f in os.listdir('.') if f.startswith('temp_trans') and f.endswith('.srt')][0]
        
        with open(srt_path, 'r', encoding='utf-8') as f:
            transcript = f.read()
            
        # 제미나이 AI API 세팅
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        prompt = f"아래 유튜브 자막을 보고 중요한 핵심 키워드 5개를 뽑아주고, 전체 내용을 요약해서 가독성 좋게 설명해줘:\n\n{transcript}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        # 제미나이에게 요약 요청 쏘기
        response = requests.post(gemini_url, headers=headers, json=payload)
        summary_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        
        # 요약본 텔레그램으로 전송 후 임시 파일 삭제
        send_telegram(f"✨ 요약이 완료되었습니다!\n\n{summary_text}")
        os.remove(srt_path)
        return jsonify({"status": "success"})
        
    except Exception as e:
        send_telegram(f"❌ 요약 처리 중 에러 발생: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # 외부(Serveo)에서 들어오는 접속을 허용하기 위해 host를 0.0.0.0으로 엽니다.
    app.run(host='0.0.0.0', port=5000)
