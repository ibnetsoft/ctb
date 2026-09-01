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
        "uptime": int(time.time() - start_time),
        "auto_pause_enabled": config.auto_pause_enabled,
        "run_duration": config.run_duration,
        "pause_duration": config.pause_duration,
        "price_oscillation_enabled": config.price_oscillation_enabled,
        "price_min": config.price_min,
        "price_max": config.price_max,
        "price_step": config.price_step
    }

@app.post("/api/toggle")
async def toggle_bot():
    config.is_running = not config.is_running
    return {"is_running": config.is_running}

@app.post("/api/config")
async def update_config(data: dict):
    if "beta" in data: config.beta = float(data["beta"])
    if "interval" in data: config.interval = int(data["interval"])
    if "target_price" in data:
        config.target_price = float(data["target_price"])
        bot.target_mid = config.target_price
    if "price_oscillation_enabled" in data:
        config.price_oscillation_enabled = bool(data["price_oscillation_enabled"])
    if "price_min" in data:
        config.price_min = float(data["price_min"])
    if "price_max" in data:
        config.price_max = float(data["price_max"])
    if "price_step" in data:
        config.price_step = float(data["price_step"])
    if "auto_pause_enabled" in data:
        config.auto_pause_enabled = bool(data["auto_pause_enabled"])
    if "run_duration" in data:
        config.run_duration = int(data["run_duration"])
    if "pause_duration" in data:
        config.pause_duration = int(data["pause_duration"])
    config.save_config()
    return {"status": "success"}

@app.get("/api/settings")
async def get_settings():
    return {
        "api_key": config.api_key[:4] + "*" * (len(config.api_key)-8) + config.api_key[-4:] if config.api_key else "",
        "secret_key": config.secret_key[:4] + "*" * (len(config.secret_key)-8) + config.secret_key[-4:] if config.secret_key else "",
        "telegram_token": config.telegram_token[:4] + "*" * (len(config.telegram_token)-8) + config.telegram_token[-4:] if config.telegram_token else "",
        "telegram_chat_id": config.telegram_chat_id,
        "telegram_enabled": config.telegram_enabled,
        "target_price": config.target_price,
        "price_oscillation_enabled": config.price_oscillation_enabled,
        "price_min": config.price_min,
        "price_max": config.price_max,
        "price_step": config.price_step,
        "auto_pause_enabled": config.auto_pause_enabled,
        "run_duration": config.run_duration,
        "pause_duration": config.pause_duration
    }

@app.post("/api/settings")
async def save_settings(data: dict):
    # 실제 키가 입력된 경우만 업데이트 (마스킹된 값 무시)
    if "api_key" in data and "*" not in data["api_key"]: config.api_key = data["api_key"]
    if "secret_key" in data and "*" not in data["secret_key"]: config.secret_key = data["secret_key"]
    if "telegram_token" in data and "*" not in data["telegram_token"]: config.telegram_token = data["telegram_token"]
    if "telegram_chat_id" in data: config.telegram_chat_id = data["telegram_chat_id"]
    if "telegram_enabled" in data: config.telegram_enabled = data["telegram_enabled"]
    if "target_price" in data:
        config.target_price = float(data["target_price"])
        bot.target_mid = config.target_price
    if "price_oscillation_enabled" in data:
        config.price_oscillation_enabled = bool(data["price_oscillation_enabled"])
    if "price_min" in data:
        config.price_min = float(data["price_min"])
    if "price_max" in data:
        config.price_max = float(data["price_max"])
    if "price_step" in data:
        config.price_step = float(data["price_step"])
    if "auto_pause_enabled" in data:
        config.auto_pause_enabled = bool(data["auto_pause_enabled"])
    if "run_duration" in data:
        config.run_duration = int(data["run_duration"])
    if "pause_duration" in data:
        config.pause_duration = int(data["pause_duration"])
    
    config.save_config()
    return {"status": "success"}

@app.post("/api/test_telegram")
async def test_telegram():
    if not config.telegram_token or not config.telegram_chat_id:
        return {"status": "error", "message": "설정 정보가 부족합니다."}
    
    bot.send_telegram("🔔 테스트 메시지입니다. 연결이 확인되었습니다!")
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
    webbrowser.open("http://localhost:8050")

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
    uvicorn.run(app, host="0.0.0.0", port=8050)

