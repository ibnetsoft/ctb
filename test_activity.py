import requests
import hashlib

BASE_URL = "https://api.biconomy.com"

def get_btc_activity_factor(symbol="BTC_USDT", size=15):
    try:
        url = f"{BASE_URL}/api/v1/kline"
        params = {"symbol": symbol, "type": "1min", "size": size}
        res = requests.get(url, params=params)
        klines = res.json()
        
        print(f"Kline count: {len(klines)}")
        if len(klines) > 0:
            print(f"Sample kline: {klines[-1]}")
            
        if not isinstance(klines, list) or len(klines) < 5:
            return 1.0
            
        vols = [float(k[5]) for k in klines]
        current_vol = vols[-1]
        avg_vol = sum(vols[:-1]) / (len(vols) - 1)
        
        vol_spike = current_vol / avg_vol if avg_vol > 0 else 1.0
        
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        price_range = (max(highs) - min(lows)) / min(lows) if min(lows) > 0 else 0
        
        v_boost = max(0, vol_spike - 1.0) * 0.1
        p_boost = price_range * 50.0
        
        boost = 1.0 + v_boost + p_boost
        final_boost = min(boost, 1.4)
        
        print(f"[Activity] BTC Spike: {vol_spike:.2f}x | Range: {price_range*100:.2f}% | Boost: {final_boost:.2f}x")
        return final_boost
        
    except Exception as e:
        print(f"BTC 활동성 계산 실패: {e}")
        import traceback
        traceback.print_exc()
        return 1.0

if __name__ == "__main__":
    get_btc_activity_factor()
