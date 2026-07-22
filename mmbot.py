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

    def save_config(self):
        data = {
            "api_key": self.api_key,
            "secret_key": self.secret_key,
            "telegram_token": self.telegram_token,
            "telegram_chat_id": self.telegram_chat_id,
            "telegram_enabled": self.telegram_enabled,
            "target_price": self.target_price,
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
            print(full_msg)
        except UnicodeEncodeError:
            try:
                encoding = sys.stdout.encoding or 'utf-8'
                print(full_msg.encode(encoding, errors='replace').decode(encoding))
            except:
                print(f"[{timestamp}] [Log] " + message.encode('ascii', errors='replace').decode('ascii'))
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
            "price": "{:.5f}".format(round(price, 5)),   
            "amount": "{:.4f}".format(round(amount, 4)), 
        })
        if res.get("code") == 0:
            side_str = "매수" if side == 2 else "매도"
            self.log(f"{side_str} 성공: {price:.5f} | 수량: {amount}")
        return res

    def place_grid_orders(self, symbol, mid_p, spread=0.015, count=6, vol_ratio=1.0, should_place_buys=True):
        # 덤핑 방어를 위한 시장 상태 분석
        cur_ob = self.get_orderbook(symbol)
        market_ask = mid_p
        if cur_ob and cur_ob.get('asks') and len(cur_ob['asks']) > 0:
            market_ask = float(cur_ob['asks'][0][0])

        is_pumping = mid_p > market_ask * 1.005  # 목표가가 현재 매도 호가보다 높은 경우 (상승 유도 상태)

        if is_pumping:
            self.log("⚠️ [덤핑 방어 모드 활성화] 목표가가 시장가보다 높아 매수를 최소화하고 아래로 넓게 배치합니다.")
            buy_spread = spread * 2.0  # 매수 범위를 아래로 2배 넓힘 (안전 거리 확보)
            sell_spread = spread
            max_allowed_buy_price = market_ask * 0.998  # 최우선 매도호가보다 0.2% 아래로 매수 제한 (즉시 체결 방지)
        else:
            buy_spread = spread
            sell_spread = spread
            max_allowed_buy_price = mid_p

        min_p = mid_p * (1 - buy_spread)
        max_p = mid_p * (1 + sell_spread)
        
        # 매수 가격 생성 및 가공
        buy_prices = [
            max(self.config.target_price, round(min_p + (mid_p - min_p) * (i / (count - 1)), 5)) for i in range(count)
        ]
        # 즉시 체결 방지를 위한 가격 상한 적용
        if is_pumping:
            buy_prices = [min(p, round(max_allowed_buy_price, 5)) for p in buy_prices]
        
        # 중복 가격 제거 및 정렬
        buy_prices = sorted(list(set(buy_prices)), reverse=True)
        
        self.log(f"[Grid] 시작 (매수 호가 수: {len(buy_prices)}개 | 매도 범위: {mid_p:.4f}~{max_p:.4f}) | 배율: {vol_ratio:.2f}")
        
        if should_place_buys:
            temp_usdt_bal = self.usdt_bal
            for p in buy_prices:
                min_required = max(5.0, 10.5 / p) if p > 0 else 150.0
                # 매수 거래량을 항상 거래소 최소 주문 조건(10.5 USDT)에 맞춘 최소 수량으로 극소화
                amt = round(min_required * 1.02, 1)
                
                order_val = p * amt
                if temp_usdt_bal < order_val:
                    continue
                res = self.place_order(symbol, 2, p, amt)
                if res and res.get("code") == 0:
                    temp_usdt_bal -= order_val
                time.sleep(0.1)
        else:
            self.log("⏳ [매수 스킵] 1분 간격 제한으로 인해 매수 그리드 배치를 건너뜁니다.")

        # 모든 매도 물량을 0.0001 USDT에 하나의 큰 매도 주문으로 상시 걸어두는 방식으로 변경했으므로,
        # 그리드 매도 주문 배치는 수행하지 않고 스킵합니다.
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

                    # 1. 미세 매수벽 (Macro Buy Wall) 크기 결정
                    # 가용 USDT의 98%를 한 번에 벽으로 세움
                    macro_wall_usdt = self.usdt_bal * 0.98
                    
                    if macro_wall_usdt < 10.0:
                        self.log("⚠️ USDT가 부족하여 기존 매수벽 주문(오래된 주문)을 취소하여 자금을 확보합니다.")
                        existing_orders = self.get_open_orders(symbol)
                        # 매수 주문(side: 2)만 필터링
                        buy_orders = [o for o in existing_orders if o.get('side') == 2 or o.get('side') == '2']
                        
                        try:
                            # 오래된 주문이 앞으로 오도록 정렬 (ctime 또는 id 사용)
                            buy_orders.sort(key=lambda x: float(x.get('ctime', x.get('id', 0))))
                        except Exception:
                            try:
                                buy_orders.sort(key=lambda x: int(x.get('id', 0)))
                            except Exception:
                                pass
                        
                        cancelled_count = 0
                        for o in buy_orders:
                            res_cancel = self.cancel_order(o["id"], symbol)
                            if res_cancel and res_cancel.get("code") == 0:
                                cancelled_count += 1
                                order_price = float(o.get('price', target_price))
                                order_left = float(o.get('left', 0))
                                recovered_usdt = order_price * order_left
                                self.usdt_bal += recovered_usdt
                                self.log(f"🧹 오래된 매수 주문 취소 완료: ID {o['id']} | 회수된 USDT: {recovered_usdt:.4f}")
                                
                                # 가용 USDT 갱신
                                macro_wall_usdt = self.usdt_bal * 0.98
                                if macro_wall_usdt >= 15.0:  # 최소 거래 진행이 가능한 잔고 확보 시 중단
                                    break
                                time.sleep(0.1)
                        
                        # 실제 잔고 재동기화
                        if cancelled_count > 0:
                            time.sleep(1) # 취소 반영 대기
                            balances = self.get_balances()
                            if balances:
                                self.air_bal = float(balances.get("AIR", {}).get("available", "0"))
                                self.usdt_bal = float(balances.get("USDT", {}).get("available", "0"))
                                macro_wall_usdt = self.usdt_bal * 0.98
                                self.log(f"🔄 잔고 갱신 완료: 가용 USDT: {self.usdt_bal:.4f} | 가용 AIR: {self.air_bal:.2f}")
                        
                        if macro_wall_usdt < 10.0:
                            self.log("⚠️ 모든 매수벽을 정리했음에도 가용 USDT가 10달러 미만입니다. 자금이 소진되어 이번 주기를 조기 종료합니다.")
                            cycle_washed_usdt = cycle_target_usdt # 강제로 이번 턴 종료시켜 휴식 진입
                            break
                        
                    # 기존 주문이 너무 많으면 오래된 것부터 정리 (API Limit 방지, 10% 잔여벽들)
                    existing_orders = self.get_open_orders(symbol)
                    if len(existing_orders) > 50:
                        self.log("🧹 미체결 주문이 50개가 넘어 오래된 주문 일부를 정리합니다.")
                        # 가격이 낮은(오래된) 매수 주문부터 취소
                        buy_orders = [o for o in existing_orders if o['side'] == 2]
                        buy_orders.sort(key=lambda x: float(x['price']))
                        for o in buy_orders[:len(buy_orders)-30]: # 상위 30개만 남기고 취소
                            self.cancel_order(o["id"], symbol)
                            
                    # 2. 순수 외부 세력 호가창 확인 (타겟 가격 설정)
                    cur_ob = self.get_orderbook(symbol)
                    if not cur_ob or not cur_ob.get('asks') or not cur_ob.get('bids'):
                        time.sleep(2)
                        continue
                        
                    market_best_ask = float(cur_ob['asks'][0][0])
                    market_best_bid = float(cur_ob['bids'][0][0])
                    
                    # USDT 고정 거래를 위해 타겟 가격 강제 고정
                    target_price = self.config.target_price
                        
                    if target_price >= market_best_ask:
                        # 호가가 겹치면 바로 체결되므로 문제 없음 (저가 매수 흡수)
                        pass
                        
                    macro_wall_air = round(macro_wall_usdt / target_price, 1)
                    
                    # 3. 매수벽 설치 (Buy First)
                    self.log(f"🟢 [매수벽 설치] {target_price:.5f} 에 {macro_wall_air} AIR (${macro_wall_usdt:.1f}) 매수벽 올림!")
                    res_buy = self.place_order(symbol, 2, target_price, macro_wall_air)
                    if not res_buy or res_buy.get("code") != 0:
                        time.sleep(2)
                        continue
                        
                    self.usdt_bal -= macro_wall_usdt
                    washed_air = 0.0
                    target_sell_air = macro_wall_air * 0.90 # 90%만 매도하고 10%는 남김
                    
                    # 4. 분할 매도 루프 (약 15달러 단위)
                    while washed_air < target_sell_air:
                        fresh_ob = self.get_orderbook(symbol)
                        if not fresh_ob or not fresh_ob.get('bids'):
                            time.sleep(1)
                            continue
                            
                        current_best_bid = float(fresh_ob['bids'][0][0])
                        total_bid_vol_at_target = sum(float(bid[1]) for bid in fresh_ob['bids'] if abs(float(bid[0]) - target_price) < 0.000001)
                        my_remaining = macro_wall_air - washed_air
                        
                        # 내 매수벽이 타인에게 모두 체결되어 사라졌는지 확인
                        if current_best_bid < target_price - 1e-6 or total_bid_vol_at_target < 10.0:
                            self.log(f"🎉/🚨 [물량 소진 감지] 타인이 내 매수벽에 물량을 던졌습니다! (저가 매수 흡수 성공) 다음 턴으로 진행합니다.")
                            break
                            
                        # 매도 금액 결정 (약 15 USDT)
                        sell_chunk_usdt = random.uniform(14.0, 16.0)
                        sell_chunk_air = round(sell_chunk_usdt / target_price, 1)
                        actual_sell_air = min(sell_chunk_air, target_sell_air - washed_air)
                        
                        if actual_sell_air * target_price < 5.0:
                            break
                            
                        # 매도 주문 실행 (내 매수벽을 때림)
                        res_sell = self.place_order(symbol, 1, target_price, actual_sell_air)
                        if res_sell and res_sell.get("code") == 0:
                            washed_air += actual_sell_air
                            cycle_washed_usdt += (actual_sell_air * target_price)
                            progress = (cycle_washed_usdt / cycle_target_usdt) * 100
                            self.log(f"💥 [자전 매도] {actual_sell_air} AIR (${actual_sell_air*target_price:.1f}) 매도 완료 (진행률: {progress:.1f}%)")
                        else:
                            break
                            
                        time.sleep(random.uniform(2.0, 4.0))
                        
                    # 남은 10%의 매수벽은 취소하지 않고 그대로 두어 매집 및 가격 방어 용도로 씀 (기존 cancel_order 로직 삭제)
                    
                    if immediate_retry:
                        self.log("🔄 즉시 새로운 최저가격을 스캔하여 다시 턴을 시작합니다.")
                        break
                        
                    # 다음 턴 시작 전 간격 (2~5초)
                    time.sleep(random.uniform(2.0, 5.0))
                    
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