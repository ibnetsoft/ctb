from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import threading
import json
import os
import glob
import re
import sys
import webbrowser
import time
from mmbot import MarketMaker, BotConfig
from tunnel_manager import tunnel_mgr

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

app = FastAPI()

# 내장 무중단 우회 터널 워치독 시작 (DigiFinex 전용 127.0.0.1:10808, Biconomy는 직결 고정 IP)
try:
    tunnel_mgr.start_background_watchdog()
except Exception as e:
    print(f"[Tunnel] 초기 기동 예외: {e}")

# ── 멀티 봇 매니저 (거래소/심볼별 완전 분리 운용) ─────────────────
configs: dict[str, BotConfig] = {}
bots: dict[str, MarketMaker] = {}
bot_threads: dict[str, threading.Thread] = {}
active_bot_id: str = "digifinex"
start_time = time.time()

def register_bot(bot_id: str, config_path: str, default_exchange: str = "digifinex", default_symbol: str = "CTB_USDT", start_thread: bool = False):
    cfg = BotConfig(config_path=config_path, bot_id=bot_id)
    if not os.path.exists(config_path):
        cfg.exchange = default_exchange
        cfg.symbol = default_symbol
        cfg.name = f"{default_exchange.upper()} ({default_symbol})"
        if default_exchange == "biconomy":
            cfg.target_price = 0.00004
            cfg.price_min = 0.000035
            cfg.price_max = 0.000045
            cfg.price_step = 0.0000005
            cfg.micro_gap = 0.00000005
            cfg.micro_width = 0.0000004
        cfg.save_config()
    mm = MarketMaker(cfg)
    configs[bot_id] = cfg
    bots[bot_id] = mm
    if start_thread and bot_id not in bot_threads:
        t = threading.Thread(target=mm.run_mm_loop, daemon=True)
        bot_threads[bot_id] = t
        t.start()
    return cfg, mm

def init_all_bots():
    # 1. 기본 DigiFinex 봇 로드 (기존 config.json 이 있으면 config_digifinex.json 생성/동기화)
    if not os.path.exists("config_digifinex.json") and os.path.exists("config.json"):
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            data["bot_id"] = "digifinex"
            data["name"] = f"DIGIFINEX ({data.get('symbol', 'CTB_USDT')})"
            with open("config_digifinex.json", "w", encoding="utf-8") as f2:
                json.dump(data, f2, indent=4)
        except Exception:
            pass

    register_bot("digifinex", "config_digifinex.json", default_exchange="digifinex", default_symbol="CTB_USDT")
    register_bot("biconomy", "config_biconomy.json", default_exchange="biconomy", default_symbol="AIR_USDT")

    # 추가 생성된 config_*.json 자동 검색 로드
    for path in sorted(glob.glob("config_*.json")):
        fname = os.path.basename(path)
        m = re.match(r"^config_([a-zA-Z0-9_\-]+)\.json$", fname)
        if m:
            bid = m.group(1).lower()
            if bid not in bots:
                register_bot(bid, path)

init_all_bots()

def resolve_bot(bot_id: str = None, data: dict = None) -> tuple[str, BotConfig, MarketMaker]:
    global active_bot_id
    bid = bot_id or (data.get("bot_id") if isinstance(data, dict) else None) or active_bot_id
    if bid not in bots:
        bid = active_bot_id if active_bot_id in bots else next(iter(bots.keys()))
    return bid, configs[bid], bots[bid]

# ── 바인딩 및 라우트 ──────────────────────────────
@app.get("/")
async def get_dashboard():
    path = get_resource_path("index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/bots")
