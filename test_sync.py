import requests
import random
import time

def get_btc_price() -> float:
    """Binance Public API를 통해 BTC/USDT 현재가 가져오기"""
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})
        data = res.json()
        if "price" in data:
            return float(data["price"])
        else:
            print(f"BTC 가격 응답 이상 (Binance): {data}")
    except Exception as e:
        print(f"BTC 가격 조회 실패 (Binance): {e}")
    return 0.0

def test_logic():
    # 시뮬레이션 초기값
    target_mid = 0.0945 
    initial_air_price = target_mid
    initial_btc_price = 0.0
    
    print(f"시뮬레이션 시작: 초기 AIR {initial_air_price}")
    
    for i in range(5):
        current_btc = get_btc_price()
        if current_btc > 0:
            if initial_btc_price == 0:
                initial_btc_price = current_btc
                print(f"BTC 초기 기준가 설정: {initial_btc_price}")
            
            btc_ratio = current_btc / initial_btc_price
            beta = 1.0
            
            old_p = target_mid
            target_mid = initial_air_price * btc_ratio * beta
            target_mid *= random.uniform(0.9995, 1.0005) # 미세한 흔들기
            
            print(f"[{i+1}] BTC: {current_btc:.2f} (x{btc_ratio:.4f}) | AIR 목표가: {old_p:.4f} -> {target_mid:.4f}")
        else:
            print("BTC 조회 실패")
        
        time.sleep(1)

if __name__ == "__main__":
    test_logic()
