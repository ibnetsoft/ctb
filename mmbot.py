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

    def cancel_all_orders(self, symbol: str):
        orders = self.get_open_orders(symbol)
        if orders:
            self.log(f"🧹 [클린업] 기존 미체결 주문 {len(orders)}개를 모두 취소합니다.")
            for o in orders:
                self.cancel_order(o["id"], symbol)
            time.sleep(0.3)
        return len(orders) if orders else 0

    def execute_manual_sweep(self, sweep_price: float = None, usdt_amount: float = 15.0) -> dict:
        """
        수동 1회 즉시 스윕 거래 실행 (100% 선매도 + 오더북 최저가 검증 + 후매수 스윕)
        """
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

        margin = max(0.00000002, spread * 0.04)
        min_sweep = round(best_bid + margin, 8)
        max_sweep = round(best_ask - margin, 8)
        
        if sweep_price is None or sweep_price <= 0:
            sweep_price = round((min_sweep + max_sweep) / 2.0, 8)
        else:
            sweep_price = round(sweep_price, 8)
            
        # ⚠️ 매수벽 < 스윕가 < 매도벽 사이 엄격 검증
        if not (best_bid < sweep_price < best_ask):
            return {
                "status": "error",
                "message": f"가격 오류: 스윕 가격({sweep_price:.8f})은 매수벽({best_bid:.8f})과 매도벽({best_ask:.8f}) 사이에 위치해야 합니다."
            }

        # 잔고 확인
        balances = self.get_balances()
        if balances:
            self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
            self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))

        if self.usdt_bal < 10.0:
            return {"status": "error", "message": f"가용 USDT가 10달러 미만입니다 ({self.usdt_bal:.2f} USDT)."}

        wash_usdt = max(10.0, min(float(usdt_amount), self.usdt_bal * 0.95))
        wash_air = round(wash_usdt / sweep_price, 3)
        p_str = "{:.8f}".format(sweep_price)
        amt_str = "{:.3f}".format(wash_air)

        # 1) 선매도 주문 발주
        res_sell = self.private_post("/api/v1/private/trade/limit", {
            "market": symbol,
            "side": 1,
            "price": p_str,
            "amount": amt_str
        })
        sell_id = res_sell.get("result", {}).get("id") if (res_sell and res_sell.get("code") == 0) else None
        
        if not sell_id:
            return {"status": "error", "message": f"선매도 발주 실패: {res_sell}"}

        # 2) 🛡️ 매수 전 오더북 최저 매도가 단독 검증
        ob_check = self.get_orderbook(symbol)
        asks_check = ob_check.get('asks', []) if ob_check else []
        if not asks_check:
            self.cancel_order(sell_id, symbol)
            return {"status": "error", "message": "오더북 재확인 실패로 매수를 취소했습니다."}

        lowest_ask_now = float(asks_check[0][0])
        if lowest_ask_now < (sweep_price - 0.000000001):
            self.cancel_order(sell_id, symbol)
            return {"status": "error", "message": f"외부 매도 침범 감지: 최저매도가({lowest_ask_now:.8f})가 스윕가보다 낮아 취소했습니다."}

        # 3) 후매수 발주 (100% 자가 체결)
        res_buy = self.private_post("/api/v1/private/trade/limit", {
            "market": symbol,
            "side": 2,
            "price": p_str,
            "amount": amt_str
        })
        buy_id = res_buy.get("result", {}).get("id") if (res_buy and res_buy.get("code") == 0) else None

        time.sleep(0.2)

        # 4) 잔여 취소
        if sell_id:
            self.cancel_order(sell_id, symbol)
        if buy_id:
            self.cancel_order(buy_id, symbol)

        self.log(f"⚡ [수동 스윕 성공] 매수벽: {best_bid:.8f} | 매도벽: {best_ask:.8f} | 체결가: {p_str} | {amt_str} AIR (${wash_usdt:.1f})")
        return {
            "status": "success",
            "price": sweep_price,
            "amount": wash_air,
            "usdt": wash_usdt,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "message": f"스윕 체결 완료: {p_str} (${wash_usdt:.1f})"
        }

    def maintain_floor_bids(self, symbol):
        # 호가창에 노출 주문을 남기지 않고 스프레드 내부 스윕핑만 실행합니다.
        pass

    def maintain_grid(self, symbol):
        # 호가창에 노출 주문을 남기지 않고 스프레드 내부 스윕핑만 실행합니다.
        pass

    def place_new_grid(self, symbol):
        pass

    def run_mm_loop(self):
        symbol = self.config.symbol
        self.log(f"MM 봇 스프레드 스윕핑 루프 가동: {symbol}")
        
        # 시작 시 혹시 남아있는 모든 미체결 주문 정리 (호가창 클린 유지)
        self.cancel_all_orders(symbol)

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
                        
                    best_bid = float(bids[0][0]) # 외부 최고 매수가 (매수벽 상단)
                    best_ask = float(asks[0][0]) # 외부 최저 매도가 (매도벽 하단)
                    
                    spread = best_ask - best_bid
                    if spread <= 0.00000002:
                        self.log(f"⚠️ 스프레드가 너무 좁아({spread:.8f}) 호가 확장을 대기합니다.")
                        time.sleep(3)
                        continue

                    # 매수벽과 매도벽 사이에 안전 마진(이격)을 둔 스윕 가능 구간 산출
                    margin = max(0.00000002, spread * 0.04)
                    min_sweep = round(best_bid + margin, 8)
                    max_sweep = round(best_ask - margin, 8)
                    
                    if min_sweep >= max_sweep:
                        min_sweep = round((best_bid + best_ask) / 2.0, 8)
                        max_sweep = min_sweep

                    # 2. 매수/매도 호가 사이에서 다이나믹 스윕핑 가격 결정
                    if not hasattr(self, 'current_sweep_price') or self.current_sweep_price < min_sweep or self.current_sweep_price > max_sweep:
                        self.current_sweep_price = round((min_sweep + max_sweep) / 2.0, 8)
                        self.sweep_direction = 1
                        
                    step = round((max_sweep - min_sweep) * random.uniform(0.06, 0.18), 8)
                    if step <= 0.00000001:
                        step = 0.00000001
                        
                    next_price = self.current_sweep_price + (self.sweep_direction * step)
                    if next_price >= max_sweep:
                        next_price = max_sweep
                        self.sweep_direction = -1
                    elif next_price <= min_sweep:
                        next_price = min_sweep
                        self.sweep_direction = 1
                        
                    self.current_sweep_price = round(next_price, 8)
                    sweep_price = self.current_sweep_price

                    # ⚠️ 이중 방어 검증: 산출된 스윕 가격이 매수벽과 매도벽 사이에 엄격히 위치하는지 확인
                    if not (best_bid < sweep_price < best_ask):
                        sweep_price = round((best_bid + best_ask) / 2.0, 8)
                        self.current_sweep_price = sweep_price

                    self.target_mid = sweep_price
                    self.config.target_price = sweep_price

                    # 3. 가용 잔고 갱신 및 주문 크기 계산 (12~16 USDT)
                    balances = self.get_balances()
                    if balances:
                        self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                        self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))

                    if self.usdt_bal < 10.0:
                        self.log(f"⚠️ 가용 USDT가 10달러 미만입니다 ({self.usdt_bal:.2f} USDT). 대기합니다.")
                        time.sleep(5)
                        continue

                    wash_usdt = round(random.uniform(12.0, 16.0), 1)
                    wash_usdt = min(wash_usdt, self.usdt_bal * 0.90)
                    if wash_usdt < 10.0:
                        wash_usdt = 10.0
                        
                    wash_air = round(wash_usdt / sweep_price, 3)
                    
                    p_str = "{:.8f}".format(sweep_price)
                    amt_str = "{:.3f}".format(wash_air)

                    # 4. 스프레드 내부 1:1 선(先)매도 후(後)매수 즉시 맞체결
                    # [규칙: 절대 먼저 매수하지 않고, 100% 선(先)매도 성공 + 최저매도호가 단독 검증 완료 후에만 매수]
                    
                    # 1) 본인 매도 주문을 스프레드 사이에 올림 (best_bid보다 높아 외부 매수자 체결 불가)
                    res_sell = self.private_post("/api/v1/private/trade/limit", {
                        "market": symbol,
                        "side": 1, # Sell (선매도)
                        "price": p_str,
                        "amount": amt_str
                    })
                    sell_id = res_sell.get("result", {}).get("id") if (res_sell and res_sell.get("code") == 0) else None
                    
                    # 선매도 주문이 정상 생성되지 않았으면 매수 주문을 절대 발주하지 않고 안전하게 건너뜀
                    if not sell_id:
                        self.log(f"⚠️ 선매도 발주 실패로 매수 스윕을 안전하게 취소합니다: {res_sell}")
                        time.sleep(2)
                        continue

                    # 2) 🛡️ [핵심 방어막] 매수 주문 전 오더북 최저 매도가 단독 검증
                    # 방금 올린 본인 매도 주문(sweep_price)이 거래소 전체에서 가장 싼 최저 매도가인지 실시간 확인
                    ob_check = self.get_orderbook(symbol)
                    asks_check = ob_check.get('asks', []) if ob_check else []
                    
                    if not asks_check:
                        self.cancel_order(sell_id, symbol)
                        self.log("⚠️ 오더북 재확인 실패로 매수를 중단하고 선매도를 취소합니다.")
                        time.sleep(1)
                        continue
                        
                    lowest_ask_now = float(asks_check[0][0])
                    
                    # 만약 최저 매도가가 내 매도 가격(sweep_price)보다 낮다면 (외부인이 더 싼 가격에 매도를 던진 경우)
                    # 절대 매수 주문을 내지 않고, 내 선매도 주문을 즉시 취소하여 USDT를 100% 보호!
                    if lowest_ask_now < (sweep_price - 0.000000001):
                        self.cancel_order(sell_id, symbol)
                        self.log(f"🚨 [외부 매도 침범 감지] 최저매도가({lowest_ask_now:.8f})가 본인선매도가({sweep_price:.8f})보다 낮습니다! 매수를 즉시 중단하고 선매도를 회수합니다.")
                        time.sleep(2)
                        continue
                    
                    # 3) 100% 단독 최저 매도가 확인 완료 -> 동일 가격 및 수량으로 본인 매수 발주 -> 방금 올린 본인 매도와 100% 즉시 체결
                    res_buy = self.private_post("/api/v1/private/trade/limit", {
                        "market": symbol,
                        "side": 2, # Buy (후매수)
                        "price": p_str,
                        "amount": amt_str
                    })
                    buy_id = res_buy.get("result", {}).get("id") if (res_buy and res_buy.get("code") == 0) else None
                    
                    time.sleep(0.2)
                    
                    # 4) 미체결 잔여 물량 즉시 취소 (호가창에 주문을 남기지 않음)
                    if sell_id:
                        self.cancel_order(sell_id, symbol)
                    if buy_id:
                        self.cancel_order(buy_id, symbol)
                        
                    cycle_washed_usdt += wash_usdt
                    progress = (cycle_washed_usdt / cycle_target_usdt) * 100
                    self.log(f"🌊 [스프레드 스윕 체결] 매수벽: {best_bid:.8f} | 매도벽: {best_ask:.8f} | 체결가: {p_str} | {amt_str} AIR (${wash_usdt:.1f}) (진행률: {progress:.1f}%)")
                    
                    # 다음 스윕 대기 (1.5 ~ 3.5초)
                    actual_sleep = max(1.0, self.config.interval * random.uniform(0.6, 1.2))
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