async def list_bots():
    items = []
    for bid, cfg in configs.items():
        mm = bots[bid]
        items.append({
            "bot_id": bid,
            "name": cfg.name or f"{cfg.exchange.upper()} ({cfg.symbol})",
            "exchange": cfg.exchange,
            "symbol": cfg.symbol,
            "base_asset": mm.get_base_asset_name(),
            "is_running": cfg.is_running,
            "usdt_total": getattr(mm, "usdt_total", mm.usdt_bal),
            "air_total": getattr(mm, "air_total", mm.air_bal),
            "last_status": mm.last_status
        })
    return {
        "active_bot_id": active_bot_id,
        "bots": items
    }

@app.post("/api/bots/select")
async def select_bot(data: dict):
    global active_bot_id
    bid = str(data.get("bot_id", "")).strip().lower()
    if bid not in bots:
        return {"status": "error", "message": f"존재하지 않는 봇 ID입니다: {bid}"}
    active_bot_id = bid
    return {"status": "success", "active_bot_id": active_bot_id}

@app.post("/api/bots/create")
async def create_bot(data: dict):
    global active_bot_id
    exchange = str(data.get("exchange", "digifinex")).strip().lower()
    symbol = str(data.get("symbol", "TOKEN_USDT")).strip().upper()
    raw_id = str(data.get("bot_id", "")).strip().lower()
    if not raw_id:
        raw_id = re.sub(r"[^a-z0-9_\-]", "_", f"{exchange}_{symbol.lower()}")
    if raw_id in bots:
        return {"status": "error", "message": f"이미 존재하는 봇 ID입니다: {raw_id}"}
    config_path = f"config_{raw_id}.json"
    cfg, mm = register_bot(raw_id, config_path, default_exchange=exchange, default_symbol=symbol, start_thread=True)
    cfg.exchange = exchange
    cfg.symbol = symbol
    cfg.name = data.get("name") or f"{exchange.upper()} ({symbol})"
    if data.get("api_key"):
        cfg.api_key = str(data["api_key"]).strip()
    if data.get("secret_key"):
        cfg.secret_key = str(data["secret_key"]).strip()
    cfg.save_config()
    mm.init_client()
    active_bot_id = raw_id
    return {"status": "success", "bot_id": raw_id, "active_bot_id": active_bot_id}

@app.get("/api/status")
async def get_status(bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id)
    return {
        "bot_id": bid,
        "bot_name": config.name or f"{config.exchange.upper()} ({config.symbol})",
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
        "price_step": config.price_step,
        "tunnel": tunnel_mgr.get_status(),
        "bots": [
            {
                "bot_id": k,
                "name": c.name or f"{c.exchange.upper()} ({c.symbol})",
                "exchange": c.exchange,
                "symbol": c.symbol,
                "is_running": c.is_running
            }
            for k, c in configs.items()
        ]
    }

@app.get("/api/tunnel_status")
async def get_tunnel_status():
    return tunnel_mgr.get_status()

@app.post("/api/tunnel_refresh")
async def refresh_tunnel_status():
    tunnel_mgr.start_tunnel_process()
    tunnel_mgr.refresh_ips()
    return tunnel_mgr.get_status()

@app.post("/api/toggle")
async def toggle_bot(bot_id: str = Query(default=None), data: dict = {}):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
    config.is_running = not config.is_running
    if config.is_running:
        bot.recovery_history = []
        bot.recovery_in_progress = False
    return {"bot_id": bid, "is_running": config.is_running}

@app.post("/api/manual_sweep")
async def manual_sweep(bot_id: str = Query(default=None), data: dict = {}):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
    price = float(data.get("price", 0)) if data.get("price") else None
    amount_usdt = float(data.get("amount_usdt", 15.0))
    res = bot.execute_manual_sweep(sweep_price=price, usdt_amount=amount_usdt)
    return res

@app.post("/api/cancel_all")
async def cancel_all(bot_id: str = Query(default=None), data: dict = {}):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
    count = bot.cancel_all_orders(config.symbol)
    balances = bot.get_balances()
    if balances:
        base_asset = bot.get_base_asset_name()
        bot.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
        bot.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
    return {"status": "success", "bot_id": bid, "cancelled_count": count, "usdt_bal": bot.usdt_bal, "air_bal": bot.air_bal}

