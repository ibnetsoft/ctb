import hashlib
import time
import requests
import random
import math
import os
import json
from datetime import datetime

# ── 초기 설정 ───────────────────────────────────
# ── 초기 설정 ───────────────────────────────────
class BotConfig:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.api_key = ""
        self.secret_key = ""
        self.telegram_token = ""
        self.telegram_chat_id = ""
        self.telegram_enabled = False
        
        self.base_url = "https://api.biconomy.com"
        self.symbol = "AIR_USDT"
        self.beta = 3.0
        self.interval = 5
        self.is_running = True
        self.anchor_reset_interval = 120
        self.logs = []
        self.target_price = 0.00001035
        self.micro_range_enabled = True
        self.micro_gap = 0.00000005
        self.micro_width = 0.00000040
        self.price_oscillation_enabled = False
        self.price_min = 0.00001
        self.price_max = 0.00002
        self.price_step = 0.0000005
        self.auto_pause_enabled = False
        self.run_duration = 120
        self.pause_duration = 30
        
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
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
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

    def save_config(self):
        data = {
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
        except Exception as e:
            print(f"설정 파일 저장 실패: {e}")

class MarketMaker:
    def __init__(self, config: BotConfig):
        self.config = config
        self.target_mid = 0.0
        self.initial_air_price = 0.0
        self.initial_btc_price = 0.0
        self.initial_btc_vol = 0.0
        self.vol_ratio = 1.0
        self.loop_count = 0
        self.current_btc_p = 0.0
        self.current_btc_v = 0.0
        self.usdt_bal = 0.0
        self.air_bal = 0.0
        self.last_status = "Initializing..."
        self.active_wash_buy_id = None
        self.last_grid_target_price = 0.0
        self.current_organic_price = 0.0
        
        self.price_step_direction = 1 # 1: UP, -1: DOWN
        # 0.00000990 부터 0.00000850 까지 15단계 바닥 매수벽 가격 리스트
        self.floor_bid_prices = [round(0.00000990 - i * 0.00000010, 8) for i in range(15)]
        
        self.auto_paused = False
        self.auto_pause_state_start = time.time()
        
        # 알림용 상태 변수
        self.is_disconnected = False
        self.disconnect_time = 0
        self.last_error_msg = ""
        self.last_buy_placed_time = 0
        
        # 7주기 거래 금액 패턴
        self.cycle_amounts = [1000.0, 2000.0, 3000.0, 1000.0, 3000.0, 5000.0, 2000.0]
        self.cycle_index = 0

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
            "text": f"[MM Bot] {message}",
            "parse_mode": "HTML"
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"텔레그램 전송 실패: {e}")

    # ── 서명 생성 ──────────────────────────────────────
    def make_sign(self, params: dict) -> str:
        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)
        query += f"&secret_key={self.config.secret_key}"
        return hashlib.md5(query.encode()).hexdigest().upper()

    # ── Private API 호출 ───────────────────────────────
    def private_post(self, endpoint: str, params: dict) -> dict:
        params["api_key"] = self.config.api_key
        params["sign"] = self.make_sign(params)
        headers = {"X-SITE-ID": "127"}
        try:
            res = requests.post(self.config.base_url + endpoint, data=params, headers=headers, timeout=10)
            result = res.json()
            if result.get("code") != 0:
                # 10번 코드는 'Order not found'로, 이미 취소/체결된 경우이므로 로그 생략
                if result.get("code") != 10:
                    self.log(f"API 오류 [{endpoint}]: {result}")
            return result
        except Exception as e:
            self.log(f"네트워크 오류 [{endpoint}]: {e}")
            return {}

    def get_orderbook(self, symbol: str) -> dict:
        try:
            res = requests.get(f"{self.config.base_url}/api/v1/depth", params={"symbol": symbol}, timeout=10)
            return res.json()
        except:
            return {}

    def get_biconomy_btc_stats(self) -> tuple:
        try:
            res = requests.get(f"{self.config.base_url}/api/v1/tickers", timeout=10)
            data = res.json()
            tickers = data.get("ticker", [])
            for t in tickers:
                if t.get("symbol") == "BTC_USDT":
                    # last: 현재가, vol: 24h 거래량
                    return float(t.get("last", 0)), float(t.get("vol", 0))
        except Exception as e:
            self.log(f"Biconomy BTC 통계 조회 실패: {e}")
        return 0.0, 0.0

    def get_btc_volume_sensitivity(self, symbol="BTC_USDT", size=20):
        try:
            url = f"{self.config.base_url}/api/v1/kline"
            params = {"symbol": symbol, "type": "1min", "size": size}
            res = requests.get(url, params=params, timeout=10)
            klines = res.json()
            if not isinstance(klines, list) or len(klines) < 10:
                return 1.0

            vols = [float(k[5]) for k in klines]
            current_vol = vols[-1]
            avg_vol = sum(vols[:-1]) / (len(vols) - 1)
            rvol = current_vol / avg_vol if avg_vol > 0 else 1.0

            highs = [float(k[2]) for k in klines]
            lows  = [float(k[3]) for k in klines]
            price_range = (max(highs) - min(lows)) / min(lows) if min(lows) > 0 else 0

            # 급등락 구간: sqrt 제거 → 선형 반응으로 스파이크에 민감하게
            if rvol >= 1.0:
                vol_component = (rvol - 1.0) * 2.5
            else:
                vol_component = (rvol - 1.0) * 1.0

            p_boost = price_range * 250.0  # 가격 변동성 가중치 강화
            sensitivity_ratio = 1.0 + vol_component + p_boost

            # 최대 배율 20x, 최소 0.3x (평탄 구간엔 최소 유지)
            final_ratio = max(0.3, min(sensitivity_ratio, 20.0))
            self.vol_ratio = final_ratio
            return final_ratio
        except Exception as e:
            self.log(f"BTC volume sensitivity 계산 실패: {e}")
            return 1.0

    def get_balances(self) -> dict:
        res = self.private_post("/api/v1/private/user", {})
        if res.get("code") == 0:
            return res.get("result", {})
        return {}

    def get_open_orders(self, symbol: str) -> list:
        all_orders = []
        offset = 0
        limit = 100
        while True:
            res = self.private_post("/api/v1/private/order/pending", {"market": symbol, "offset": offset, "limit": limit})
            if res.get("code") == 0:
                result_obj = res.get("result", {})
                if isinstance(result_obj, dict):
                    records = result_obj.get("records", [])
                    if not records:
                        break
                    all_orders.extend(records)
                    if len(records) < limit:
                        break
                    offset += limit
                else:
                    break
            else:
                break
        return all_orders

    def cancel_order(self, order_id: str, symbol: str):
        return self.private_post("/api/v1/private/trade/cancel", {"order_id": order_id, "market": symbol})

    def place_order(self, symbol: str, side: int, price: float, amount: float):
        # side: 1=ASK(매도), 2=BID(매수)
        res = self.private_post("/api/v1/private/trade/limit", {
            "market": symbol,
            "side": side,
            "price": "{:.8f}".format(round(price, 8)),   
            "amount": "{:.3f}".format(round(amount, 3)), 
        })
        if res.get("code") == 0:
            side_str = "매수" if side == 2 else "매도"
            self.log(f"{side_str} 성공: {price:.8f} | 수량: {amount}")
        return res

    def maintain_floor_bids(self, symbol):
        """
        외부 매도벽보다 최소 15% 이상 안전하게 떨어진 깊은 바닥 가격(0.00000850 이하)에만
        바닥 매수벽을 설치하여 외부 일반 매도세에 절대 긁히지 않도록 방어합니다.
        """
        ob = self.get_orderbook(symbol)
        asks = ob.get('asks', []) if ob else []
        lowest_ask = float(asks[0][0]) if asks else 0.00001000
        safe_floor_ceiling = min(0.00000850, lowest_ask * 0.85)

        open_orders = self.get_open_orders(symbol)
        open_buy_orders = [o for o in open_orders if o.get('side') == 2 or o.get('side') == '2']
        
        # 현재 걸려있는 매수 주문 가격들 (소수점 8자리 기준 비교)
        existing_buy_prices = set(round(float(o.get('price', 0)), 8) for o in open_buy_orders)
        
        safe_floor_prices = [p for p in self.floor_bid_prices if p <= safe_floor_ceiling]
        missing_floor_prices = [p for p in safe_floor_prices if p not in existing_buy_prices]
        
        if missing_floor_prices:
            missing_floor_prices.sort(reverse=True)
            floor_order_usdt = 10.5
            wash_reserve_usdt = 35.0  # 자전거래 연료로 남겨둘 최소 가용 USDT
            
            for p in missing_floor_prices:
                # 주문 전 잔고 확인: 자전거래용 예비금 + 주문금액 필요
                balances = self.get_balances()
                if balances:
                    self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                    self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                    
                if self.usdt_bal < (wash_reserve_usdt + floor_order_usdt):
                    # 가용 자전거래 자금을 보존하기 위해 하위 바닥벽 설치는 보류
                    break
                    
                amt = round(floor_order_usdt / p, 3)
                p_str = "{:.8f}".format(p)
                amt_str = "{:.3f}".format(amt)
                
                res = self.private_post("/api/v1/private/trade/limit", {
                    "market": symbol,
                    "side": 2, # Buy
                    "price": p_str,
                    "amount": amt_str
                })
                if res.get("code") == 0:
                    self.log(f"🛡️ [안전 깊은 바닥 매수벽] 설치: {p_str} | {amt_str} AIR (${floor_order_usdt:.1f})")
                time.sleep(0.15)

    def maintain_grid(self, symbol):
        # 1. 외부 매도벽과 충분히 떨어진 안전 깊은 바닥 매수벽만 점검 및 유지
        self.maintain_floor_bids(symbol)
        
        open_orders = self.get_open_orders(symbol)
        floor_prices_set = set(self.floor_bid_prices)
        
        # 2. 매도벽(Asks) 정리
        sell_orders = [o for o in open_orders if o.get('side') == 1 or o.get('side') == '1']
        if sell_orders:
            self.log(f"🧹 [매도벽 미생성 정책] 기존 봇 매도 주문 {len(sell_orders)}개를 정리합니다.")
            for o in sell_orders:
                self.cancel_order(o["id"], symbol)
            time.sleep(0.3)
            
        # 3. 외부 매도세 침범 위험이 있는 상단 임의 매수 주문은 취소하여 USDT를 100% 안전하게 보호
        near_buy_orders = [o for o in open_orders if (o.get('side') == 2 or o.get('side') == '2') and o["id"] != self.active_wash_buy_id and round(float(o.get('price', 0)), 8) not in floor_prices_set]
        if near_buy_orders:
            self.log(f"🛡️ [USDT 보호] 시장가 인근 위험 매수 주문 {len(near_buy_orders)}개를 취소하여 외부 매도 침범을 방어합니다.")
            for o in near_buy_orders:
                self.cancel_order(o["id"], symbol)
            time.sleep(0.3)

    def place_new_grid(self, symbol):
        # 시장가 인근에는 노출 매수벽을 세우지 않고 1:1 원자적 즉시 맞체결로만 거래합니다.
        pass

    def run_mm_loop(self):
        symbol = self.config.symbol
        self.log(f"MM 봇 메인 루프 가동: {symbol}")
        
        # 초기 가격 설정 (USDT 고정)
        self.target_mid = self.config.target_price
        self.initial_air_price = self.config.target_price
        while self.initial_btc_price == 0.0:
            self.initial_btc_price, self.initial_btc_vol = self.get_biconomy_btc_stats()
            if self.initial_btc_price == 0.0: time.sleep(2)
        
        self.log(f"초기 설정 완료 - AIR: {self.initial_air_price}, BTC: {self.initial_btc_price}")

        while True:
            # 봇 정지 상태 확인 (대시보드 제어용)
            if not self.config.is_running:
                self.last_status = "Stopped (Standby)"
                self.auto_pause_state_start = time.time()
                time.sleep(2)
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

            try:
                self.last_status = "Updating Balances..."
                balances = self.get_balances()
                if not balances:
                    time.sleep(5); continue
                
                self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                self.log(f"[잔고] 가용 USDT: {self.usdt_bal:.4f} | 가용 AIR: {self.air_bal:.2f}")

                self.loop_count += 1
                
                # 1. 주기별 목표 설정
                cycle_target_usdt = self.cycle_amounts[self.cycle_index]
                self.log(f"🎯 [새로운 주기 시작] 목표 거래량: ${cycle_target_usdt} (주기: {self.cycle_index + 1}/7)")
                
                cycle_washed_usdt = 0.0
                immediate_retry = False
                
                while cycle_washed_usdt < cycle_target_usdt:
                    # 자동 일시정지 타이머 체크 (중간 루프 탈출)
                    if self.config.auto_pause_enabled:
                        now = time.time()
                        elapsed_mins = (now - self.auto_pause_state_start) / 60.0
                        if not self.auto_paused and elapsed_mins >= self.config.run_duration:
                            self.auto_paused = True
                            self.auto_pause_state_start = now
                            self.log(f"⏳ [자동 일시정지] 설정된 운영 시간({self.config.run_duration}분)이 경과하여 {self.config.pause_duration}분간 거래를 중지합니다.")
                            self.send_telegram(f"⏳ [자동 일시정지] 설정된 운영 시간({self.config.run_duration}분) 경과. {self.config.pause_duration}분간 정지합니다.")
                            break

                    # 최저 매도호가 하단 소수점 7째자리 미세구간 유기적 가격 결정
                    ob = self.get_orderbook(symbol)
                    fresh_asks = ob.get('asks', []) if ob else []
                    
                    if self.config.micro_range_enabled:
                        # 1. 호가창의 최저 매도호가 확인
                        if fresh_asks:
                            lowest_ask = float(fresh_asks[0][0])
                        else:
                            lowest_ask = max(0.00001050, self.config.target_price + 0.00000050)
                            
                        # 2. 거래 상한선: 최저 매도호가 바로 아래 (매도벽을 긁지 않도록 고정)
                        ceiling_price = round(lowest_ask - self.config.micro_gap, 8)
                        # 3. 거래 하한선: 상한선 대비 소수점 7째자리 작은 폭 아래 (바닥벽 0.00001005 이상 보장)
                        floor_price = round(max(0.00001005, ceiling_price - self.config.micro_width), 8)
                        
                        if ceiling_price <= floor_price:
                            ceiling_price = round(floor_price + 0.00000020, 8)
                            
                        # 4. 유기적 미세 랜덤워크 (Mean-Reverting Micro-Jitter)
                    # 1. 외부 최저 매도호가 실시간 탐지 및 하향 추적
                    ob = self.get_orderbook(symbol)
                    fresh_asks = ob.get('asks', []) if ob else []
                    
                    if fresh_asks:
                        lowest_ask = float(fresh_asks[0][0])
                    else:
                        lowest_ask = 0.00001000
                        
                    # 외부 최저 매도벽보다 항상 micro_gap(기본 0.00000005) 아래 가격으로 결정
                    # 외부 매도세가 낮아지면 자동으로 target_price도 하향 추적하여 밑에서만 거래
                    target_price = round(lowest_ask - self.config.micro_gap, 8)
                    if target_price <= 0.00000100:
                        target_price = round(lowest_ask * 0.999, 8)
                        
                    self.config.target_price = target_price
                    self.target_mid = target_price
                    self.log(f"🎯 [외부 매도벽 하단 추적] 외부최저매도: {lowest_ask:.8f} | 자전거래가: {target_price:.8f}")

                    # 2. 매수벽 유지 (매도벽은 일체 생성 안 함)
                    self.maintain_grid(symbol)

                    # 3. 가용 잔고 최신 정보로 갱신
                    balances = self.get_balances()
                    if balances:
                        self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                        self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))

                    # 4. USDT 부족 시 자동 연료 충전 (자가 치유)
                    floor_prices_set = set(self.floor_bid_prices)
                    if self.usdt_bal < 10.5:
                        self.log("⚠️ 가용 USDT가 10.5달러 미만이어 자전거래 연료를 긴급 확보합니다.")
                        existing_orders = self.get_open_orders(symbol)
                        
                        # A. 상단 매수 주문 먼저 취소
                        buy_orders = [o for o in existing_orders if (o.get('side') == 2 or o.get('side') == '2') and round(float(o.get('price', 0)), 8) not in floor_prices_set]
                        for o in buy_orders:
                            self.cancel_order(o["id"], symbol)
                            time.sleep(0.1)
                            
                        # B. 최하단 바닥 매수벽 1~3개 임시 회수
                        balances = self.get_balances()
                        self.usdt_bal = float(balances.get("USDT", {}).get("available", "0")) if balances else self.usdt_bal
                        if self.usdt_bal < 10.5:
                            floor_orders = [o for o in existing_orders if (o.get('side') == 2 or o.get('side') == '2') and round(float(o.get('price', 0)), 8) in floor_prices_set]
                            floor_orders.sort(key=lambda x: float(x.get('price', 0))) # 0.00000850부터
                            for fo in floor_orders:
                                self.cancel_order(fo["id"], symbol)
                                time.sleep(0.15)
                                balances = self.get_balances()
                                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0")) if balances else self.usdt_bal
                                if self.usdt_bal >= 30.0:
                                    break
                                    
                        time.sleep(0.5)
                        balances = self.get_balances()
                        if balances:
                            self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                            self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                            self.log(f"🔄 잔고 갱신 완료: 가용 USDT: {self.usdt_bal:.4f} | 가용 AIR: {self.air_bal:.2f}")

                    if self.usdt_bal < 10.0:
                        self.log("⚠️ 가용 USDT가 10달러 미만입니다. 최소 주문 가능 금액 확보를 대기합니다.")
                        time.sleep(5)
                        continue

                    # 5. 1:1 원자적(Atomic) 즉시 맞체결 자전거래 (15 USDT 단위)
                    # [외부 매도물량 매수 방지 핵심]:
                    # 1) target_price는 무조건 외부 최저 매도호가(lowest_ask)보다 엄격히 낮게 제한
                    # 2) 본인 매도(Sell)를 먼저 호가창(lowest_ask 아래)에 올리고, 0.03초 후 본인 매수(Buy)로 타격
                    # 3) 외부 매도벽(lowest_ask 이상)과는 가격이 달라 절대 체결될 수 없으며, 본인 매도물량만 100% 매수됨
                    target_price = min(target_price, round(lowest_ask - max(0.00000002, self.config.micro_gap), 8))
                    
                    wash_usdt = min(15.0, max(10.5, self.usdt_bal * 0.90))
                    wash_air = round(wash_usdt / target_price, 3)
                    
                    p_str = "{:.8f}".format(target_price)
                    amt_str = "{:.3f}".format(wash_air)
                    
                    # 1) 본인 매도 주문을 먼저 전송하여 lowest_ask 아래에 최우선 매도호가 생성
                    res_sell = self.private_post("/api/v1/private/trade/limit", {
                        "market": symbol,
                        "side": 1, # Sell
                        "price": p_str,
                        "amount": amt_str
                    })
                    sell_id = res_sell.get("result", {}).get("id") if res_sell else None
                    
                    # 2) 0.03초 내에 동일 가격 및 수량으로 본인 매수 주문 전송 -> 방금 올린 본인 매도와 100% 즉시 체결
                    time.sleep(0.03)
                    res_buy = self.private_post("/api/v1/private/trade/limit", {
                        "market": symbol,
                        "side": 2, # Buy
                        "price": p_str,
                        "amount": amt_str
                    })
                    buy_id = res_buy.get("result", {}).get("id") if res_buy else None
                    
                    time.sleep(0.3)
                    
                    # 3) 미체결 잔여 물량 즉시 취소 (외부인이 긁어가는 것 방지)
                    if sell_id:
                        self.cancel_order(sell_id, symbol)
                    if buy_id:
                        self.cancel_order(buy_id, symbol)
                        
                    cycle_washed_usdt += wash_usdt
                    progress = (cycle_washed_usdt / cycle_target_usdt) * 100
                    self.log(f"⚡ [1:1 즉시 자전거래] {p_str} 에 {amt_str} AIR (${wash_usdt:.1f}) 100% 맞체결 완료 (진행률: {progress:.1f}%)")
                    
                    # 다음 틱 대기 (2~4초)
                    time.sleep(random.uniform(2.0, 4.0))
                    
                # 7. 성공적으로 주기 목표(예: 1000달러)를 마쳤다면 휴식 없이 바로 다음 주기로 넘어감
                if cycle_washed_usdt >= cycle_target_usdt * 0.95:
                    self.log(f"✅ {cycle_target_usdt}달러 자전거래 한 사이클 완료! 휴식 없이 즉시 다음 주기로 진입합니다.")
                    self.cycle_index = (self.cycle_index + 1) % len(self.cycle_amounts)

            except Exception as e:
                self.log(f"메인 루프 치명적 오류: {e}")
                
                # 인터넷 끊김 감지 및 알림 로직
                if not self.is_disconnected:
                    self.is_disconnected = True
                    self.disconnect_time = time.time()
                    self.log("⚠️ 연결 끊김 감지 (재시도 중...)")
                
                time.sleep(5)

            # 인터넷 복구 시 알림
            if self.is_disconnected and self.target_mid > 0:
                duration = int(time.time() - self.disconnect_time)
                msg = f"✅ 연결 복구 및 거래 재개 (중단 시간: 약 {duration}초)"
                self.log(msg)
                self.send_telegram(msg)
                self.is_disconnected = False
                self.disconnect_time = 0

            # 설정된 interval에 따라 수동 대기
            actual_sleep = self.config.interval * random.uniform(0.7, 1.3)
            time.sleep(actual_sleep)

if __name__ == "__main__":
    # mmbot.py 단독 실행 시 (구형 방식 대응)
    conf = BotConfig()
    mm = MarketMaker(conf)
    mm.run_mm_loop()