import hashlib
import time
import requests
import random
import os
import json
from datetime import datetime

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
        self.is_running = True
        self.logs = []
        self.target_price = 0.00004
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
                    self.auto_pause_enabled = bool(data.get("auto_pause_enabled", self.auto_pause_enabled))
                    self.run_duration = int(data.get("run_duration", self.run_duration))
                    self.pause_duration = int(data.get("pause_duration", self.pause_duration))
            except Exception as e:
                print(f"설정 파일 로드 실패: {e}")

class SimpleMarketMaker:
    def __init__(self, config: BotConfig):
        self.config = config
        self.usdt_bal = 0.0
        self.air_bal = 0.0
        self.auto_paused = False
        self.auto_pause_state_start = time.time()
        
    def log(self, message):
        import sys
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {message}"
        try:
            print(full_msg)
        except UnicodeEncodeError:
            try:
                encoding = sys.stdout.encoding or 'utf-8'
                print(full_msg.encode(encoding, errors='replace').decode(encoding))
            except:
                print(f"[{timestamp}] [Log] " + message.encode('ascii', errors='replace').decode('ascii'))

    def make_sign(self, params: dict) -> str:
        sorted_params = sorted(params.items())
        query = "&".join(f"{k}={v}" for k, v in sorted_params)
        query += f"&secret_key={self.config.secret_key}"
        return hashlib.md5(query.encode()).hexdigest().upper()

    def private_post(self, endpoint: str, params: dict) -> dict:
        params["api_key"] = self.config.api_key
        params["sign"] = self.make_sign(params)
        headers = {"X-SITE-ID": "127"}
        try:
            res = requests.post(self.config.base_url + endpoint, data=params, headers=headers, timeout=10)
            result = res.json()
            if result.get("code") != 0 and result.get("code") != 10:
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

    def run_mm_loop(self):
        symbol = self.config.symbol
        self.log(f"새로운 매수 우선 봇 (mmbot2) 가동: {symbol}")

        while True:
            if not self.config.is_running:
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
                else:
                    if elapsed_mins >= self.config.pause_duration:
                        self.auto_paused = False
                        self.auto_pause_state_start = now
                        self.log(f"▶️ [자동 재개] 설정된 일시정지 시간({self.config.pause_duration}분)이 경과하여 거래를 재개합니다.")
            else:
                self.auto_paused = False
                self.auto_pause_state_start = time.time()

            # 자동 일시정지 대기 처리
            if self.auto_paused:
                time.sleep(2)
                continue

            try:
                balances = self.get_balances()
                if not balances:
                    time.sleep(5); continue
                
                self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                self.log(f"[잔고] 가용 USDT: {self.usdt_bal:.4f} | 가용 AIR: {self.air_bal:.2f}")

                # 가격 고정
                target_price = self.config.target_price
                
                # 새로운 매수 물량을 올리기 전에, 이전 턴에 남겨놨던 10% 매수 물량을 전량 취소 (USDT 자동 회수)
                existing_orders = self.get_open_orders(symbol)
                for o in existing_orders:
                    self.cancel_order(o["id"], symbol)
                if len(existing_orders) > 0:
                    time.sleep(1) # 취소 반영 대기
                    # 취소 후 잔고 갱신
                    balances = self.get_balances()
                    if balances:
                        self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                
                macro_wall_usdt = self.usdt_bal * 0.98
                
                if macro_wall_usdt < 10.0:
                    self.log(f"⚠️ USDT가 10달러 미만입니다. 자금이 소진되어 대기합니다.")
                    time.sleep(10)
                    continue
                        
                # 2. 타겟 가격 및 매수벽 크기 계산 (8자리 소수점 지원)
                macro_wall_air = round(macro_wall_usdt / target_price, 3)
                
                self.log(f"🟢 [매수벽 설치] {target_price:.8f} 에 {macro_wall_air:.3f} AIR (${macro_wall_usdt:.1f}) 매수벽 올림!")
                res_buy = self.place_order(symbol, 2, target_price, macro_wall_air)
                if not res_buy or res_buy.get("code") != 0:
                    time.sleep(2)
                    continue
                    
                self.usdt_bal -= macro_wall_usdt
                washed_air = 0.0
                target_sell_air = round(macro_wall_air * 0.90, 3) # 90%만 매도하고 10%는 남김
                
                immediate_retry = False
                
                while washed_air < target_sell_air:
                    fresh_ob = self.get_orderbook(symbol)
                    bids = fresh_ob.get('bids', []) if fresh_ob else []
                    if bids:
                        current_best_bid = float(bids[0][0])
                        total_bid_vol_at_target = sum(float(bid[1]) for bid in bids if abs(float(bid[0]) - target_price) < 1e-7)
                        
                        if current_best_bid < target_price - 1e-7 or total_bid_vol_at_target < 10.0:
                            self.log(f"🎉/🚨 [물량 소진 감지] 타인이 내 매수벽에 물량을 던졌습니다! (저가 매수 흡수 성공) 다음 턴으로 진행합니다.")
                            immediate_retry = True
                            break
                        
                    sell_chunk_usdt = random.uniform(14.0, 16.0)
                    sell_chunk_air = round(sell_chunk_usdt / target_price, 3)
                    actual_sell_air = round(min(sell_chunk_air, target_sell_air - washed_air), 3)
                    
                    if actual_sell_air * target_price < 5.0:
                        break
                        
                    res_sell = self.place_order(symbol, 1, target_price, actual_sell_air)
                    if res_sell and res_sell.get("code") == 0:
                        washed_air += actual_sell_air
                        self.log(f"💥 [자전 매도] {actual_sell_air:.3f} AIR (${actual_sell_air*target_price:.1f}) 매도 완료 (진행률: {washed_air/target_sell_air*100:.1f}%)")
                    else:
                        break
                        
                    time.sleep(random.uniform(2.0, 4.0))
                    
                if immediate_retry:
                    self.log("🔄 즉시 새로운 턴을 시작합니다.")
                
                time.sleep(random.uniform(2.0, 5.0))

            except Exception as e:
                self.log(f"메인 루프 오류: {e}")
                time.sleep(5)

if __name__ == "__main__":
    conf = BotConfig()
    mm = SimpleMarketMaker(conf)
    mm.run_mm_loop()
