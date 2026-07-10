#!/data/data/com.termux/files/usr/bin/bash
# 안드로이드 태블릿(Termux)에서 유튜브 스크립트 봇 준비 - 한 번만 실행
set -e
echo "== 태블릿 서버 설치 시작 =="
pkg update -y && pkg upgrade -y
pkg install -y python nodejs git
pip install -U "yt-dlp[default]"
termux-setup-storage || true
echo
echo "설치 완료!"
echo "다음 순서로 진행하세요:"
echo " 1) 이 폴더에 yt_script_extractor.py, yt_telegram_bot.py 를 둔다"
echo "    (git clone 으로 받았다면 이미 있음)"
echo " 2) 멤버십 계정에서 뽑은 cookies.txt 를 이 폴더에 둔다"
echo " 3) yt_telegram_bot.py 의 BOT_TOKEN 을 입력한다  (nano yt_telegram_bot.py)"
echo " 4) 봇 실행:  bash start.sh"
