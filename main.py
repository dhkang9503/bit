import os
import time
import pyupbit
import requests
import datetime

ACCESS_KEY = os.environ.get("UPBIT_ACCESS_KEY")
SECRET_KEY = os.environ.get("UPBIT_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("텔레그램 전송 실패:", e)

def get_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_price(ticker):
    return pyupbit.get_current_price(ticker)

def get_balance(symbol):
    balances = upbit.get_balances()
    for b in balances:
        if b['currency'] == symbol:
            return float(b['balance']), float(b.get('avg_buy_price', 0))
    return 0, 0

def buy_crypto(ticker, krw_balance):
    price = get_price(ticker)
    if price is None or krw_balance < 6000:
        return
    upbit.buy_market_order(ticker, krw_balance * 0.9995)
    send_telegram(f"[매수] {ticker} / 금액: {krw_balance:.0f}원 / 가격: {price:.0f}")

def sell_crypto(ticker, volume, reason="익절/손절"):
    price = get_price(ticker)
    if price is None or volume < 0.0001:
        return
    upbit.sell_market_order(ticker, volume)
    send_telegram(f"[매도-{reason}] {ticker} / 수량: {volume:.6f} / 가격: {price:.0f}")

def trade():
    tickers = ["KRW-BTC", "KRW-ETH"]
    send_telegram("📈 단타 봇 시작됨 (RSI + 수익률 조건)")

    while True:
        try:
            for ticker in tickers:
                symbol = ticker.split("-")[1]
                df = pyupbit.get_ohlcv(ticker, interval="minute5", count=100)
                if df is None or len(df) < 15:
                    continue

                rsi = get_rsi(df).iloc[-1]
                balance, avg_price = get_balance(symbol)
                current_price = get_price(ticker)
                krw_balance, _ = get_balance("KRW")

                # 손절 조건
                if balance > 0:
                    change_ratio = (current_price - avg_price) / avg_price
                    if change_ratio <= -0.01:
                        sell_crypto(ticker, balance, reason="손절")
                    elif rsi > 70 and change_ratio >= 0.03:
                        sell_crypto(ticker, balance, reason="익절(RSI70 + 수익률)")

                # 매수 조건
                elif rsi < 30 and krw_balance > 6000:
                    buy_crypto(ticker, krw_balance)

            time.sleep(0.12)

        except Exception as e:
            send_telegram(f"❗에러 발생: {e}")
            time.sleep(60)

if __name__ == "__main__":
    trade()
