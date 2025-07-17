import os
import time
import pyupbit
import requests
import datetime

# 환경 변수에서 키 로드
ACCESS_KEY = os.environ.get("UPBIT_ACCESS_KEY")
SECRET_KEY = os.environ.get("UPBIT_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 업비트 로그인
upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# 텔레그램 메시지 전송 함수
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("텔레그램 전송 오류:", e)

# RSI 계산 함수
def get_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# 매수 함수
def buy_crypto(ticker, krw_balance):
    price = pyupbit.get_current_price(ticker)
    if price and krw_balance > 5000:
        unit = krw_balance * 0.9995 / price
        result = upbit.buy_market_order(ticker, krw_balance * 0.9995)
        msg = f"[매수] {ticker} - 수량: {unit:.6f}, 가격: {price:.0f}"
        send_telegram(msg)

# 매도 함수
def sell_crypto(ticker, volume):
    price = pyupbit.get_current_price(ticker)
    if price and volume > 0:
        result = upbit.sell_market_order(ticker, volume)
        msg = f"[매도] {ticker} - 수량: {volume:.6f}, 가격: {price:.0f}"
        send_telegram(msg)

# 메인 루프
def trade():
    target_coins = ["KRW-BTC", "KRW-ETH"]
    send_telegram("📈 단타봇 시작")

    while True:
        try:
            for ticker in target_coins:
                df = pyupbit.get_ohlcv(ticker, interval="minute5", count=100)
                if df is None or len(df) < 15:
                    continue

                rsi = get_rsi(df).iloc[-1]
                balances = upbit.get_balances()
                krw_balance = float(next((b['balance'] for b in balances if b['currency'] == "KRW"), 0))
                coin_balance = float(next((b['balance'] for b in balances if b['currency'] in ticker.split("-")[1]), 0))

                if rsi < 30:
                    buy_crypto(ticker, krw_balance)
                elif rsi > 70 and coin_balance > 0:
                    sell_crypto(ticker, coin_balance)

            time.sleep(10)

        except Exception as e:
            send_telegram(f"❗에러 발생: {e}")
            time.sleep(60)

if __name__ == "__main__":
    trade()
