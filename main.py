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
        "exchange": config.exchange,
        "symbol": config.symbol,
        "base_asset": bot.get_base_asset_name(),
        "is_running": config.is_running,
        "btc_price": bot.current_btc_p,
        "air_target": bot.target_mid,
        "vol_ratio": bot.vol_ratio,
        "usdt_bal": bot.usdt_bal,
        "usdt_total": getattr(bot, "usdt_total", bot.usdt_bal),
        "usdt_freeze": getattr(bot, "usdt_freeze", 0.0),
        "air_bal": bot.air_bal,
        "air_total": getattr(bot, "air_total", bot.air_bal),
        "air_freeze": getattr(bot, "air_freeze", 0.0),
        "last_status": bot.last_status,
        "beta": config.beta,
        "interval": config.interval,
        "uptime": int(time.time() - start_time),
        "auto_pause_enabled": config.auto_pause_enabled,
        "run_duration": config.run_duration,
        "pause_duration": config.pause_duration,
        "micro_range_enabled": config.micro_range_enabled,
        "micro_gap": config.micro_gap,
        "micro_width": config.micro_width,
        "price_oscillation_enabled": config.price_oscillation_enabled,
        "price_min": config.price_min,
        "price_max": config.price_max,
        "price_step": config.price_step
    }

@app.post("/api/toggle")
async def toggle_bot():
    config.is_running = not config.is_running
    return {"is_running": config.is_running}

@app.post("/api/manual_sweep")
async def manual_sweep(data: dict = {}):
    price = float(data.get("price", 0)) if data.get("price") else None
    amount_usdt = float(data.get("amount_usdt", 15.0))
    res = bot.execute_manual_sweep(sweep_price=price, usdt_amount=amount_usdt)
    return res

@app.post("/api/cancel_all")
async def cancel_all():
    count = bot.cancel_all_orders(config.symbol)
    balances = bot.get_balances()
    if balances:
        base_asset = bot.get_base_asset_name()
        bot.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
        bot.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
    return {"status": "success", "cancelled_count": count, "usdt_bal": bot.usdt_bal, "air_bal": bot.air_bal}

@app.post("/api/generate_grid")
async def generate_grid(data: dict = {}):
    center_price = float(data.get("center_price")) if data.get("center_price") else 540.5
    levels = int(data.get("levels", 12))
    step = float(data.get("step", 0.04))
    amount = float(data.get("amount", 0.02))
    cancel_existing = bool(data.get("cancel_existing", True))
    min_price = float(data.get("min_price", 540.0))
    max_price = float(data.get("max_price", 541.0))
    organic = bool(data.get("organic", True))
    
    res = bot.generate_orderbook_grid(
        center_price=center_price,
        levels=levels,
        step=step,
        amount_per_order=amount,
        cancel_existing=cancel_existing,
        min_price=min_price,
        max_price=max_price,
        organic=organic
    )
    return res

@app.get("/api/orderbook")
async def get_orderbook():
    ob = bot.get_orderbook(config.symbol)
    raw_bids = ob.get("bids", [])
    raw_asks = ob.get("asks", [])
    bids = sorted(raw_bids, key=lambda x: float(x[0]), reverse=True) if raw_bids else []
    asks = sorted(raw_asks, key=lambda x: float(x[0]), reverse=False) if raw_asks else []
    
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    spread = max(0.0, best_ask - best_bid) if (bids and asks) else 0.0
    margin = max(0.00000002, spread * 0.04)
    min_sweep = round(best_bid + margin, 4)
    max_sweep = round(best_ask - margin, 4)
    if config.price_min > 0 and config.price_max > 0:
        min_sweep = max(min_sweep, round(config.price_min + 0.002, 4))
        max_sweep = min(max_sweep, round(config.price_max - 0.002, 4))
    mid_sweep = round((best_bid + best_ask) / 2.0, 4)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "min_sweep": min_sweep,
        "max_sweep": max_sweep,
        "mid_sweep": mid_sweep,
        "bids": bids[:12],
        "asks": asks[:12]
    }

@app.post("/api/config")
async def update_config(data: dict):
    if "exchange" in data: config.exchange = str(data["exchange"]).lower()
    if "symbol" in data: config.symbol = str(data["symbol"])
    if "beta" in data: config.beta = float(data["beta"])
    if "interval" in data: config.interval = int(data["interval"])
    if "target_price" in data:
        config.target_price = float(data["target_price"])
        bot.target_mid = config.target_price
    if "micro_range_enabled" in data:
        config.micro_range_enabled = bool(data["micro_range_enabled"])
    if "micro_gap" in data:
        config.micro_gap = float(data["micro_gap"])
    if "micro_width" in data:
        config.micro_width = float(data["micro_width"])
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
    bot.init_client()
    return {"status": "success"}

