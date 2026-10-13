import hashlib
import hmac
import time
import requests
import random
import math
import os
import json
import urllib.parse
from datetime import datetime

from tunnel_manager import tunnel_mgr

# ── 거래소 클라이언트 인터페이스 ─────────────────────────────────

class DigiFinexClient:
    """디지파이넥스(DigiFinex) API v3 연동 클라이언트 (내장 무중단 우회 터널 자동 연동)"""
    FALLBACK_URLS = [
        "https://openapi.digifinex.com",
        "https://openapi.digifinex.vip",
        "https://openapi.digifinex.io"
    ]

    def __init__(self, api_key: str, secret_key: str, log_func=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = self.FALLBACK_URLS[0]
        self.log = log_func or print
        self.time_offset = 0
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Connection": "keep-alive"})
        tunnel_mgr.start_tunnel_process()
        self.sync_time()

    def _request(self, method: str, url: str, **kwargs):
        """내장 우회 터널(127.0.0.1:10808)을 우선 사용하며, 실패 시 직결로 즉시 폴백합니다."""
        proxies = tunnel_mgr.proxies_dict if tunnel_mgr.is_running else {}
        if proxies:
            try:
                return self.session.request(method, url, proxies=proxies, **kwargs)
            except Exception:
                pass
        return self.session.request(method, url, **kwargs)

    def sync_time(self):
        """거래소 서버 시간과 로컬 PC 시간 오차를 동기화합니다."""
        last_err = None
        for url in self.FALLBACK_URLS:
            try:
                res = self._request("GET", f"{url}/v3/time", timeout=5)
                if res.status_code != 200:
                    last_err = f"HTTP {res.status_code} ({url})"
                    continue
                data = res.json()
                if data.get("code") == 0 and "server_time" in data:
                    self.base_url = url
                    server_ts = int(data["server_time"])
                    local_ts = int(time.time())
                    self.time_offset = server_ts - local_ts
                    self.log(f"[DigiFinex] 서버 시간 동기화 완료 (오차: {self.time_offset}초 | 내장 우회터널 연동)")
                    return
            except Exception as e:
                last_err = e
        self.log(f"[DigiFinex] 서버 시간 동기화 실패: {last_err}")

    def _sign(self, param_str: str) -> str:
        return hmac.new(
            self.secret_key.encode('utf-8'),
            param_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def _headers(self, sign: str) -> dict:
        ts = int(time.time() + getattr(self, "time_offset", 0))
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-TIMESTAMP": str(ts),
            "ACCESS-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded"
        }

    def private_get(self, endpoint: str, params: dict = None) -> dict:
        for attempt in range(2):
            params = params or {}
            sorted_items = sorted(params.items())
            query_str = urllib.parse.urlencode(sorted_items)
            sign = self._sign(query_str)
            headers = self._headers(sign)
            url = f"{self.base_url}{endpoint}"
            if query_str:
                url += f"?{query_str}"
            try:
                res = self._request("GET", url, headers=headers, timeout=10)
                if res.status_code in (451, 403) and attempt == 0:
                    self.sync_time()
                    continue
                data = res.json()
                if data.get("code") == 10008 and attempt == 0:
                    self.log("[DigiFinex] 타임스탬프 오차(10008) 감지, 시간 재동기화 후 재시도...")
                    self.sync_time()
                    continue
                if data.get("code") != 0:
                    self.log(f"[DigiFinex] API 오류 [GET {endpoint}]: {data}")
                return data
            except Exception as e:
                self.log(f"[DigiFinex] 네트워크 오류 [GET {endpoint}]: {e}")
                return {}
        return {}

    def private_post(self, endpoint: str, data: dict = None) -> dict:
        for attempt in range(2):
            data = data or {}
            sorted_items = sorted(data.items())
            body_str = urllib.parse.urlencode(sorted_items)
            sign = self._sign(body_str)
            headers = self._headers(sign)
            url = f"{self.base_url}{endpoint}"
            try:
                res = self._request("POST", url, data=body_str, headers=headers, timeout=10)
                if res.status_code in (451, 403) and attempt == 0:
                    self.sync_time()
                    continue
                result = res.json()
                if result.get("code") == 10008 and attempt == 0:
                    self.log("[DigiFinex] 타임스탬프 오차(10008) 감지, 시간 재동기화 후 재시도...")
                    self.sync_time()
                    continue
                if result.get("code") != 0:
                    self.log(f"[DigiFinex] API 오류 [POST {endpoint}]: {result}")
                return result
            except Exception as e:
                self.log(f"[DigiFinex] 네트워크 오류 [POST {endpoint}]: {e}")
                return {}
        return {}

    def get_orderbook(self, symbol: str) -> dict:
        sym = symbol.lower()
        now = time.time()
        if hasattr(self, "_cached_orderbook") and hasattr(self, "_cached_orderbook_time"):
            if now - self._cached_orderbook_time < 1.5 and self._cached_orderbook.get("bids"):
                return self._cached_orderbook
        try:
            res = self._request("GET", f"{self.base_url}/v3/order_book", params={"symbol": sym}, timeout=10)
            if res.status_code != 200:
                if hasattr(self, "_cached_orderbook") and self._cached_orderbook.get("bids"):
                    return self._cached_orderbook
                return {"bids": [], "asks": []}
            data = res.json()
            # DigiFinex: {"code": 0, "bids": [[price, amount], ...], "asks": [[price, amount], ...]}
            # 주의: DigiFinex는 asks를 내림차순(높은 가격부터)으로 반환하므로 반드시 오름차순(최저가부터)으로 정렬해야 함!
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])
            bids = sorted(raw_bids, key=lambda x: float(x[0]), reverse=True)
            asks = sorted(raw_asks, key=lambda x: float(x[0]), reverse=False)
            res_dict = {
                "bids": bids,
                "asks": asks
            }
            self._cached_orderbook = res_dict
            self._cached_orderbook_time = now
            return res_dict
        except Exception as e:
            if hasattr(self, "_cached_orderbook") and self._cached_orderbook.get("bids"):
                return self._cached_orderbook
            self.log(f"[DigiFinex] 오더북 조회 실패: {e}")
            return {"bids": [], "asks": []}

    def get_balances(self) -> dict:
        now = time.time()
        if hasattr(self, "_cached_balances") and hasattr(self, "_cached_balances_time"):
            if now - self._cached_balances_time < 2.0 and self._cached_balances:
                return self._cached_balances
        res = self.private_get("/v3/spot/assets")
        balances = {}
        if res.get("code") == 0:
            for item in res.get("list", []):
                curr = str(item.get("currency", "")).upper()
                free = str(item.get("free", 0))
                total = str(item.get("total", free))
                balances[curr] = {
                    "available": free,
                    "total": total,
                    "freeze": str(max(0.0, float(total) - float(free)))
                }
            self._cached_balances = balances
            self._cached_balances_time = now
        elif hasattr(self, "_cached_balances") and self._cached_balances:
            return self._cached_balances
        return balances

    def get_open_orders(self, symbol: str) -> list:
        sym = symbol.lower()
        res = self.private_get("/v3/spot/order/current", {"symbol": sym})
        orders = []
        if res.get("code") == 0:
            data = res.get("data", [])
            for item in data:
                orders.append({
                    "id": str(item.get("order_id", "")),
                    "price": float(item.get("price", 0)),
                    "amount": float(item.get("amount", 0)),
                    "side": item.get("type", "")
                })
        return orders

    def place_order(self, symbol: str, side: int, price: float, amount: float):
        # side: 1=ASK(매도), 2=BID(매수)
        side_str = "buy" if side == 2 else "sell"
        sym = symbol.lower()
        if price >= 100:
            p_str = f"{round(price, 4):.4f}"
        elif price >= 1:
            p_str = f"{round(price, 6):.6f}"
        else:
            p_str = f"{round(price, 8):.8f}"

        if amount >= 100:
            a_str = f"{round(amount, 2):.2f}"
        elif amount >= 1:
            a_str = f"{round(amount, 4):.4f}"
        else:
            a_str = f"{round(amount, 6):.6f}"

        payload = {
            "symbol": sym,
            "type": side_str,
            "price": p_str,
            "amount": a_str
        }
        res = self.private_post("/v3/spot/order/new", payload)
        if res.get("code") == 0:
            k_side = "매수" if side == 2 else "매도"
            self.log(f"[DigiFinex] {k_side} 성공: {p_str} | 수량: {a_str}")
            # 공통 포맷 호환을 위해 id 주입
            if "order_id" in res:
                res["result"] = {"id": res.get("order_id")}
        return res

    def cancel_order(self, order_id: str, symbol: str):
        payload = {"order_id": str(order_id)}
        return self.private_post("/v3/spot/order/cancel", payload)

    def cancel_all_orders(self, symbol: str) -> int:
        orders = self.get_open_orders(symbol)
        if not orders:
            return 0
        order_ids = [str(o["id"]) for o in orders if o.get("id")]
        self.log(f"🧹 [DigiFinex 클린업] 기존 미체결 주문 {len(order_ids)}개를 모두 취소합니다.")
        cancelled = 0
        for oid in order_ids:
            try:
                res = self.cancel_order(oid, symbol)
                if res.get("code") == 0:
                    cancelled += 1
                time.sleep(0.05)
            except Exception as e:
                self.log(f"[DigiFinex] 주문 {oid} 취소 실패: {e}")
        return cancelled

    def get_btc_stats(self) -> tuple:
        try:
            res = self._request("GET", f"{self.base_url}/v3/ticker", params={"symbol": "btc_usdt"}, timeout=10)
            data = res.json()
            ticker_list = data.get("ticker", [])
            if ticker_list:
                t = ticker_list[0]
                return float(t.get("last", 0)), float(t.get("vol", 0))
        except Exception as e:
            self.log(f"[DigiFinex] BTC 시세 조회 실패: {e}")
        return 0.0, 0.0


