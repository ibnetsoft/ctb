from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import threading
import json
import os
import sys
import webbrowser
import time
from mmbot import MarketMaker, BotConfig

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

app = FastAPI()

# 글로벌 상태 공유
config = BotConfig()
bot = MarketMaker(config)
start_time = time.time()

# ── 바인딩 및 라우트 ──────────────────────────────
@app.get("/")
async def get_dashboard():
    path = get_resource_path("index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/status")
async def get_status():
    return {
        "is_running": config.is_running,
        "btc_price": bot.current_btc_p,
        "air_target": bot.target_mid,
        "vol_ratio": bot.vol_ratio,
        "usdt_bal": bot.usdt_bal,
        "air_bal": bot.air_bal,
        "last_status": bot.last_status,
        "beta": config.beta,
        "interval": config.interval,
        "uptime": int(time.time() - start_time)
    }

@app.post("/api/toggle")
async def toggle_bot():
    config.is_running = not config.is_running
    return {"is_running": config.is_running}

@app.post("/api/config")
async def update_config(data: dict):
    if "beta" in data: config.beta = float(data["beta"])
    if "interval" in data: config.interval = int(data["interval"])
    return {"status": "success"}

@app.get("/api/logs")
async def get_logs():
    return {"logs": config.logs[-50:]}

# ── 백그라운드 봇 가동 ───────────────────────────
def start_bot():
    bot.run_mm_loop()

def open_browser():
    """브라우저 자동 열기"""
    time.sleep(1.5) # 서버 부팅 대기
    webbrowser.open("http://localhost:8000")

@app.on_event("startup")
async def startup_event():
    # 봇을 별도의 스레드에서 실행
    thread = threading.Thread(target=start_bot, daemon=True)
    thread.start()
    
    # 브라우저 열기 스레드
    threading.Thread(target=open_browser, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    # 외부 접근 허용을 위해 0.0.0.0 권장하나, 로컬 전용이면 127.0.0.1도 가능
    uvicorn.run(app, host="0.0.0.0", port=8000)

