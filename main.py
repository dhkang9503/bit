import os
import time
import pyupbit
import requests
import pandas as pd
from datetime import datetime, timedelta

ACCESS_KEY = os.environ.get("UPBIT_ACCESS_KEY")
SECRET_KEY = os.environ.get("UPBIT_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

holding = {}
daily_loss = 0
is_stopped = False
MAX_DAILY_LOSS = 0.05  # -5%
MAX_HOLDINGS = 2
RESET_HOUR = 9


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("텔레그램 전송 실패:", e)


def get_ema(df, period):
    return df['close'].ewm(span=period).mean()


def get_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def get_price(ticker):
    return pyupbit.get_current_price(ticker)


def get_balance(symbol):
    balances = upbit.get_balances()
    for b in balances:
        if b['currency'] == symbol:
            return float(b['balance']), float(b.get('avg_buy_price', 0))
    return 0, 0


def get_top_volume_altcoins(n=3):
    tickers = pyupbit.get_tickers(fiat="KRW")
    tickers = [t for t in tickers if not t.endswith("BTC")]

    volumes = []
    for ticker in tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval="minute5", count=2)
            if df is not None and len(df) >= 2:
                volume = df['volume'].iloc[-2] * df['close'].iloc[-2]
                volumes.append((ticker, volume))
            time.sleep(0.05)
        except:
            continue

    volumes.sort(key=lambda x: x[1], reverse=True)
    return [v[0] for v in volumes[:n]]


def buy_crypto(ticker, amount, atr):
    price = get_price(ticker)
    if price is None or amount < 6000:
        return

    amount *= 0.9995  # 수수료 반영
    volume = amount / price
    upbit.buy_market_order(ticker, amount)

    holding[ticker] = {
        'entry_price': price,
        'volume': volume,
        'atr': atr
    }

    send_telegram(
        f"[📥 매수] {ticker}\n"
        f"매수 금액 : {amount:,.0f}원\n"
        f"매수 가격 : {price:,.0f}원\n"
        f"보유 수량 : {volume:.4f}개\n"
        f"ATR (5분) : {atr:.4f}"
    )


def sell_crypto(ticker, reason):
    global daily_loss

    info = holding.get(ticker)
    if not info:
        return

    volume = info['volume']
    entry_price = info['entry_price']
    current_price = get_price(ticker)
    if current_price is None or volume < 0.0001:
        return

    upbit.sell_market_order(ticker, volume)

    profit_rate = (current_price - entry_price) / entry_price
    profit = (current_price - entry_price) * volume
    daily_loss += -profit_rate if profit_rate < 0 else 0

    send_telegram(
        f"[📤 매도-{reason}] {ticker}\n"
        f"매수가 : {entry_price:,.0f}원\n"
        f"현재가 : {current_price:,.0f}원\n"
        f"수익률 : {profit_rate * 100:+.2f}%\n"
        f"수익금 : {profit:+,.0f}원\n"
        f"보유 수량 : {volume:.4f}개"
    )

    del holding[ticker]


def initialize_holding():
    balances = upbit.get_balances()
    for b in balances:
        currency = b['currency']
        if currency == "KRW":
            continue
        volume = float(b['balance'])
        avg_price = float(b.get('avg_buy_price', 0))
        if volume > 0 and avg_price > 0:
            ticker = f"KRW-{currency}"
            df_5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=100)
            if df_5 is None:
                continue
            atr = get_atr(df_5).iloc[-1]
            holding[ticker] = {
                'entry_price': avg_price,
                'volume': volume,
                'atr': atr
            }
    send_telegram("✅ 기존 포지션 복원 완료")


def trade():
    global daily_loss, is_stopped
    send_telegram("🚀 전략 시작: 고비중 + 손실제한 + 포지션 제한")
    last_reset = datetime.now().date()

    while True:
        try:
            now = datetime.now()

            if now.hour == RESET_HOUR and now.date() != last_reset:
                daily_loss = 0
                is_stopped = False
                last_reset = now.date()
                send_telegram("🕘 손실 한도 초기화됨. 거래 재개 가능.")

            if daily_loss >= MAX_DAILY_LOSS:
                if not is_stopped:
                    send_telegram(f"🛑 거래 정지됨: 당일 누적 손실 -{daily_loss*100:.2f}%")
                    is_stopped = True
                time.sleep(60)
                continue

            tickers = get_top_volume_altcoins()
            krw_balance, _ = get_balance("KRW")

            for ticker in tickers:
                if len(holding) >= MAX_HOLDINGS:
                    break

                symbol = ticker.split("-")[1]
                df_5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=100)
                df_15 = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
                if df_5 is None or df_15 is None:
                    continue

                ema9 = get_ema(df_15, 9).iloc[-1]
                ema21 = get_ema(df_15, 21).iloc[-1]
                if ema9 <= ema21:
                    continue

                rsi = get_rsi(df_5).iloc[-1]
                if rsi >= 30 or ticker in holding:
                    continue

                atr = get_atr(df_5).iloc[-1]

                if rsi < 10:
                    position_ratio = 0.20
                elif rsi < 20:
                    position_ratio = 0.15
                elif rsi < 25:
                    position_ratio = 0.10
                elif rsi < 28:
                    position_ratio = 0.08
                else:
                    position_ratio = 0.05

                amount = krw_balance * position_ratio
                buy_crypto(ticker, amount, atr)

            for ticker in list(holding.keys()):
                info = holding[ticker]
                current_price = get_price(ticker)
                if current_price is None:
                    continue

                entry_price = info['entry_price']
                atr = info['atr']
                change = (current_price - entry_price) / entry_price

                min_gain = 0.015
                min_loss = 0.01
                gain_target = max((atr / entry_price) * 1.5, min_gain)
                loss_limit = max((atr / entry_price) * 1.0, min_loss)

                if change >= gain_target:
                    sell_crypto(ticker, reason="익절")
                elif change <= -loss_limit:
                    sell_crypto(ticker, reason="손절")

            time.sleep(0.2)

        except Exception as e:
            send_telegram(f"❗에러 발생: {e}")
            time.sleep(60)


if __name__ == "__main__":
    initialize_holding()
    trade()