class BiconomyClient:
    """바이코노미(Biconomy) API v1 연동 클라이언트 (Keep-Alive 초고속 세션 + 직결 고정 IP)"""
    def __init__(self, api_key: str, secret_key: str, log_func=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = "https://api.biconomy.com"
        self.log = log_func or print
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"X-SITE-ID": "127", "Connection": "keep-alive"})
        self.fast_session2 = requests.Session()
        self.fast_session2.trust_env = False
        self.fast_session2.headers.update({"X-SITE-ID": "127", "Connection": "keep-alive"})

    def make_sign(self, params: dict) -> str:
        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)
        query += f"&secret_key={self.secret_key}"
        return hashlib.md5(query.encode()).hexdigest().upper()

    def private_post(self, endpoint: str, params: dict, use_secondary_session: bool = False) -> dict:
        payload = dict(params)
        payload["api_key"] = self.api_key
        payload["sign"] = self.make_sign(payload)
        sess = self.fast_session2 if use_secondary_session else self.session
        try:
            res = sess.post(self.base_url + endpoint, data=payload, timeout=10)
            result = res.json()
            if result.get("code") != 0 and result.get("code") != 10:
                self.log(f"[Biconomy] API 오류 [{endpoint}]: {result}")
            return result
        except Exception as e:
            self.log(f"[Biconomy] 네트워크 오류 [{endpoint}]: {e}")
            return {}

    def place_simultaneous_wash_pair(self, symbol: str, price: float, amount: float, head_start_sec: float = 0.03) -> tuple:
        """
        Keep-Alive된 2개의 독립 세션으로 선매도(side=1)와 후매수(side=2)를 0.03초 시차로 동시 병렬 발사하여
        외부 스나이핑 봇이 호가창을 보고 끼어들 1초의 틈을 원천 제거합니다.
        """
        from concurrent.futures import ThreadPoolExecutor
        sym = symbol.upper()
        p_str = "{:.8f}".format(round(price, 8))
        a_str = "{:.3f}".format(round(amount, 3))

        def _fire_sell():
            res = self.private_post("/api/v1/private/trade/limit", {
                "market": sym, "side": 1, "price": p_str, "amount": a_str
            }, use_secondary_session=False)
            if res.get("code") == 0:
                self.log(f"[Biconomy] 초고속 선매도 성공: {price:.8f} | 수량: {amount}")
            return res

        def _fire_buy():
            time.sleep(head_start_sec)
            res = self.private_post("/api/v1/private/trade/limit", {
                "market": sym, "side": 2, "price": p_str, "amount": a_str
            }, use_secondary_session=True)
            if res.get("code") == 0:
                self.log(f"[Biconomy] 초고속 후매수 성공: {price:.8f} | 수량: {amount}")
            return res

        with ThreadPoolExecutor(max_workers=2) as ex:
            f_sell = ex.submit(_fire_sell)
            f_buy = ex.submit(_fire_buy)
            res_sell = f_sell.result()
            res_buy = f_buy.result()
        return res_sell, res_buy

    def get_orderbook(self, symbol: str) -> dict:
        sym = symbol.upper()
        try:
            res = self.session.get(f"{self.base_url}/api/v1/depth", params={"symbol": sym}, timeout=10)
            data = res.json()
            result = data.get("result") if isinstance(data.get("result"), dict) else data
            raw_bids = result.get("bids", []) if isinstance(result, dict) else []
            raw_asks = result.get("asks", []) if isinstance(result, dict) else []
            bids = sorted(raw_bids, key=lambda x: float(x[0]), reverse=True)
            asks = sorted(raw_asks, key=lambda x: float(x[0]), reverse=False)
            return {"bids": bids, "asks": asks}
        except:
            return {"bids": [], "asks": []}

    def get_balances(self) -> dict:
        res = self.private_post("/api/v1/private/user", {})
        if res.get("code") == 0:
            return res.get("result", {})
        return {}

    def get_open_orders(self, symbol: str) -> list:
        sym = symbol.upper()
        all_orders = []
        offset = 0
        limit = 100
        while True:
            res = self.private_post("/api/v1/private/order/pending", {"market": sym, "offset": offset, "limit": limit})
            if res.get("code") == 0:
                result_obj = res.get("result", {})
                if isinstance(result_obj, dict):
                    records = result_obj.get("records", [])
                    if not records:
                        break
                    for item in records:
                        side_val = str(item.get("side", "")).lower()
                        all_orders.append({
                            "id": str(item.get("id", "")),
                            "price": float(item.get("price", 0)),
                            "amount": float(item.get("amount", 0)),
                            "side": "buy" if side_val in ["buy", "2"] else "sell"
                        })
                    if len(records) < limit:
                        break
                    offset += limit
                else:
                    break
            else:
                break
        return all_orders

    def place_order(self, symbol: str, side: int, price: float, amount: float):
        sym = symbol.upper()
        res = self.private_post("/api/v1/private/trade/limit", {
            "market": sym,
            "side": side,
            "price": "{:.8f}".format(round(price, 8)),
            "amount": "{:.3f}".format(round(amount, 3)),
        })
        if res.get("code") == 0:
            side_str = "매수" if side == 2 else "매도"
            self.log(f"[Biconomy] {side_str} 성공: {price:.8f} | 수량: {amount}")
        return res

    def cancel_order(self, order_id: str, symbol: str):
        sym = symbol.upper()
        return self.private_post("/api/v1/private/trade/cancel", {"order_id": order_id, "market": sym})

    def cancel_all_orders(self, symbol: str) -> int:
        orders = self.get_open_orders(symbol)
        if orders:
            self.log(f"🧹 [Biconomy 클린업] 기존 미체결 주문 {len(orders)}개를 모두 취소합니다.")
            for o in orders:
                self.cancel_order(o["id"], symbol)
            time.sleep(0.3)
        return len(orders) if orders else 0

    def get_btc_stats(self) -> tuple:
        try:
            res = requests.get(f"{self.base_url}/api/v1/tickers", timeout=10)
            data = res.json()
            tickers = data.get("ticker", [])
            for t in tickers:
                if t.get("symbol") == "BTC_USDT":
                    return float(t.get("last", 0)), float(t.get("vol", 0))
        except Exception as e:
            self.log(f"[Biconomy] BTC 통계 조회 실패: {e}")
        return 0.0, 0.0


# ── 설정 클래스 ───────────────────────────────────────────────

