import hashlib
import time
import requests
import random
import math
from datetime import datetime

# ── 초기 설정 ───────────────────────────────────
class BotConfig:
    def __init__(self):
        self.api_key = "e36d39fb-2bbc-4a88-8ed6-534b453bfa9e"
        self.secret_key = "f9c6a549-d34c-4288-aef1-206fe6f692ad"
        self.base_url = "https://api.biconomy.com"
        self.symbol = "AIR_USDT"
        self.beta = 3.0
        self.interval = 5
        self.is_running = True
        self.anchor_reset_interval = 120
        self.logs = []

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

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        print(full_msg)
        self.config.logs.append(full_msg)
        if len(self.config.logs) > 100:
            self.config.logs.pop(0)

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
            lows = [float(k[3]) for k in klines]
            price_range = (max(highs) - min(lows)) / min(lows) if min(lows) > 0 else 0
            
            # 안정화를 위해 배율 상한 및 대폭 완화된 로직 적용
            if rvol >= 1.0:
                # 지수 제거 및 보수적인 배율 (sqrt)
                vol_component = (math.sqrt(rvol) - 1.0) * 2.0
            else:
                vol_component = (rvol - 1.0) * 1.5
                
            p_boost = price_range * 100.0 # 가격 변동성 영향도 300 -> 100 하향
            sensitivity_ratio = 1.0 + vol_component + p_boost
            
            final_ratio = max(0.2, min(sensitivity_ratio, 8.0)) # 최대 배율 15.0 -> 8.0 하향
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
        res = self.private_post("/api/v1/private/order/pending", {"market": symbol, "offset": 0, "limit": 100})
        if res.get("code") == 0:
            result_obj = res.get("result", {})
            if isinstance(result_obj, dict):
                return result_obj.get("records", [])
        return []

    def cancel_order(self, order_id: str, symbol: str):
        return self.private_post("/api/v1/private/trade/cancel", {"order_id": order_id, "market": symbol})

    def place_order(self, symbol: str, side: int, price: float, amount: float):
        # side: 1=ASK(매도), 2=BID(매수)
        res = self.private_post("/api/v1/private/trade/limit", {
            "market": symbol,
            "side": side,
            "price": "{:.4f}".format(round(price, 4)),   
            "amount": "{:.4f}".format(round(amount, 4)), 
        })
        if res.get("code") == 0:
            side_str = "매수" if side == 2 else "매도"
            self.log(f"{side_str} 성공: {price:.4f} | 수량: {amount}")
        return res

    def place_grid_orders(self, symbol, mid_p, spread=0.015, count=6, vol_ratio=1.0):
        min_p = mid_p * (1 - spread)
        max_p = mid_p * (1 + spread)
        self.log(f"[Grid] 시작 ({min_p:.4f}~{max_p:.4f}) | 배율: {vol_ratio:.2f}")
        
        buy_prices = [round(min_p + (mid_p - min_p) * (i / (count - 1)), 4) for i in range(count)]
        for p in buy_prices:
            # 물량 범위를 150~250에서 100~500으로 대폭 확장하여 '일정한 거래량' 느낌 제거
            base_amt = random.uniform(100.0, 500.0)
            amt = round(max(110.0, base_amt * vol_ratio), 1)
            self.place_order(symbol, 2, p, amt)
            time.sleep(0.1)

        sell_prices = [round(mid_p + (max_p - mid_p) * (i / (count - 1)), 4) for i in range(count)]
        for p in sell_prices:
            # 매도 물량도 동일하게 가변 적용
            base_amt = random.uniform(100.0, 500.0)
            amt = round(max(110.0, base_amt * vol_ratio), 1)
            self.place_order(symbol, 1, p, amt)
            time.sleep(0.1)

    def run_mm_loop(self):
        symbol = self.config.symbol
        self.log(f"MM 봇 메인 루프 가동: {symbol}")
        
        # 초기 가격 설정
        while self.target_mid == 0.0:
            try:
                cur_ob = self.get_orderbook(symbol)
                if cur_ob and cur_ob.get('asks') and cur_ob.get('bids'):
                    self.target_mid = (float(cur_ob['asks'][0][0]) + float(cur_ob['bids'][0][0])) / 2
                else:
                    res = requests.get(f"{self.config.base_url}/api/v1/tickers", timeout=10)
                    data = res.json()
                    for t in data.get("ticker", []):
                        if t.get("symbol") == symbol:
                            self.target_mid = float(t.get("last", 0))
            except Exception as e:
                self.log(f"초기 가격 획득 중 오류: {e}")
            if self.target_mid == 0.0: time.sleep(2)
        
        self.initial_air_price = self.target_mid
        while self.initial_btc_price == 0.0:
            self.initial_btc_price, self.initial_btc_vol = self.get_biconomy_btc_stats()
            if self.initial_btc_price == 0.0: time.sleep(2)
        
        self.log(f"초기 설정 완료 - AIR: {self.initial_air_price}, BTC: {self.initial_btc_price}")

        while True:
            # 봇 정지 상태 확인 (대시보드 제어용)
            if not self.config.is_running:
                self.last_status = "Stopped (Standby)"
                time.sleep(2)
                continue

            try:
                self.last_status = "Updating Balances..."
                balances = self.get_balances()
                if not balances:
                    time.sleep(5); continue
                
                self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                
                self.last_status = "Tracking BTC..."
                self.current_btc_p, self.current_btc_v = self.get_biconomy_btc_stats()
                
                if self.current_btc_p > 0:
                    p_ratio = self.current_btc_p / self.initial_btc_price
                    self.vol_ratio = self.get_btc_volume_sensitivity()
                    
                    sensitive_p_ratio = (p_ratio - 1.0) * self.config.beta + 1.0
                    self.target_mid = self.initial_air_price * sensitive_p_ratio
                    self.target_mid *= random.uniform(0.9999, 1.0001)
                
                # 주기적 기준가 재설정 (Anchor)
                self.loop_count += 1
                cur_ob = self.get_orderbook(symbol)
                best_bid = 0.0
                best_ask = 0.0
                if cur_ob and cur_ob.get('asks') and cur_ob.get('bids'):
                    best_ask = float(cur_ob['asks'][0][0])
                    best_bid = float(cur_ob['bids'][0][0])
                    market_mid = (best_ask + best_bid) / 2
                
                # 급격한 이격 발생 시 또는 주기 도래 시 Anchor 실행
                price_diff = abs(self.target_mid - market_mid) / market_mid if market_mid > 0 else 0
                if self.loop_count >= self.config.anchor_reset_interval or price_diff > 0.03:
                    trigger_reason = "Interval" if self.loop_count >= self.config.anchor_reset_interval else "Price Deviation"
                    self.log(f"[Anchor] 기준가 동기화 실행 ({trigger_reason})")
                    if market_mid > 0:
                        self.initial_air_price = market_mid
                        self.initial_btc_price = self.current_btc_p
                        self.loop_count = 0

                p = round(self.target_mid, 4)
                scaled_amount = random.randint(160, 1600) * self.vol_ratio
                
                max_buy = (self.usdt_bal * 0.9) / p
                max_sell = self.air_bal * 0.9
                amount = round(max(110.0, min(float(scaled_amount), max_buy, max_sell)), 1)

                # Phase 1: 기존 주문 파악 및 유량 주문(Ghost) 즉시 제거
                self.last_status = "Scanning & Cleaning Ghost Orders..."
                existing_orders = self.get_open_orders(symbol)
                
                # 타겟가 기반 청소 기준 (타겟가 대비 10% 이상 차이나면 Ghost로 간주)
                ghost_threshold = 0.10 
                for o in existing_orders:
                    o_price = float(o["price"])
                    deviation = abs(o_price - p) / p if p > 0 else 0
                    if deviation > ghost_threshold:
                        self.log(f"!!! [Ghost 제거] {o_price:.4f} (이격: {deviation:.1%})")
                        self.cancel_order(o["id"], symbol)

                # Phase 2: 새로운 그리드 설치 (교체 전 미리 깔기)
                self.last_status = "Placing New Grid (Incremental)..."
                self.place_grid_orders(symbol, p, spread=0.015, count=6, vol_ratio=self.vol_ratio)

                # Phase 3: 가격 이동 (Sweeping) & Wash Trading
                self.last_status = "Price Sweeping & Wash Trading..."
                if cur_ob and cur_ob.get('asks') and cur_ob.get('bids'):
                    best_ask = float(cur_ob['asks'][0][0])
                    best_bid = float(cur_ob['bids'][0][0])
                    spread_pct = (best_ask - best_bid) / best_bid
                    
                    # 가격 보호 장치: 타겟 가격이 시장가와 너무 멀면 Wash 생략
                    target_deviation = abs(p - best_bid) / best_bid if best_bid > 0 else 0
                    
                    if self.usdt_bal > (p * amount * 1.5) and self.air_bal > amount * 1.5 and spread_pct < 0.01:
                        if target_deviation > 0.02:
                            self.log(f"!!! [Price Jump 감지] Wash 생략 ({target_deviation:.2%})")
                        else:
                            # 타겟가에 맞추는 공격적 체결 (Sweeping 효과)
                            self.log(f">>> [Active Trade] {amount}개 실행 (가격: {p:.4f})")
                            self.place_order(symbol, 2, p, amount)
                            time.sleep(0.7)
                            self.place_order(symbol, 1, p, amount) 

                # Phase 4: 구형 주문 제거 (이제 공백 없이 교체 완료)
                self.last_status = "Removing Stale Orders..."
                for o in existing_orders:
                    # Phase 1에서 이미 지운 건 제외 (간단하게 모든 existing_orders 취소 시도)
                    # 이미 취소된 건 API에서 오류를 뱉겠지만 보전성을 위해 수행
                    self.cancel_order(o["id"], symbol)

                self.last_status = "Idle (Waiting next loop)"

            except Exception as e:
                self.log(f"메인 루프 치명적 오류: {e}")
                time.sleep(5)

            # 설정된 interval에 따라 수동 대기
            actual_sleep = self.config.interval * random.uniform(0.7, 1.3)
            time.sleep(actual_sleep)

if __name__ == "__main__":
    # mmbot.py 단독 실행 시 (구형 방식 대응)
    conf = BotConfig()
    mm = MarketMaker(conf)
    mm.run_mm_loop()