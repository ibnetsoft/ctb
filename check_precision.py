import requests

BASE_URL = "https://api.biconomy.com"
symbol = "AIR_USDT"

def get_market_info():
    # 시도해볼 후보 엔드포인트들
    endpoints = [
        "/api/v1/tickers",
        "/api/v1/market/config",
        "/api/v1/trade/market/info"
    ]
    
    for ep in endpoints:
        print(f"Checking {ep}...")
        try:
            res = requests.get(BASE_URL + ep)
            data = res.json()
            if data.get("code") == 0 or isinstance(data, list):
                print(f"Success! Response from {ep}:")
                # AIR_USDT 관련 정보만 출력
                if isinstance(data, list):
                    for item in data:
                        if item.get("symbol") == symbol:
                            print(item)
                elif "data" in data:
                    if isinstance(data["data"], list):
                        for item in data["data"]:
                            if item.get("symbol") == symbol:
                                print(item)
                    elif symbol in data["data"]:
                        print(data["data"][symbol])
            else:
                print(f"Fail: {data}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    get_market_info()