@app.post("/api/generate_grid")
async def generate_grid(bot_id: str = Query(default=None), data: dict = {}):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
    center_price = float(data.get("center_price")) if data.get("center_price") else config.target_price
    levels = int(data.get("levels", 12))
    step = float(data.get("step", config.price_step or 0.04))
    amount = float(data.get("amount", 0.02))
    cancel_existing = bool(data.get("cancel_existing", True))
    min_price = float(data.get("min_price", config.price_min or 540.0))
    max_price = float(data.get("max_price", config.price_max or 541.0))
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
async def get_orderbook(bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id)
    ob = bot.get_orderbook(config.symbol)
    raw_bids = ob.get("bids", [])
    raw_asks = ob.get("asks", [])
    bids = sorted(raw_bids, key=lambda x: float(x[0]), reverse=True) if raw_bids else []
    asks = sorted(raw_asks, key=lambda x: float(x[0]), reverse=False) if raw_asks else []
    
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    spread = max(0.0, best_ask - best_bid) if (bids and asks) else 0.0
    dec = 4 if (config.price_min >= 1.0 or best_bid >= 1.0) else 8
    box_pad = 0.002 if dec == 4 else (config.price_max - config.price_min) * 0.005
    margin = max(10 ** (-dec), spread * 0.04)
    min_sweep = round(best_bid + margin, dec)
    max_sweep = round(best_ask - margin, dec)
    if config.price_min > 0 and config.price_max > 0:
        min_sweep = max(min_sweep, round(config.price_min + box_pad, dec))
        max_sweep = min(max_sweep, round(config.price_max - box_pad, dec))
    mid_sweep = round((best_bid + best_ask) / 2.0, dec)
    return {
        "bot_id": bid,
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
async def update_config(data: dict, bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
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
    config.name = f"{config.exchange.upper()} ({config.symbol})"
    config.save_config()
    bot.init_client()
    return {"status": "success", "bot_id": bid}

@app.get("/api/settings")
async def get_settings(bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id)
    return {
        "bot_id": bid,
        "name": config.name or f"{config.exchange.upper()} ({config.symbol})",
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
async def save_settings(data: dict, bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
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
    
    config.name = f"{config.exchange.upper()} ({config.symbol})"
    config.save_config()
    bot.init_client()
    return {"status": "success", "bot_id": bid}

@app.post("/api/test_telegram")
async def test_telegram(bot_id: str = Query(default=None), data: dict = {}):
    bid, config, bot = resolve_bot(bot_id=bot_id, data=data)
    if not config.telegram_token or not config.telegram_chat_id:
        return {"status": "error", "message": "설정 정보가 부족합니다."}
    
    bot.send_telegram("🔔 테스트 메시지입니다. 연결이 확인되었습니다!")
    return {"status": "success"}

@app.get("/api/test_connection")
async def test_connection(bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id)
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
            "bot_id": bid,
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
async def get_logs(bot_id: str = Query(default=None)):
    bid, config, bot = resolve_bot(bot_id=bot_id)
    return {"bot_id": bid, "logs": config.logs[-50:]}

# ── 백그라운드 멀티 봇 가동 ───────────────────────────
def open_browser():
    """브라우저 자동 열기"""
    time.sleep(1.5) # 서버 부팅 대기
    webbrowser.open("http://localhost:8050")

@app.on_event("startup")
async def startup_event():
    # 등록된 모든 봇을 각각의 독립 스레드에서 실행 (서로 간섭 없이 동시 가동 가능)
    for bid, mm in bots.items():
        if bid not in bot_threads:
            t = threading.Thread(target=mm.run_mm_loop, daemon=True)
            bot_threads[bid] = t
            t.start()
    
    if os.environ.get("NO_BROWSER") != "1":
        threading.Thread(target=open_browser, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8050)
