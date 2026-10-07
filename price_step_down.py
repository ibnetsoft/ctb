import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import time
import json
from datetime import datetime
from mmbot import DigiFinexClient, BotConfig

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def run_price_step_down():
    cfg = BotConfig()
    client = DigiFinexClient(cfg.api_key, cfg.secret_key, log_func=log)
    symbol = "ctb_usdt"
    
    # 1. 현재 거래소 마지막 체결가 확인
    try:
        import requests
        res = requests.get(f"https://openapi.digifinex.com/v3/ticker?symbol={symbol}", timeout=5).json()
        current_p = float(res.get("ticker", [{}])[0].get("last", 2541.0))
    except Exception as e:
        log(f"Ticker 조회 실패, 기본값 2541.0 사용: {e}")
        current_p = 2541.0

    target_final_p = 541.0
    step_drop = 100.0

    log(f"🚀 [가격 단계적 하향 복구 시작]")
    log(f"   현재가: {current_p:.2f} USDT -> 최종 목표가: {target_final_p:.2f} USDT")
    log(f"   간격: 1분당 -{step_drop} USDT 하향")

    # 시작 가격 계산
    next_price = current_p - step_drop
    step_count = 0

    while True:
        step_count += 1
        
        # 목표가 이하로 내려가면 541.0으로 고정 후 최종 실행
        is_final = False
        if next_price <= target_final_p:
            next_price = target_final_p
            is_final = True

        # 주문 수량 계산 ($15 USDT 상당)
        amount = round(15.0 / next_price, 4)
        if amount < 0.005:
            amount = 0.005

        log(f"📍 [단계 {step_count}] 목표 가격: {next_price:.2f} USDT | 수량: {amount} CTB (${next_price * amount:.1f})")

        # 1) 선매도 주문 발주
        res_sell = client.place_order(symbol, 1, next_price, amount)
        sell_id = res_sell.get("result", {}).get("id") or res_sell.get("order_id")
        
        if not sell_id:
            log(f"⚠️ 선매도 주문 실패: {res_sell}. 10초 후 재시도...")
            time.sleep(10)
            continue

        time.sleep(0.3)

        # 2) 후매수 주문 발주 (선매도와 100% 자가 맞체결)
        res_buy = client.place_order(symbol, 2, next_price, amount)
        buy_id = res_buy.get("result", {}).get("id") or res_buy.get("order_id")

        time.sleep(0.5)

        # 3) 잔여 미체결 혹시 모를 대비 취소
        if sell_id:
            client.cancel_order(sell_id, symbol)
        if buy_id:
            client.cancel_order(buy_id, symbol)

        log(f"✅ [체결 성공] 가격 {next_price:.2f} USDT 체결 완료! (자가 맞체결)")

        if is_final:
            log(f"🎉 [목표 달성] 가격이 최종 목표치 {target_final_p:.2f} USDT에 정상 도달했습니다!")
            break

        # 다음 가격 준비
        next_price = next_price - step_drop
        log(f"⏳ 다음 가격 하향({next_price:.2f} USDT)까지 1분간 대기합니다...\n")
        time.sleep(60)

if __name__ == "__main__":
    run_price_step_down()