@app.get("/api/settings")
async def get_settings():
    return {
        "exchange": config.exchange,
        "symbol": config.symbol,
        "api_key": config.api_key[:4] + "*" * (len(config.api_key)-8) + config.api_key[-4:] if config.api_key else "",
        "secret_key": config.secret_key[:4] + "*" * (len(config.secret_key)-8) + config.secret_key[-4:] if config.secret_key else "",
        "telegram_token": config.telegram_token[:4] + "*" * (len(config.telegram_token)-8) + config.telegram_token[-4:] if config.telegram_token else "",
        "telegram_chat_id": config.telegram_chat_id,
        "telegram_enabled": config.telegram_enabled,
        "target_price": config.target_price,
        "micro_range_enabled": config.micro_range_enabled,
        "micro_gap": config.micro_gap,
        "micro_width": config.micro_width,
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
    if "exchange" in data: config.exchange = str(data["exchange"]).lower()
    if "symbol" in data: config.symbol = str(data["symbol"])
    if "api_key" in data and "*" not in data["api_key"]: config.api_key = data["api_key"]
    if "secret_key" in data and "*" not in data["secret_key"]: config.secret_key = data["secret_key"]
    if "telegram_token" in data and "*" not in data["telegram_token"]: config.telegram_token = data["telegram_token"]
    if "telegram_chat_id" in data: config.telegram_chat_id = data["telegram_chat_id"]
    if "telegram_enabled" in data: config.telegram_enabled = data["telegram_enabled"]
    if "target_price" in data:
        config.target_price = float(data["target_price"])
        bot.target_mid = config.target_price
    if "micro_range_enabled" in data:
        config.micro_range_enabled = bool(data["micro_range_enabled"])
    if "micro_gap" in data:
        config.micro_gap = float(data["micro_gap"])
    if "micro_width" in data:
        config.micro_width = float(data["micro_width"])
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
    bot.init_client()
    return {"status": "success"}

@app.post("/api/test_telegram")
async def test_telegram():
    if not config.telegram_token or not config.telegram_chat_id:
        return {"status": "error", "message": "설정 정보가 부족합니다."}
    
    bot.send_telegram("🔔 테스트 메시지입니다. 연결이 확인되었습니다!")
    return {"status": "success"}

@app.get("/api/test_connection")
async def test_connection():
    if not config.api_key or not config.secret_key:
        return {
            "status": "error",
            "message": "API Key 또는 Secret Key가 설정되지 않았습니다. 키를 먼저 입력하고 저장해 주세요."
        }
    
    try:
        bot.init_client()
        balances = bot.get_balances()
        if not balances:
            return {
                "status": "error",
                "message": f"[{config.exchange.upper()}] 거래소 연동 실패! API Key/Secret이 올바른지, IP 화이트리스트가 적용되어 있는지 확인해 주세요."
            }
        
        base_asset = bot.get_base_asset_name()
        symbol = config.symbol
        orders = bot.get_open_orders(symbol)
        
        usdt_info = balances.get("USDT", {"available": "0", "freeze": "0"})
        base_info = balances.get(base_asset, {"available": "0", "freeze": "0"})
        
        usdt_avail = float(usdt_info.get("available", 0))
        usdt_freeze = float(usdt_info.get("freeze", 0))
        usdt_total = usdt_avail + usdt_freeze
        
        base_avail = float(base_info.get("available", 0))
        base_freeze = float(base_info.get("freeze", 0))
        base_total = base_avail + base_freeze
        
        buy_orders = []
        sell_orders = []
        for o in orders:
            side_str = str(o.get("side", "")).lower()
            order_item = {
                "id": str(o.get("id", "")),
                "price": float(o.get("price", 0)),
                "amount": float(o.get("amount", 0)),
                "side": "buy" if side_str in ["buy", "2", 2] else "sell"
            }
            if order_item["side"] == "buy":
                buy_orders.append(order_item)
            else:
                sell_orders.append(order_item)
                
        buy_orders.sort(key=lambda x: x["price"], reverse=True)
        sell_orders.sort(key=lambda x: x["price"])
        
        return {
            "status": "success",
            "exchange": config.exchange,
            "symbol": symbol,
            "base_asset": base_asset,
            "usdt": {
                "available": usdt_avail,
                "freeze": usdt_freeze,
                "total": usdt_total
            },
            "token": {
                "name": base_asset,
                "available": base_avail,
                "freeze": base_freeze,
                "total": base_total
            },
            "orders": {
                "total_count": len(orders),
                "buy_count": len(buy_orders),
                "sell_count": len(sell_orders),
                "buy_orders": buy_orders,
                "sell_orders": sell_orders
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"연동 검증 중 오류 발생: {str(e)}"
        }

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

