#!/data/data/com.termux/files/usr/bin/bash
# 봇 시작 - 화면이 꺼져도 죽지 않도록 wake-lock 을 건다
termux-wake-lock
echo "봇 시작됨 (화면 꺼져도 유지). 중지: Ctrl+C 후  termux-wake-unlock"
python youtube_summarizer.py