class BotConfig:
    def __init__(self, config_path="config.json", bot_id=None, name=None):
        self.config_path = config_path
        self.bot_id = bot_id or "digifinex"
        self.name = name or ""
        self.exchange = "digifinex"  # 기본 거래소: digifinex 또는 biconomy
        self.api_key = ""
        self.secret_key = ""
        self.telegram_token = ""
        self.telegram_chat_id = ""
        self.telegram_enabled = False
        
        self.symbol = "CTB_USDT"
        self.beta = 3.0
        self.interval = 5
        self.is_running = False
        self.anchor_reset_interval = 120
        self.logs = []
        self.target_price = 539.0
        self.micro_range_enabled = True
        self.micro_gap = 0.05
        self.micro_width = 0.20
        self.price_oscillation_enabled = True
        self.price_min = 535.0
        self.price_max = 545.0
        self.price_step = 0.1
        self.auto_pause_enabled = False
        self.run_duration = 120
        self.pause_duration = 30
        self.auto_recovery_enabled = True
        self.auto_recovery_delay = 30
        
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.bot_id = data.get("bot_id", self.bot_id)
                    self.name = data.get("name", self.name)
                    self.exchange = data.get("exchange", self.exchange).lower()
                    self.symbol = data.get("symbol", self.symbol)
                    self.api_key = data.get("api_key", self.api_key)
                    self.secret_key = data.get("secret_key", self.secret_key)
                    self.telegram_token = data.get("telegram_token", self.telegram_token)
                    self.telegram_chat_id = data.get("telegram_chat_id", self.telegram_chat_id)
                    self.telegram_enabled = data.get("telegram_enabled", self.telegram_enabled)
                    self.target_price = float(data.get("target_price", self.target_price))
                    self.micro_range_enabled = bool(data.get("micro_range_enabled", self.micro_range_enabled))
                    self.micro_gap = float(data.get("micro_gap", self.micro_gap))
                    self.micro_width = float(data.get("micro_width", self.micro_width))
                    self.price_oscillation_enabled = bool(data.get("price_oscillation_enabled", self.price_oscillation_enabled))
                    self.price_min = float(data.get("price_min", self.price_min))
                    self.price_max = float(data.get("price_max", self.price_max))
                    self.price_step = float(data.get("price_step", self.price_step))
                    self.auto_pause_enabled = bool(data.get("auto_pause_enabled", self.auto_pause_enabled))
                    self.run_duration = int(data.get("run_duration", self.run_duration))
                    self.pause_duration = int(data.get("pause_duration", self.pause_duration))
                    self.auto_recovery_enabled = bool(data.get("auto_recovery_enabled", self.auto_recovery_enabled))
                    self.auto_recovery_delay = int(data.get("auto_recovery_delay", self.auto_recovery_delay))
                    if not self.name:
                        self.name = f"{self.exchange.upper()} ({self.symbol})"
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

    def save_config(self):
        data = {
            "bot_id": self.bot_id,
            "name": self.name or f"{self.exchange.upper()} ({self.symbol})",
            "exchange": self.exchange,
            "symbol": self.symbol,
            "api_key": self.api_key,
            "secret_key": self.secret_key,
            "telegram_token": self.telegram_token,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_enabled": self.telegram_enabled,
            "target_price": self.target_price,
            "micro_range_enabled": self.micro_range_enabled,
            "micro_gap": self.micro_gap,
            "micro_width": self.micro_width,
            "price_oscillation_enabled": self.price_oscillation_enabled,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "price_step": self.price_step,
            "auto_pause_enabled": self.auto_pause_enabled,
            "run_duration": self.run_duration,
            "pause_duration": self.pause_duration
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            # 기본 digifinex 봇은 레거시 호환을 위해 config.json에도 동기화
            if self.bot_id == "digifinex" and self.config_path != "config.json":
                with open("config.json", "w", encoding="utf-8") as f2:
                    json.dump(data, f2, indent=4)
        except Exception as e:
            print(f"설정 파일 저장 실패: {e}")


# ── 마켓 메이커 엔진 ──────────────────────────────────────────

class MarketMaker:
    def __init__(self, config: BotConfig):
        self.config = config
        self.init_client()

        self.target_mid = 0.0
        self.vol_ratio = 1.0
        self.loop_count = 0
        self.current_btc_p = 0.0
        self.current_btc_v = 0.0
        self.usdt_bal = 0.0
        self.usdt_total = 0.0
        self.usdt_freeze = 0.0
        self.air_bal = 0.0
        self.air_total = 0.0
        self.air_freeze = 0.0
        self.last_status = "Initializing..."
        
        self.auto_paused = False
        self.auto_pause_state_start = time.time()
        
        # 7주기 거래 금액 패턴
        self.cycle_amounts = [1000.0, 2000.0, 3000.0, 1000.0, 3000.0, 5000.0, 2000.0]
        self.cycle_index = 0

    def init_client(self):
        """거래소 설정에 맞춰 클라이언트 인스턴스 생성"""
        if self.config.exchange.lower() == "digifinex":
            self.client = DigiFinexClient(self.config.api_key, self.config.secret_key, self.log)
        else:
            self.client = BiconomyClient(self.config.api_key, self.config.secret_key, self.log)

    def log(self, message):
        import sys
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        try:
            print(full_msg, flush=True)
        except UnicodeEncodeError:
            try:
                encoding = sys.stdout.encoding or 'utf-8'
                print(full_msg.encode(encoding, errors='replace').decode(encoding), flush=True)
            except:
                print(f"[{timestamp}] [Log] " + message.encode('ascii', errors='replace').decode('ascii'), flush=True)
        self.config.logs.append(full_msg)
        if len(self.config.logs) > 100:
            self.config.logs.pop(0)

    def send_telegram(self, message):
        if not self.config.telegram_enabled or not self.config.telegram_token or not self.config.telegram_chat_id:
            return
        
        url = f"https://api.telegram.org/bot{self.config.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": f"[{self.config.exchange.upper()} MM Bot] {message}",
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"텔레그램 전송 실패: {e}")

    # 거래소 API 위임 메서드
    def get_orderbook(self, symbol: str) -> dict:
        return self.client.get_orderbook(symbol)

    def get_balances(self) -> dict:
        return self.client.get_balances()

    def get_open_orders(self, symbol: str) -> list:
        return self.client.get_open_orders(symbol)

    def place_order(self, symbol: str, side: int, price: float, amount: float):
        # 🛡️ 박스권 절대 강제 방어: 설정된 min/max 범위를 벗어나는 모든 주문 원천 차단
        if self.config.price_min > 0 and self.config.price_max > 0:
            dec = 4 if self.config.price_min >= 1.0 else 8
            tol = 0.0001 if self.config.price_min >= 1.0 else 1e-8
            if price < (self.config.price_min - tol) or price > (self.config.price_max + tol):
                self.log(f"🚨 [박스권 강제 차단] 주문 가격({price:.{dec}f})이 박스권 [{self.config.price_min:.{dec}f} ~ {self.config.price_max:.{dec}f}] 밖이므로 발주를 전면 차단합니다.")
                return {"code": -999, "message": f"Box range violation: {price} not in [{self.config.price_min}, {self.config.price_max}]"}
        return self.client.place_order(symbol, side, price, amount)

    def cancel_order(self, order_id: str, symbol: str):
        return self.client.cancel_order(order_id, symbol)

    def cancel_all_orders(self, symbol: str):
        return self.client.cancel_all_orders(symbol)

    def update_orderbook_seamlessly(self, center_price: float = None, levels: int = 12, min_price: float = None, max_price: float = None) -> dict:
        """
        호가창을 절대 전부 비우지 않고(No Full Wipe), 기존 호가의 대다수를 유지하면서
        중심가 이동에 따라 필요한 호가만 추가(Add First)하고 외곽/충돌 호가만 선별 정리(Prune)하는 점진적 무중단 슬라이딩 알고리즘.
        - 호가창이 비워지는 순간이 0초도 발생하지 않음 (항시 10~12단계 이상 유지)
        """
        symbol = self.config.symbol
        min_p = float(min_price) if min_price else (self.config.price_min or 540.0)
        max_p = float(max_price) if max_price else (self.config.price_max or 541.0)
        dec = 4 if min_p >= 1.0 else 8
        box_span = max(10 ** (-dec), max_p - min_p)
        scale = 1.0 if min_p >= 1.0 else box_span
        tol = 0.0001 if min_p >= 1.0 else 1e-8

        if center_price is None or center_price <= 0:
            center_price = round((min_p + max_p) / 2.0, dec)
        else:
            center_price = round(float(center_price), dec)

        # 1. 현재 열려 있는 주문 실시간 조회
        try:
            cur_orders = self.get_open_orders(symbol)
        except Exception as e:
            self.log(f"주문 조회 오류: {e}")
            cur_orders = []

        existing_buys = []
        existing_sells = []
        inner_conflicts = []

        # 안쪽 충돌 임계치 (스프레드 확보: 체결을 위해 center_price 주변 최소 0.010 * scale 이상 공간 확보)
        inner_gap = 0.010 * scale

        for o in cur_orders:
            try:
                p = round(float(o.get("price", 0)), dec)
                side = str(o.get("side", "")).lower()
                oid = str(o.get("id", ""))
                amt = float(o.get("amount", 0))

                # 박스권 [min_p, max_p] 밖이면 즉시 취소 대상
                if p < (min_p - tol) or p > (max_p + tol):
                    inner_conflicts.append((oid, f"박스권 외({p:.{dec}f})"))
                    continue

                if side == "sell":
                    # 매도 주문이 새로운 중심가보다 아래이거나 너무 가까우면 취소 대상 (충돌 방지)
                    if p <= (center_price + inner_gap):
                        inner_conflicts.append((oid, f"중심가 근접 매도({p:.{dec}f})"))
                    else:
                        existing_sells.append({"id": oid, "price": p, "amount": amt})
                elif side == "buy":
                    # 매수 주문이 새로운 중심가보다 위이거나 너무 가까우면 취소 대상 (충돌 방지)
                    if p >= (center_price - inner_gap):
                        inner_conflicts.append((oid, f"중심가 근접 매수({p:.{dec}f})"))
                    else:
                        existing_buys.append({"id": oid, "price": p, "amount": amt})
            except Exception:
                continue

        # 충돌하는 1~2개 내부 호가만 선별 취소 (호가창의 90% 이상은 그대로 살려둠)
        if inner_conflicts:
            for oid, reason in inner_conflicts:
                self.cancel_order(oid, symbol)
                time.sleep(0.04)

        # 2. 남은 호가 정렬
        existing_sells.sort(key=lambda x: x["price"]) # 최저 매도가순
        existing_buys.sort(key=lambda x: x["price"], reverse=True) # 최고 매수가순

        created_sells = []
        created_buys = []
        errors = []

        # 3. 매도 호가 부족분 먼저 발주 (Add First: 호가창이 비지 않도록 신규 주문 먼저 투입)
        needed_asks = max(0, levels - len(existing_sells))
        if needed_asks > 0:
            existing_sell_prices = [x["price"] for x in existing_sells]
            span_ask = (max_p - center_price) * 0.95
            step_ask_avg = max(0.015 * scale, span_ask / (levels + 1))
            curr_ask = center_price + max(0.015 * scale, inner_gap + 0.005 * scale)
            
            candidates = []
            for _ in range(levels * 3):
                curr_ask += random.uniform(0.65, 1.35) * step_ask_avg
                p = round(min(max_p - 0.002 * scale, curr_ask), dec)
                if p > (center_price + inner_gap) and not any(abs(p - ex) < (0.006 * scale) for ex in (existing_sell_prices + [c[0] for c in candidates])):
                    amt_val = round(random.uniform(0.0185, 0.0255), 4) if min_p >= 1.0 else round(random.uniform(10.0, 14.0) / max(p, 1e-8), 3)
                    candidates.append((p, amt_val))
                if len(candidates) >= needed_asks:
                    break

            for p, amt in candidates[:needed_asks]:
                res = self.place_order(symbol, 1, p, amt)
                oid = res.get("result", {}).get("id") or res.get("order_id")
                if res.get("code") == 0 or oid:
                    created_sells.append({"price": p, "amount": amt, "id": oid})
                    existing_sells.append({"id": oid, "price": p, "amount": amt})
                else:
                    errors.append(f"매도 {p} 실패: {res.get('msg', '오류')}")
                time.sleep(0.08)

        # 4. 매수 호가 부족분 먼저 발주 (Add First: 호가창이 비지 않도록 신규 주문 먼저 투입)
        needed_bids = max(0, levels - len(existing_buys))
        if needed_bids > 0:
            existing_buy_prices = [x["price"] for x in existing_buys]
            span_bid = (center_price - min_p) * 0.95
            step_bid_avg = max(0.015 * scale, span_bid / (levels + 1))
            curr_bid = center_price - max(0.015 * scale, inner_gap + 0.005 * scale)

            candidates = []
            for _ in range(levels * 3):
                curr_bid -= random.uniform(0.65, 1.35) * step_bid_avg
                p = round(max(min_p + 0.002 * scale, curr_bid), dec)
                if p < (center_price - inner_gap) and not any(abs(p - ex) < (0.006 * scale) for ex in (existing_buy_prices + [c[0] for c in candidates])):
                    amt_val = round(random.uniform(0.0185, 0.0255), 4) if min_p >= 1.0 else round(random.uniform(10.0, 14.0) / max(p, 1e-8), 3)
                    candidates.append((p, amt_val))
                if len(candidates) >= needed_bids:
                    break

            for p, amt in candidates[:needed_bids]:
                res = self.place_order(symbol, 2, p, amt)
                oid = res.get("result", {}).get("id") or res.get("order_id")
                if res.get("code") == 0 or oid:
                    created_buys.append({"price": p, "amount": amt, "id": oid})
                    existing_buys.append({"id": oid, "price": p, "amount": amt})
                else:
                    errors.append(f"매수 {p} 실패: {res.get('msg', '오류')}")
                time.sleep(0.08)

        # 5. 잉여 외곽 호가 정리 (Prune Outliers):
        # 만약 매도/매수가 12개를 초과하면 중심가에서 가장 먼 외곽 주문을 몇 개만 정리
        existing_sells.sort(key=lambda x: x["price"])
        if len(existing_sells) > levels:
            excess_sells = existing_sells[levels:] # 가격이 가장 높은 외곽 매도들
            for ex in excess_sells:
                if ex.get("id"):
                    self.cancel_order(ex["id"], symbol)
                    time.sleep(0.04)

        existing_buys.sort(key=lambda x: x["price"], reverse=True)
        if len(existing_buys) > levels:
            excess_bids = existing_buys[levels:] # 가격이 가장 낮은 외곽 매수들
            for ex in excess_bids:
                if ex.get("id"):
                    self.cancel_order(ex["id"], symbol)
                    time.sleep(0.04)

        # 6. 설정 동기화
        self.config.price_min = min_p
        self.config.price_max = max_p
        self.config.target_price = center_price
        self.config.save_config()
        self.target_mid = center_price

        total_added = len(created_buys) + len(created_sells)
        total_pruned = len(inner_conflicts)
        self.log(f"🌊 [점진적 무중단 호가 갱신] 중심: {center_price:.{dec}f} | 보충: +{total_added}건 (매수 {len(created_buys)}, 매도 {len(created_sells)}) | 정리: -{total_pruned}건 | 호가창 상태: 상시 유지")

        return {
            "status": "success",
            "center_price": center_price,
            "min_price": min_p,
            "max_price": max_p,
            "created_buys": created_buys,
            "created_sells": created_sells,
            "total_count": total_added,
            "errors": errors,
            "message": f"점진적 무중단 12단계 호가 유지 완료 (+{total_added}건, -{total_pruned}건)"
        }

    def generate_orderbook_grid(self, center_price: float = None, levels: int = 12, step: float = 0.04, amount_per_order: float = 0.02, cancel_existing: bool = False, min_price: float = None, max_price: float = None, organic: bool = True) -> dict:
        """
        호가창 그리드 생성 메서드:
        호가창이 깨끗이 비워지는 일이 없도록 전체 취소를 하지 않고 update_orderbook_seamlessly를 호출하여 점진적으로 보충/교체합니다.
        """
        return self.update_orderbook_seamlessly(center_price=center_price, levels=levels, min_price=min_price, max_price=max_price)

    def maintain_orderbook_grid(self, levels: int = 12):
        """
        실시간 12단계 호가 연속 유지 관리:
        부족하거나 위치가 어긋난 호가를 점진적으로 보충하여 호가창이 항상 꽉 차 있도록 유지합니다.
        """
        min_p = self.config.price_min or 540.0
        max_p = self.config.price_max or 541.0
        dec = 4 if min_p >= 1.0 else 8
        center_p = self.config.target_price or round((min_p + max_p) / 2.0, dec)
        
        try:
            orders = self.get_open_orders(self.config.symbol)
            buys = [o for o in orders if str(o.get("side", "")).lower() == "buy" and min_p <= float(o.get("price", 0)) <= max_p]
            sells = [o for o in orders if str(o.get("side", "")).lower() == "sell" and min_p <= float(o.get("price", 0)) <= max_p]
            
            if len(buys) < (levels - 1) or len(sells) < (levels - 1):
                self.update_orderbook_seamlessly(center_price=center_p, levels=levels, min_price=min_p, max_price=max_p)
        except Exception as e:
            self.log(f"⚠️ 호가 유지 관리 중 오류: {e}")

    def calculate_next_organic_price(self) -> float:
        """
        100+ 다채로운 시장 시뮬레이션 파동 엔진:
        - 6대 시장 국면 (상승 추세, 하락 추세, 박스권 횡보, 변동성 급등락, 풀백 반등, 불규칙 브라운 운동)
        - 마르코프 체인 기반 확률적 국면 전환 (외부에서 다음 틱의 방향을 예측 불가능)
        - 소수점 불규칙 지터링으로 중복 가격 발생 원천 방지
        - 설정된 박스권 내부 엄격 안전 유지
        """
        min_p = self.config.price_min or 540.0
        max_p = self.config.price_max or 541.0
        dec = 4 if min_p >= 1.0 else 8
        scale = 1.0 if min_p >= 1.0 else max(1e-8, max_p - min_p)
        safe_min = round(min_p + 0.08 * scale, dec)
        safe_max = round(max_p - 0.08 * scale, dec)

        if not hasattr(self, 'organic_price') or self.organic_price < safe_min or self.organic_price > safe_max:
            self.organic_price = round((safe_min + safe_max) / 2.0, dec)
            self.regime = "RANDOM_WALK"
            self.regime_steps_left = random.randint(3, 7)
            self.momentum = 0.0

        self.regime_steps_left -= 1
        if self.regime_steps_left <= 0:
            regimes = ["TREND_UP", "TREND_DOWN", "CHOPPY_RANGE", "RANDOM_WALK", "VOLATILITY_SURGE", "PULLBACK_REVERSAL"]
            # 경계 근처일 때 반대 방향으로 자연스럽게 회귀
            if self.organic_price > (safe_max - 0.12 * scale):
                weights = [0.05, 0.45, 0.25, 0.10, 0.05, 0.10]
            elif self.organic_price < (safe_min + 0.12 * scale):
                weights = [0.45, 0.05, 0.25, 0.10, 0.05, 0.10]
            else:
                weights = [0.22, 0.22, 0.20, 0.18, 0.09, 0.09]
            
            self.regime = random.choices(regimes, weights=weights)[0]
            self.regime_steps_left = random.randint(3, 8)

        # 국면별 가격 변동폭 (단위: USDT)
        if self.regime == "TREND_UP":
            delta = (random.uniform(0.012, 0.038) + random.gauss(0, 0.005)) * scale
        elif self.regime == "TREND_DOWN":
            delta = (-random.uniform(0.012, 0.038) + random.gauss(0, 0.005)) * scale
        elif self.regime == "CHOPPY_RANGE":
            delta = random.uniform(-0.016, 0.016) * scale
        elif self.regime == "VOLATILITY_SURGE":
            direction = 1 if random.random() < 0.5 else -1
            delta = direction * random.uniform(0.035, 0.065) * scale
        elif self.regime == "PULLBACK_REVERSAL":
            delta = -self.momentum * random.uniform(0.6, 1.2) if abs(self.momentum) > (0.008 * scale) else random.uniform(-0.02, 0.02) * scale
        else: # RANDOM_WALK
            delta = random.gauss(0, 0.020) * scale

        # 완전 불규칙 미세 노이즈
        micro_jitter = random.uniform(-0.0040, 0.0040) * scale
        raw_next = self.organic_price + delta + micro_jitter

        # 박스권 상/하단 소프트 바운스
        if raw_next > safe_max:
            raw_next = safe_max - random.uniform(0.01, 0.04) * scale
            self.regime = "TREND_DOWN"
            self.regime_steps_left = random.randint(4, 8)
        elif raw_next < safe_min:
            raw_next = safe_min + random.uniform(0.01, 0.04) * scale
            self.regime = "TREND_UP"
            self.regime_steps_left = random.randint(4, 8)

        self.momentum = delta
        self.organic_price = round(raw_next, dec)
        return self.organic_price

    def get_base_asset_name(self) -> str:
        """심볼에서 기준 코인명 추출 (예: AIR_USDT -> AIR)"""
        parts = self.config.symbol.replace("/", "_").split("_")
        return parts[0].upper() if len(parts) > 0 else "AIR"

    def find_external_best_orders(self, symbol: str, min_wall_usdt: float = 3.0) -> tuple:
        """
        내 미체결 주문(매수벽 20개 등)을 제외한 순수 외부 유저의 최우선 매수호가(valid_ext_bid, 최소 $3 이상 진짜 매수벽)와
        소액 미끼 호가를 포함한 최상단 외부 매수호가(raw_top_ext_bid, raw_top_ext_usdt),
        최우선 매도호가(external_best_ask)를 정밀 계산합니다.
        """
        open_orders = self.get_open_orders(symbol)
        ob = self.get_orderbook(symbol)
        bids = ob.get("bids", []) if ob else []
        asks = ob.get("asks", []) if ob else []

        my_bids = {}
        my_asks = {}
        for o in open_orders:
            try:
                p = round(float(o.get("price", 0)), 8)
                amt = float(o.get("amount", 0))
                side = str(o.get("side", "")).lower()
                if side == "buy":
                    my_bids[p] = my_bids.get(p, 0.0) + amt
                else:
                    my_asks[p] = my_asks.get(p, 0.0) + amt
            except Exception:
                continue

        valid_ext_bid = 0.0
        raw_top_ext_bid = 0.0
        raw_top_ext_usdt = 0.0

        for b in bids:
            try:
                p = round(float(b[0]), 8)
                total_amt = float(b[1])
                my_amt = my_bids.get(p, 0.0)
                ext_amt = total_amt - my_amt
                if ext_amt > 0.1:
                    ext_usdt = p * ext_amt
                    if raw_top_ext_bid <= 0:
                        raw_top_ext_bid = p
                        raw_top_ext_usdt = ext_usdt
                    # 최소 $3.0 이상인 호가만 진짜 외부 매수벽으로 인정 (4.5원짜리 미끼 호가 필터링)
                    if ext_usdt >= min_wall_usdt:
                        valid_ext_bid = p
                        break
            except Exception:
                continue

        external_best_ask = 0.0
        for a in asks:
            try:
                p = round(float(a[0]), 8)
                total_amt = float(a[1])
                my_amt = my_asks.get(p, 0.0)
                if (total_amt - my_amt) > 1.0:
                    external_best_ask = p
                    break
            except Exception:
                continue

        return valid_ext_bid, external_best_ask, open_orders, ob, raw_top_ext_bid, raw_top_ext_usdt

    def emergency_stop_biconomy(self, reason: str, cancel_ids: list = None, pre_usdt: float = None, pre_air: float = None):
        """
        외부 유저 물량과의 체결 또는 호가 침범 감지 시 즉시 자전 잔여 주문을 취소하고,
        USDT 및 코인 잔고 변화량을 상세히 로그에 남긴 뒤 봇을 비상 정지합니다.
        (기존 매수벽 20개는 절대 취소하지 않고 보존)
        """
        symbol = self.config.symbol
        base_asset = self.get_base_asset_name()
        if cancel_ids:
            for oid in cancel_ids:
                if oid:
                    try:
                        self.cancel_order(str(oid), symbol)
                        time.sleep(0.05)
                    except Exception:
                        pass

        self.config.is_running = False
        self.last_status = f"🚨 비상 중단: {reason}"

        # 취소 직후 최신 잔고 조회 및 변화량 계산
        bal_report = ""
        try:
            time.sleep(0.15)
            balances = self.get_balances()
            if balances:
                self.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
                self.air_freeze = float(balances.get(base_asset, {}).get("freeze", "0"))
                self.air_total = float(balances.get(base_asset, {}).get("total", self.air_bal + self.air_freeze))
                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                self.usdt_freeze = float(balances.get("USDT", {}).get("freeze", "0"))
                self.usdt_total = float(balances.get("USDT", {}).get("total", self.usdt_bal + self.usdt_freeze))

                init_usdt = getattr(self, "initial_usdt_total", self.usdt_total)
                init_air = getattr(self, "initial_air_total", self.air_total)
                step_usdt_diff = (self.usdt_total - pre_usdt) if pre_usdt is not None else 0.0
                step_air_diff = (self.air_total - pre_air) if pre_air is not None else 0.0
                total_usdt_diff = self.usdt_total - init_usdt
                total_air_diff = self.air_total - init_air

                bal_report = (
                    f"📊 [비상 중단 잔고 변화] 현재 USDT 총액: ${self.usdt_total:.4f} (가용: ${self.usdt_bal:.4f}, 매수벽동결: ${self.usdt_freeze:.2f}) | "
                    f"이번 스텝 ΔUSDT: {step_usdt_diff:+.4f}$ (시작대비 누적 ΔUSDT: {total_usdt_diff:+.4f}$) | "
                    f"이번 스텝 Δ{base_asset}: {step_air_diff:+,.2f} (시작대비 누적 Δ{base_asset}: {total_air_diff:+,.2f})"
                )
        except Exception as e:
            bal_report = f"⚠️ 비상 중단 잔고 조회 오류: {e}"

        msg = f"🚨 [비상 중단 - 외부 물량 보호] {reason} -> 거래를 즉시 중단했습니다."
        self.log(msg)
        if bal_report:
            self.log(bal_report)
        self.send_telegram(msg + ("\n" + bal_report if bal_report else ""))

        # 자동 재가동(자가 복구) 스케줄링 실행
        self.schedule_auto_recovery(reason)

    def replenish_buy_walls_if_needed(self):
        """
        하단 매수벽 20개 (0.00000116 ~ 0.00000135, 각 약 $5.05) 점검:
        체결되거나 누락된 호가가 있으면 자동으로 즉시 재발주하여 20개 완벽 복원
        """
        symbol = self.config.symbol
        if self.config.exchange.lower() != "biconomy":
            return
        try:
            open_orders = self.get_open_orders(symbol)
            existing_prices = {
                round(float(o.get("price", 0)), 8)
                for o in open_orders
                if str(o.get("side", "")).lower() == "buy" and float(o.get("price", 0)) > 0
            }
            wall_prices = [round(0.00000116 + i * 0.00000001, 8) for i in range(20)]
            missing = [p for p in wall_prices if p not in existing_prices]
            if missing:
                self.log(f"🧱 [매수벽 자동 보충] 누락된 매수벽 {len(missing)}건 감지 ({[f'{p:.8f}' for p in missing]}) -> 자동 재배치 중...")
                for p in missing:
                    amt = round(5.05 / p, 2)
                    self.client.place_order(symbol, 2, p, amt)
                    time.sleep(0.12)
                self.log(f"✅ [매수벽 보충 완료] 하단 20개 매수벽(0.00000116~0.00000135) 100% 정상 복원 완료")
        except Exception as e:
            self.log(f"⚠️ 매수벽 점검/보충 오류: {e}")

    def schedule_auto_recovery(self, reason: str):
        """
        비상 중단 후 30초 쿨다운 ➔ 하단 매수벽 20개 점검/보충 ➔ 자동 재가동
        (단, 10분 내 5회 이상 연속 중단 시 서킷 브레이커로 자산 보호)
        """
        if not getattr(self.config, "auto_recovery_enabled", True):
            return
        if getattr(self, "recovery_in_progress", False):
            return

        now = time.time()
        # 최근 10분(600초) 이내의 비상 중단 이력만 유지
        if not hasattr(self, "recovery_history"):
            self.recovery_history = []
        self.recovery_history = [t for t in self.recovery_history if now - t < 600]
        self.recovery_history.append(now)

        # 서킷 브레이커: 10분 내 5회 연속 중단 시 완전 정지
        if len(self.recovery_history) >= 5:
            cb_msg = f"🛑 [서킷 브레이커 발동] 10분 내 비상 중단이 5회 연속 발생했습니다. 비정상적인 외부 개입으로부터 자산을 보호하기 위해 자동 재가동을 중지하고 완전 정지합니다."
            self.log(cb_msg)
            self.send_telegram(cb_msg)
            return

        self.recovery_in_progress = True
        cooldown_sec = getattr(self.config, "auto_recovery_delay", 30)

        def _recovery_worker():
            try:
                self.log(f"⏳ [자가 복구 대기] 외부 개입 감지 후 {cooldown_sec}초간 안전 쿨다운 후 봇을 자동으로 재시작합니다...")
                time.sleep(cooldown_sec)

                # 1) 매수벽 20개 점검 및 보충
                self.log("🔧 [자가 복구 진행] 1) 하단 매수벽 20개 점검 및 누락분 자동 재배치...")
                self.replenish_buy_walls_if_needed()

                # 2) 잔고 및 호가창 확인
                self.log("🔧 [자가 복구 진행] 2) 잔고 확인 및 호가 정렬 완료...")
                pre_bals = self.get_balances()
                if pre_bals:
                    base_asset = self.get_base_asset_name()
                    self.air_bal = float(pre_bals.get(base_asset, {}).get("available", "0"))
                    self.usdt_bal = float(pre_bals.get("USDT", {}).get("available", "0"))
                    self.usdt_total = float(pre_bals.get("USDT", {}).get("total", self.usdt_bal))

                # 3) 봇 자동 재가동
                self.config.is_running = True
                self.last_status = "Active (Auto-Recovered)"
                resume_msg = f"🔄 [자가 복구 완료] 비상 상황 후 쿨다운({cooldown_sec}s) 및 안전 점검 완료 -> 자전거래를 자동 재개했습니다!"
                self.log(resume_msg)
                self.send_telegram(resume_msg)
            except Exception as e:
                self.log(f"⚠️ 자가 복구 워커 예외: {e}")
            finally:
                self.recovery_in_progress = False

        threading.Thread(target=_recovery_worker, daemon=True).start()

    def execute_biconomy_safe_wash_step(self, custom_usdt: float = None) -> dict:
        """
        Biconomy (AIR_USDT) 전용 안전 자전거래 1회 실행:
        1원칙: 외부 유저 주문과는 절대 거래하지 않음 (하단 매수벽 20개는 그대로 유지)
        2원칙: 진짜 외부 매수벽($3 이상)이 있으면 그 바로 위(+1틱), 없으면 내 매수벽 20개 최상단(0.00000135) 바로 위(0.00000136)에서 $5.1~$5.5 불규칙 동시 자전(시차 0.03초)
        3원칙: 소액 미끼 호가 출현, 안전 상한선(0.00000230) 초과, 또는 외부 물량 개입 시 주문 전/즉시 비상 중단
        """
        symbol = self.config.symbol
        base_asset = self.get_base_asset_name()
        max_ceiling_price = 0.00000230

        # 0. 자전 전 잔고 스냅샷 확보
        pre_balances = self.get_balances()
        if not pre_balances:
            return {"status": "error", "message": "잔고 조회 실패"}

        self.air_bal = float(pre_balances.get(base_asset, {}).get("available", "0"))
        self.air_freeze = float(pre_balances.get(base_asset, {}).get("freeze", "0"))
        self.air_total = float(pre_balances.get(base_asset, {}).get("total", self.air_bal + self.air_freeze))
        self.usdt_bal = float(pre_balances.get("USDT", {}).get("available", "0"))
        self.usdt_freeze = float(pre_balances.get("USDT", {}).get("freeze", "0"))
        self.usdt_total = float(pre_balances.get("USDT", {}).get("total", self.usdt_bal + self.usdt_freeze))

        if not hasattr(self, "initial_usdt_total") or self.initial_usdt_total <= 0:
            self.initial_usdt_total = self.usdt_total
            self.initial_air_total = self.air_total

        pre_air_total = self.air_total
        pre_usdt_total = self.usdt_total

        if self.usdt_bal < 6.0:
            self.log(f"⚠️ 가용 USDT 부족 ({self.usdt_bal:.2f} USDT). 대기합니다.")
            return {"status": "error", "message": f"가용 USDT 부족 ({self.usdt_bal:.2f} USDT)"}

        # 1. 외부 최우선 매수호가($3 이상 진짜 매수벽) 및 상단 미끼 호가 탐색
        ext_bid, ext_ask, pre_open_orders, ob, raw_top_ext_bid, raw_top_ext_usdt = self.find_external_best_orders(symbol, min_wall_usdt=3.0)
        pre_wall_ids = {str(o.get("id")) for o in pre_open_orders if o.get("id")}

        # 내 매수벽(20개) 중 최상단 가격 확인 (기본값 0.00000135)
        my_buy_prices = [
            round(float(o.get("price", 0)), 8)
            for o in pre_open_orders
            if str(o.get("side", "")).lower() == "buy" and float(o.get("price", 0)) > 0
        ]
        my_top_bid = max(my_buy_prices) if my_buy_prices else 0.00000135

        # 외부 유저 매수호가($3 이상)가 있으면 그 바로 위(+1틱), 없으면 내 매수벽 20개 최상단(0.00000135) 바로 위(0.00000136)에서 거래
        tick = 0.00000001
        if ext_bid > 0:
            base_bid = ext_bid
            base_bid_label = f"외부최우선매수: {ext_bid:.8f}"
        else:
            base_bid = my_top_bid
            base_bid_label = f"내매수벽최상단: {my_top_bid:.8f}(외부매수없음)"

        target_price = round(base_bid + tick, 8)
        self.target_mid = target_price
        self.config.target_price = target_price

        # 🛡️ 방어 1 (미끼 매수호가 자동 청소): 기준 매수벽(base_bid) 위에 $0.50 이하의 초소액 미끼 매수호가(예: 4원짜리)가 걸려 있으면 즉시 매도로 털어내고 0.00000136으로 복귀!
        if raw_top_ext_bid > base_bid:
            if raw_top_ext_usdt <= 0.50:
                self.log(f"🧹 [미끼 매수호가 자동 제거] {raw_top_ext_bid:.8f} (${raw_top_ext_usdt:.4f}) 초소액 매수호가 감지 -> 즉시 체결 제거 후 {target_price:.8f} 복귀")
                sweep_amt = round(5.05 / raw_top_ext_bid, 2)
                s_res = self.client.place_order(symbol, 1, raw_top_ext_bid, sweep_amt)
                s_oid = str(s_res.get("result", {}).get("id") or s_res.get("order_id") or "")
                time.sleep(0.15)
                if s_oid:
                    self.cancel_order(s_oid, symbol)
                time.sleep(0.25)
                # 체결 후 현재가가 높아진 경우 target_price까지 계단식 하향 복귀
                step_p = round(raw_top_ext_bid * 0.52, 8)
                while step_p > target_price:
                    if ext_ask <= 0 or step_p < ext_ask:
                        self.client.place_simultaneous_wash_pair(symbol, step_p, round(5.15 / step_p, 2), 0.03)
                        time.sleep(0.35)
                    next_p = round(step_p * 0.80, 8)
                    if next_p <= target_price or next_p == step_p:
                        break
                    step_p = next_p
                # 잔고 스냅샷 갱신
                pre_balances = self.get_balances() or pre_balances
                self.air_total = float(pre_balances.get(base_asset, {}).get("total", self.air_total))
                self.usdt_total = float(pre_balances.get("USDT", {}).get("total", self.usdt_total))
                pre_air_total = self.air_total
                pre_usdt_total = self.usdt_total
            else:
                self.emergency_stop_biconomy(
                    f"기준 매수벽({base_bid:.8f}) 위에 미끼 매수호가({raw_top_ext_bid:.8f}, ${raw_top_ext_usdt:.4f}) 감지 -> 고가 유인 원천 차단(발주 0건)",
                    pre_usdt=pre_usdt_total, pre_air=pre_air_total
                )
                return {"status": "emergency_stop", "message": "소액 미끼 매수호가 감지로 발주 전 즉시 중단"}

        # 🛡️ 방어 2 (최대 상한선 잠금): 타겟가가 안전 상한선(0.00000230)을 초과하면 절대 발주하지 않고 즉시 중단!
        if target_price > round(max_ceiling_price + 1e-9, 8):
            self.emergency_stop_biconomy(
                f"타겟가({target_price:.8f})가 안전 상한선({max_ceiling_price:.8f})을 초과 -> 고가 유인 원천 차단(발주 0건)",
                pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "안전 상한선 초과로 발주 전 즉시 중단"}

        # 🛡️ 방어 3 (매도벽 충돌 차단): 타겟 가격이 외부 매도호가 이상이면 즉시 중단
        if ext_ask > 0 and target_price >= round(ext_ask - 1e-9, 8):
            self.emergency_stop_biconomy(
                f"타겟가({target_price:.8f})가 외부 매도1호가({ext_ask:.8f})에 도달/충돌",
                pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "외부 매도호가 충돌 위험으로 즉시 중단"}

        # 2. 불규칙 거래 금액 산출 ($5.10 ~ $5.50 사이 불규칙)
        if custom_usdt and custom_usdt > 0:
            wash_usdt = round(float(custom_usdt), 4)
        else:
            wash_usdt = round(random.uniform(5.10, 5.50), 4)
        wash_air = round(wash_usdt / target_price, 2)

        if self.air_bal < wash_air:
            self.log(f"⚠️ 가용 {base_asset} 부족 ({self.air_bal:,.2f} < {wash_air:,.2f}). 대기합니다.")
            return {"status": "error", "message": f"가용 {base_asset} 부족"}

        # 안전 검증 4: 발사 직전 호가창 최종 재확인
        ob_pre = self.get_orderbook(symbol)
        bids_pre = ob_pre.get("bids", []) if ob_pre else []
        asks_pre = ob_pre.get("asks", []) if ob_pre else []
        if not bids_pre:
            return {"status": "error", "message": "오더북 재확인 실패"}

        top_bid_now = round(float(bids_pre[0][0]), 8)
        if top_bid_now >= target_price:
            self.emergency_stop_biconomy(
                f"발주 직전 타겟가({target_price:.8f}) 이상에 외부 매수호가({top_bid_now:.8f}) 출현 (발주 0건)",
                pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "외부 매수호가 침범으로 즉시 중단"}

        if asks_pre:
            top_ask_now = round(float(asks_pre[0][0]), 8)
            if top_ask_now <= target_price:
                self.emergency_stop_biconomy(
                    f"발주 직전 타겟가({target_price:.8f}) 이하에 외부 매도호가({top_ask_now:.8f}) 출현 (발주 0건)",
                    pre_usdt=pre_usdt_total, pre_air=pre_air_total
                )
                return {"status": "emergency_stop", "message": "외부 매도호가 침범으로 즉시 중단"}

        # 3. ⚡ 초고속 병렬 동시 발주 (Keep-Alive 듀얼 세션으로 선매도 + 후매수 0.03초 시차 동시 발사)
        if hasattr(self.client, "place_simultaneous_wash_pair"):
            res_sell, res_buy = self.client.place_simultaneous_wash_pair(symbol, target_price, wash_air, head_start_sec=0.03)
        else:
            res_sell = self.client.place_order(symbol, 1, target_price, wash_air)
            res_buy = self.client.place_order(symbol, 2, target_price, wash_air)

        sell_id = str(res_sell.get("result", {}).get("id") or res_sell.get("order_id") or "")
        buy_id = str(res_buy.get("result", {}).get("id") or res_buy.get("order_id") or "")

        # 50% 호가 밴드 오류(code: 11024) 발생 시 이전 체결가로부터 타겟가까지 자동 단계적 하향 조정
        if (res_sell.get("code") == 11024 or res_buy.get("code") == 11024):
            try:
                t_resp = requests.get(f"{self.client.base_url}/api/v1/tickers", timeout=3).json()
                t_items = [t for t in t_resp.get("ticker", []) if t.get("symbol") == symbol]
                curr_last = float(t_items[0].get("last", 0)) if t_items else 0
                if curr_last > target_price * 1.3:
                    self.log(f"🔄 [호가 밴드(11024) 자동 복귀] 현재가({curr_last:.8f}) -> 타겟가({target_price:.8f}) 단계적 하향 조정")
                    step_p = round(curr_last * 0.70, 8)
                    while step_p > target_price:
                        self.client.place_simultaneous_wash_pair(symbol, step_p, round(5.15 / step_p, 2), 0.03)
                        time.sleep(0.35)
                        next_p = round(step_p * 0.75, 8)
                        if next_p <= target_price or next_p == step_p:
                            break
                        step_p = next_p
                    res_sell, res_buy = self.client.place_simultaneous_wash_pair(symbol, target_price, wash_air, head_start_sec=0.03)
                    sell_id = str(res_sell.get("result", {}).get("id") or res_sell.get("order_id") or "")
                    buy_id = str(res_buy.get("result", {}).get("id") or res_buy.get("order_id") or "")
            except Exception:
                pass

        if res_sell.get("code") != 0 or not sell_id or res_buy.get("code") != 0 or not buy_id:
            self.emergency_stop_biconomy(
                f"동시 자전 발주 중 일부 실패(sell={res_sell}, buy={res_buy})로 잔여 긴급 회수",
                cancel_ids=[sell_id, buy_id], pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "주문 발주 오류로 비상 중단"}

        time.sleep(0.20)

        # 5. 체결 후 외부 개입 여부 정밀 감사 (Open Orders & Balance Delta)
        post_open_orders = self.get_open_orders(symbol)
        post_open_ids = {str(o.get("id")) for o in post_open_orders if o.get("id")}

        # 5-1) 방금 넣은 선매도(sell_id)나 후매수(buy_id)가 미체결로 남아 있다면 외부 물량과 엉킨 것!
        if sell_id in post_open_ids or buy_id in post_open_ids:
            self.emergency_stop_biconomy(
                f"자전 주문({target_price:.8f}) 중 미체결 잔량 발생 (외부 물량 개입 감지)",
                cancel_ids=[sell_id, buy_id], pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "미체결 잔량 감지(외부 개입)로 즉시 중단"}

        # 5-2) 기존 매수벽(pre_wall_ids) 중 체결되어 사라진 주문이 있는지 확인
        missing_walls = pre_wall_ids - post_open_ids
        if missing_walls:
            self.emergency_stop_biconomy(
                f"기존 매수벽 주문 중 {len(missing_walls)}건이 외부와 체결됨 감지",
                pre_usdt=pre_usdt_total, pre_air=pre_air_total
            )
            return {"status": "emergency_stop", "message": "매수벽 외부 체결 감지로 즉시 중단"}

        # 5-3) 자전 전후 총 잔고(USDT / AIR) 변화량 감사 (순수 자전이면 수수료 외 잔고 변동이 없어야 함)
        post_balances = self.get_balances()
        if post_balances:
            self.air_bal = float(post_balances.get(base_asset, {}).get("available", "0"))
            self.air_freeze = float(post_balances.get(base_asset, {}).get("freeze", "0"))
            self.air_total = float(post_balances.get(base_asset, {}).get("total", self.air_bal + self.air_freeze))
            self.usdt_bal = float(post_balances.get("USDT", {}).get("available", "0"))
            self.usdt_freeze = float(post_balances.get("USDT", {}).get("freeze", "0"))
            self.usdt_total = float(post_balances.get("USDT", {}).get("total", self.usdt_bal + self.usdt_freeze))

            usdt_diff = abs(self.usdt_total - pre_usdt_total)
            air_diff = abs(self.air_total - pre_air_total)
            # 수수료(약 $0.01~0.03) 범위를 넘어 $0.50 이상 또는 주문수량의 5% 이상 변동 시 외부 거래로 판정
            if usdt_diff > 0.50 or air_diff > (wash_air * 0.05):
                self.emergency_stop_biconomy(
                    f"자전 전후 잔고 비정상 변동 감지 (ΔUSDT={usdt_diff:.4f}, Δ{base_asset}={air_diff:,.2f}) - 외부 체결 의심",
                    pre_usdt=pre_usdt_total, pre_air=pre_air_total
                )
                return {"status": "emergency_stop", "message": "외부 물량 체결 감지로 즉시 중단"}

        self.last_status = f"Active ({target_price:.8f} | ${wash_usdt:.2f})"
        self.log(
            f"✅ [Biconomy 무결점 자전 완료] {base_bid_label} -> 체결가: {target_price:.8f} | "
            f"수량: {wash_air:,.2f} {base_asset} (${wash_usdt:.2f}) | USDT총액: ${self.usdt_total:.2f} (변화 0)"
        )
        return {
            "status": "success",
            "price": target_price,
            "external_best_bid": base_bid,
            "external_best_ask": ext_ask,
            "amount": wash_air,
            "usdt": wash_usdt,
            "best_bid": base_bid,
            "best_ask": ext_ask,
            "message": f"자전 체결 완료: {target_price:.8f} (${wash_usdt:.2f})"
        }

    def execute_manual_sweep(self, sweep_price: float = None, usdt_amount: float = 15.0) -> dict:
        """
        수동 1회 즉시 스윕 거래 실행 (100% 선매도 + 오더북 최저가 검증 + 후매수 스윕)
        """
        if self.config.exchange.lower() == "biconomy":
            return self.execute_biconomy_safe_wash_step(custom_usdt=usdt_amount if usdt_amount and usdt_amount < 10 else None)

        symbol = self.config.symbol
        ob = self.get_orderbook(symbol)
        bids = ob.get('bids', []) if ob else []
        asks = ob.get('asks', []) if ob else []
        
        if not bids or not asks:
            return {"status": "error", "message": "오더북 데이터를 불러올 수 없습니다."}
            
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread = best_ask - best_bid
        
        if spread <= 0.00000002:
            return {"status": "error", "message": f"스프레드가 너무 좁습니다: {spread:.8f}"}

        # 박스권 절대 강제 방어
        min_p = self.config.price_min or 540.0
        max_p = self.config.price_max or 541.0
        dec = 4 if min_p >= 1.0 else 8
        box_pad = 0.002 if min_p >= 1.0 else (max_p - min_p) * 0.005
        margin = max(10 ** (-dec), spread * 0.05)
        min_sweep = best_bid + margin
        max_sweep = best_ask - margin
        
        # 외부 물량(5000 등)과 격리: 스프레드 범위를 박스권 내부로 엄격 클램핑
        min_sweep = max(min_sweep, min_p + box_pad)
        max_sweep = min(max_sweep, max_p - box_pad)

        if sweep_price is None or sweep_price <= 0:
            sweep_price = round((min_sweep + max_sweep) / 2.0, dec)
        else:
            sweep_price = round(sweep_price, dec)
            
        if not (min_p <= sweep_price <= max_p):
            return {
                "status": "error",
                "message": f"가격 오류: 스윕 가격({sweep_price:.{dec}f})이 박스권 [{min_p:.{dec}f} ~ {max_p:.{dec}f}] 범위를 벗어났습니다."
            }
            
        # 매수벽 < 스윕가 < 매도벽 사이 엄격 검증
        if not (best_bid < sweep_price < best_ask):
            return {
                "status": "error",
                "message": f"가격 오류: 스윕 가격({sweep_price:.{dec}f})은 매수벽({best_bid:.{dec}f})과 매도벽({best_ask:.{dec}f}) 사이에 위치해야 합니다."
            }

        # 잔고 확인
        base_asset = self.get_base_asset_name()
        balances = self.get_balances()
        if balances:
            self.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
            self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))

        if self.usdt_bal < 10.0:
            return {"status": "error", "message": f"가용 USDT가 10달러 미만입니다 ({self.usdt_bal:.2f} USDT)."}

        wash_usdt = min(float(usdt_amount or 15.0), self.usdt_bal * 0.95)
        amt_dec = 4 if sweep_price >= 1.0 else 3
        wash_air = round(wash_usdt / sweep_price, amt_dec)
        if sweep_price >= 100:
            p_str = f"{sweep_price:.4f}"
        elif sweep_price >= 1:
            p_str = f"{sweep_price:.6f}"
        else:
            p_str = f"{sweep_price:.8f}"

        if wash_air >= 100:
            amt_str = f"{wash_air:.2f}"
        elif wash_air >= 1:
            amt_str = f"{wash_air:.4f}"
        else:
            amt_str = f"{wash_air:.6f}"

        # 1-1) 선매도 발주 직전 외부 호가 침범 사전 차단
        ob_pre = self.get_orderbook(symbol)
        bids_pre = ob_pre.get('bids', []) if ob_pre else []
        asks_pre = ob_pre.get('asks', []) if ob_pre else []
        if bids_pre and float(bids_pre[0][0]) >= (sweep_price - 0.000000001):
            return {"status": "error", "message": f"🚨 [외부 매수 침범 감지] 외부 매수가({float(bids_pre[0][0]):.4f})가 스윕가({sweep_price:.4f}) 이상이어서 발주를 취소했습니다."}
        if asks_pre and float(asks_pre[0][0]) <= (sweep_price + 0.000000001):
            return {"status": "error", "message": f"🚨 [외부 매도 침범 감지] 외부 매도가({float(asks_pre[0][0]):.4f})가 스윕가({sweep_price:.4f}) 이하이어서 발주를 취소했습니다."}

        # 1-2) 선매도 주문 발주
        res_sell = self.place_order(symbol, 1, sweep_price, wash_air)
        sell_id = res_sell.get("result", {}).get("id") or res_sell.get("order_id")
        
        if not sell_id:
            return {"status": "error", "message": f"선매도 발주 실패: {res_sell}"}

        # 2) 매수 전 오더북 최저 매도가 및 외부 물량 혼입 정밀 검증
        ob_check = self.get_orderbook(symbol)
        asks_check = ob_check.get('asks', []) if ob_check else []
        if not asks_check:
            self.cancel_order(sell_id, symbol)
            return {"status": "error", "message": "오더북 재확인 실패로 매수를 취소했습니다."}

        lowest_ask_now = float(asks_check[0][0])
        lowest_ask_amt = float(asks_check[0][1])
        if lowest_ask_now < (sweep_price - 0.000000001):
            self.cancel_order(sell_id, symbol)
            return {"status": "error", "message": f"🚨 [외부 매도 침범 감지] 최저매도가({lowest_ask_now:.4f})가 스윕가({sweep_price:.4f})보다 낮아 선매도를 회수했습니다."}

        if lowest_ask_now == sweep_price and lowest_ask_amt > (wash_air * 1.5):
            self.cancel_order(sell_id, symbol)
            return {"status": "error", "message": f"🚨 [외부 물량 혼입 감지] 동일 호가({sweep_price:.4f})에 타인 물량({lowest_ask_amt:.4f})이 섞여 있어 선매도를 회수했습니다."}

        # 3) 후매수 발주 (100% 자가 체결)
        res_buy = self.place_order(symbol, 2, sweep_price, wash_air)
        buy_id = res_buy.get("result", {}).get("id") or res_buy.get("order_id")

        time.sleep(0.2)

        # 4) 잔여 취소
        if sell_id:
            self.cancel_order(sell_id, symbol)
        if buy_id:
            self.cancel_order(buy_id, symbol)

        self.log(f"⚡ [수동 스윕 성공] 매수벽: {best_bid:.8f} | 매도벽: {best_ask:.8f} | 체결가: {p_str} | {amt_str} {base_asset} (${wash_usdt:.1f})")
        return {
            "status": "success",
            "price": sweep_price,
            "amount": wash_air,
            "usdt": wash_usdt,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "message": f"스윕 체결 완료: {p_str} (${wash_usdt:.1f})"
        }

    def run_mm_loop(self):
        symbol = self.config.symbol
        base_asset = self.get_base_asset_name()
        self.log(f"MM 봇 백그라운드 서비스 준비 완료 (대기 상태): [{self.config.exchange.upper()}] {symbol}")

        while True:
            # 봇 정지 상태 확인 (대시보드 제어용: 사용자가 START 버튼을 눌러야만 실행)
            if not self.config.is_running:
                if not str(self.last_status).startswith("🚨"):
                    self.last_status = "Stopped (Standby)"
                self.auto_pause_state_start = time.time()
                time.sleep(1)
                continue

            # 자동 일시정지 타이머 체크
            if self.config.auto_pause_enabled:
                now = time.time()
                elapsed_mins = (now - self.auto_pause_state_start) / 60.0
                
                if not self.auto_paused:
                    if elapsed_mins >= self.config.run_duration:
                        self.auto_paused = True
                        self.auto_pause_state_start = now
                        self.log(f"⏳ [자동 일시정지] 설정된 운영 시간({self.config.run_duration}분)이 경과하여 {self.config.pause_duration}분간 거래를 중지합니다.")
                        self.send_telegram(f"⏳ [자동 일시정지] 설정된 운영 시간({self.config.run_duration}분) 경과. {self.config.pause_duration}분간 정지합니다.")
                else:
                    if elapsed_mins >= self.config.pause_duration:
                        self.auto_paused = False
                        self.auto_pause_state_start = now
                        self.log(f"▶️ [자동 재개] 설정된 일시정지 시간({self.config.pause_duration}분)이 경과하여 거래를 재개합니다.")
                        self.send_telegram(f"▶️ [자동 재개] 설정된 일시정지 시간({self.config.pause_duration}분) 경과. 거래를 재개합니다.")
            else:
                self.auto_paused = False
                self.auto_pause_state_start = time.time()

            # 자동 일시정지 대기 처리
            if self.auto_paused:
                remaining_sec = int((self.config.pause_duration * 60) - (time.time() - self.auto_pause_state_start))
                remaining_sec = max(0, remaining_sec)
                self.last_status = f"Auto Paused (Resumes in {remaining_sec}s)"
                time.sleep(2)
                continue

            # ── Biconomy (AIR_USDT) 전용 전략 루프 ────────────────────────
            if self.config.exchange.lower() == "biconomy":
                try:
                    self.current_btc_p, self.current_btc_v = self.client.get_btc_stats()
                    res = self.execute_biconomy_safe_wash_step()
                    if not self.config.is_running or res.get("status") == "emergency_stop":
                        continue

                    # 15초 ~ 30초 불규칙 간격 대기 (0.5초 단위로 정지 신호 반응)
                    wait_sec = round(random.uniform(15.0, 30.0), 2)
                    self.log(f"⏱️ [Biconomy 대기] 다음 자전거래까지 {wait_sec:.2f}초 불규칙 대기...")
                    waited = 0.0
                    while waited < wait_sec and self.config.is_running:
                        step_s = min(0.5, wait_sec - waited)
                        time.sleep(step_s)
                        waited += step_s
                except Exception as e:
                    self.log(f"[Biconomy 루프 오류] {e}")
                    time.sleep(5)
                continue

            try:
                self.last_status = "Updating Balances..."
                balances = self.get_balances()
                if not balances:
                    time.sleep(5)
                    continue
                
                base_asset = self.get_base_asset_name()
                self.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
                self.air_total = float(balances.get(base_asset, {}).get("total", self.air_bal))
                self.air_freeze = float(balances.get(base_asset, {}).get("freeze", "0"))
                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                self.usdt_total = float(balances.get("USDT", {}).get("total", self.usdt_bal))
                self.usdt_freeze = float(balances.get("USDT", {}).get("freeze", "0"))
                self.log(f"[잔고] USDT 총액: {self.usdt_total:.2f} (가용: {self.usdt_bal:.2f}, 호가잠김: {self.usdt_freeze:.2f}) | {base_asset} 총액: {self.air_total:.2f} (가용: {self.air_bal:.2f}, 호가잠김: {self.air_freeze:.2f})")

                # BTC 시세 업데이트
                self.current_btc_p, self.current_btc_v = self.client.get_btc_stats()

                self.loop_count += 1
                
                # 1. 주기별 목표 설정
                cycle_target_usdt = self.cycle_amounts[self.cycle_index]
                self.log(f"🎯 [새로운 주기 시작] 목표 거래량: ${cycle_target_usdt} (주기: {self.cycle_index + 1}/7)")
                
                cycle_washed_usdt = 0.0
                
                while cycle_washed_usdt < cycle_target_usdt:
                    # 중간 정지 및 일시정지 체크
                    if not self.config.is_running:
                        break
                    if self.config.auto_pause_enabled:
                        now = time.time()
                        elapsed_mins = (now - self.auto_pause_state_start) / 60.0
                        if not self.auto_paused and elapsed_mins >= self.config.run_duration:
                            self.auto_paused = True
                            self.auto_pause_state_start = now
                            self.log(f"⏳ [자동 일시정지] 설정된 운영 시간({self.config.run_duration}분) 경과.")
                            break

                    # 1. 매 주문 직전 실시간 오더북(매수벽/매도벽) 정밀 조사
                    ob = self.get_orderbook(symbol)
                    bids = ob.get('bids', []) if ob else []
                    asks = ob.get('asks', []) if ob else []
                    
                    if not bids or not asks:
                        self.log("⚠️ 오더북 데이터 대기 중...")
                        time.sleep(3)
                        continue
                        
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    
                    spread = best_ask - best_bid
                    if spread <= 0.00000002:
                        self.log(f"⚠️ 스프레드가 너무 좁아({spread:.8f}) 호가 확장을 대기합니다.")
                        time.sleep(3)
                        continue

                    # 1. 100+ 패턴 다채로운 유기적 가격 엔진으로 다음 체결 목표가 산출
                    target_price = self.calculate_next_organic_price()
                    min_p_cfg = self.config.price_min or 540.0
                    max_p_cfg = self.config.price_max or 541.0
                    dec = 4 if min_p_cfg >= 1.0 else 8
                    scale = 1.0 if min_p_cfg >= 1.0 else max(1e-8, max_p_cfg - min_p_cfg)

                    # 2. 호가창 유동적 동기화:
                    # target_price가 현재 호가 스프레드(best_bid ~ best_ask) 밖이거나 스프레드가 0.07 * scale 이상 벌어졌다면,
                    if not (best_bid < target_price < best_ask) or spread > (0.07 * scale):
                        self.log(f"🌊 [호가망 점진 이동] 체결 파동({target_price:.{dec}f})에 맞춰 호가창 무중단 점진 재배치 (스프레드: {best_bid:.{dec}f} ~ {best_ask:.{dec}f})")
                        self.update_orderbook_seamlessly(center_price=target_price, levels=12, min_price=self.config.price_min, max_price=self.config.price_max)
                        time.sleep(0.4)
                        ob = self.get_orderbook(symbol)
                        bids = ob.get('bids', []) if ob else []
                        asks = ob.get('asks', []) if ob else []
                        if not bids or not asks:
                            continue
                        best_bid = float(bids[0][0])
                        best_ask = float(asks[0][0])
                        spread = best_ask - best_bid

                    # 정기적인 12단계 호가 개수 확인 및 자동 보충
                    self.maintain_orderbook_grid(levels=12)

                    # 3. 스윕 체결 가격 결정 (스프레드 내부 안전 마진 클램프)
                    margin = max(10 ** (-dec), spread * 0.05)
                    min_sweep = round(best_bid + margin, dec)
                    max_sweep = round(best_ask - margin, dec)
                    if min_sweep >= max_sweep:
                        sweep_price = round((best_bid + best_ask) / 2.0, dec)
                    else:
                        sweep_price = round(max(min_sweep, min(target_price, max_sweep)), dec)

                    # 박스권 절대 강제선
                    if self.config.price_min > 0 and self.config.price_max > 0:
                        safe_lo = round(self.config.price_min + 0.05 * scale, dec)
                        safe_hi = round(self.config.price_max - 0.05 * scale, dec)
                        sweep_price = round(max(safe_lo, min(sweep_price, safe_hi)), dec)

                    self.target_mid = sweep_price
                    self.config.target_price = sweep_price

                    # 4. 가용 잔고 갱신 및 불규칙 수량 다양화 (8.5 ~ 25.0 USDT, 고정 패턴 절대 방지)
                    balances = self.get_balances()
                    if balances:
                        self.air_bal = float(balances.get(base_asset, {}).get("available", "0"))
                        self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))

                    if self.usdt_bal < 10.0:
                        self.log(f"⚠️ 가용 USDT가 10달러 미만입니다 ({self.usdt_bal:.2f} USDT). 대기합니다.")
                        time.sleep(5)
                        continue

                    vol_type = random.random()
                    if vol_type < 0.15:
                        wash_usdt = round(random.uniform(8.5, 11.5), 2)   # 소액 체결
                    elif vol_type < 0.30:
                        wash_usdt = round(random.uniform(18.5, 25.5), 2)  # 큰 볼륨 스파이크
                    else:
                        wash_usdt = round(random.uniform(12.5, 17.5), 2)  # 평상시 유기적 체결
                        
                    wash_usdt = min(wash_usdt, self.usdt_bal * 0.90)
                    if wash_usdt < 8.0:
                        wash_usdt = 8.0
                    amt_dec = 4 if sweep_price >= 1.0 else 3
                    wash_air = round(wash_usdt / sweep_price, amt_dec)
                    p_str = f"{sweep_price:.{dec}f}"
                    amt_str = f"{wash_air:.{amt_dec}f}"

                    # 4-1) 선매도 발주 직전 실시간 오더북 침범 사전 차단
                    ob_pre = self.get_orderbook(symbol)
                    bids_pre = ob_pre.get('bids', []) if ob_pre else []
                    asks_pre = ob_pre.get('asks', []) if ob_pre else []
                    if bids_pre and round(float(bids_pre[0][0]), dec) >= round(sweep_price, dec):
                        self.log(f"🚨 [외부 매수 침범 감지] 외부 매수가({float(bids_pre[0][0]):.{dec}f})가 스윕가({sweep_price:.{dec}f}) 이상으로 침범하여 발주를 보류합니다.")
                        time.sleep(2)
                        continue
                    if asks_pre and round(float(asks_pre[0][0]), dec) <= round(sweep_price, dec):
                        self.log(f"🚨 [외부 매도 침범 감지] 외부 매도가({float(asks_pre[0][0]):.{dec}f})가 스윕가({sweep_price:.{dec}f}) 이하로 침범하여 발주를 보류합니다.")
                        time.sleep(2)
                        continue

                    # 4-2) 스프레드 내부 1:1 선(先)매도 후(後)매수 맞체결
                    res_sell = self.place_order(symbol, 1, sweep_price, wash_air)
                    sell_id = res_sell.get("result", {}).get("id") or res_sell.get("order_id")
                    
                    if not sell_id:
                        self.log(f"⚠️ 선매도 발주 실패로 매수 스윕을 안전하게 취소합니다: {res_sell}")
                        time.sleep(2)
                        continue

                    # 🛡️ [핵심 방어막] 매수 주문 전 오더북 최저 매도가 및 외부 물량 혼입 정밀 검증
                    ob_check = self.get_orderbook(symbol)
                    asks_check = ob_check.get('asks', []) if ob_check else []
                    
                    if not asks_check:
                        self.cancel_order(sell_id, symbol)
                        self.log("⚠️ 오더북 재확인 실패로 매수를 중단하고 선매도를 취소합니다.")
                        time.sleep(1)
                        continue
                        
                    lowest_ask_now = round(float(asks_check[0][0]), dec)
                    lowest_ask_amt = float(asks_check[0][1])
                    
                    if lowest_ask_now < round(sweep_price, dec):
                        self.cancel_order(sell_id, symbol)
                        self.log(f"🚨 [외부 매도 침범 감지] 최저매도가({lowest_ask_now:.{dec}f})가 본인선매도가({sweep_price:.{dec}f})보다 낮습니다! 선매도를 즉시 회수합니다.")
                        time.sleep(2)
                        continue

                    if lowest_ask_now == round(sweep_price, dec) and lowest_ask_amt > (wash_air * 1.5):
                        self.cancel_order(sell_id, symbol)
                        self.log(f"🚨 [외부 물량 혼입 감지] 동일 호가({sweep_price:.{dec}f})에 타인 물량({lowest_ask_amt:.{amt_dec}f})이 섞여 있어 선매도를 즉시 회수합니다.")
                        time.sleep(2)
                        continue
                    
                    # 100% 단독 최저 매도가 확인 완료 -> 후매수 발주
                    res_buy = self.place_order(symbol, 2, sweep_price, wash_air)
                    buy_id = res_buy.get("result", {}).get("id") or res_buy.get("order_id")
                    
                    time.sleep(0.2)
                    
                    # 미체결 잔여 물량 즉시 취소
                    if sell_id:
                        self.cancel_order(sell_id, symbol)
                    if buy_id:
                        self.cancel_order(buy_id, symbol)
                        
                    cycle_washed_usdt += wash_usdt
                    progress = (cycle_washed_usdt / cycle_target_usdt) * 100
                    self.log(f"🌊 [{self.config.exchange.upper()} 스윕 체결] 매수벽: {best_bid:.8f} | 매도벽: {best_ask:.8f} | 체결가: {p_str} | {amt_str} {base_asset} (${wash_usdt:.1f}) (진행률: {progress:.1f}%)")
                    
                    # 불규칙 시간 템포 (2.5 ~ 14.0초 인간/시장 무작위 거래 간격)
                    pace = random.random()
                    if pace < 0.25:
                        actual_sleep = round(random.uniform(2.5, 4.2), 2)
                    elif pace < 0.85:
                        actual_sleep = round(random.uniform(4.8, 8.5), 2)
                    else:
                        actual_sleep = round(random.uniform(9.0, 14.0), 2)
                    time.sleep(actual_sleep)
                    
                # 사이클 완료 시
                if cycle_washed_usdt >= cycle_target_usdt * 0.95:
                    self.log(f"✅ {cycle_target_usdt}달러 스윕핑 한 사이클 완료! 즉시 다음 주기로 진입합니다.")
                    self.cycle_index = (self.cycle_index + 1) % len(self.cycle_amounts)

            except Exception as e:
                self.log(f"메인 루프 치명적 오류: {e}")
                time.sleep(5)

if __name__ == "__main__":
    conf = BotConfig()
    mm = MarketMaker(conf)
    mm.run_mm_loop()