import os
import pyupbit
import time
import traceback
import datetime
import pandas as pd
import requests

# API 키
ACCESS_KEY = os.environ["UPBIT_ACCESS_KEY"]
SECRET_KEY = os.environ["UPBIT_SECRET_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
skip_coins = ['KRW', 'DOGE', 'APENFT']

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"[텔레그램 전송 실패] {e}")

def get_top_altcoins(limit=5):
    tickers = pyupbit.get_tickers(fiat="KRW")
    tickers = [t for t in tickers if t not in ["KRW-BTC", "KRW-DOGE"]]
    volumes = []
    for ticker in tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                trade_volume = df['volume'].iloc[-1] * df['close'].iloc[-1]
                volumes.append((ticker, trade_volume))
        except:
            continue
    volumes.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in volumes[:limit]]

def get_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(window=period).mean()
    ma_down = down.rolling(window=period).mean()
    rs = ma_up / ma_down
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_current_price(ticker):
    try:
        return pyupbit.get_current_price(ticker)
    except:
        return None

def is_btc_uptrend():
    df = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=3)
    return df['close'].iloc[-1] > df['close'].iloc[-2]

def initialize_positions():
    positions = {}
    balances = upbit.get_balances()
    for b in [b for b in balances if b['currency'] not in skip_coins]:
        coin = f"KRW-{b['currency']}"
        amount = float(b['balance'])
        if amount > 0:
            positions[coin] = {
                "holding": True,
                "entry_price": float(b.get('avg_buy_price', 0)),
                "high_price": float(b.get('avg_buy_price', 0)),
                "added": False,
                "partial_taken": False
            }
        else:
            positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}
    return positions

INVEST_RATIO = 0.3
REINVEST_RATIO = 0.98

send_telegram(f"✅ Bot initialized. KRW balance: {upbit.get_balance('KRW'):,.0f}")
positions = initialize_positions()

while True:
    try:
        top_coins = get_top_altcoins()
        print(f"[{datetime.datetime.now()}] 감시 중: {top_coins}")

        btc_uptrend = is_btc_uptrend()
        krw_balance = upbit.get_balance("KRW")

        for coin in top_coins:
            if coin not in positions:
                positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

            df = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
            if df is None:
                continue

            sma5 = df['close'].rolling(5).mean().iloc[-1]
            sma15 = df['close'].rolling(15).mean().iloc[-1]
            prev_sma5 = df['close'].rolling(5).mean().iloc[-2]
            prev_sma15 = df['close'].rolling(15).mean().iloc[-2]
            rsi = get_rsi(df['close']).iloc[-1]
            price = get_current_price(coin)

            if not positions[coin]["holding"]:
                if btc_uptrend and sma5 > sma15 and prev_sma5 <= prev_sma15 and rsi < 40:
                    invest_amount = krw_balance * INVEST_RATIO * REINVEST_RATIO
                    if invest_amount > 5000:
                        upbit.buy_market_order(coin, invest_amount)
                        positions[coin] = {
                            "holding": True,
                            "entry_price": price,
                            "high_price": price,
                            "added": False,
                            "partial_taken": False
                        }
                        send_telegram(f"✅ 매수: {coin}\n가격: {price:,.0f}\nRSI: {rsi:.2f}")

            else:
                entry = positions[coin]["entry_price"]
                pnl = (price - entry) / entry
                high = positions[coin]["high_price"]
                if price > high:
                    positions[coin]["high_price"] = price

                # 추가 매수 (1회만)
                if not positions[coin]["added"] and pnl <= -0.02:
                    invest_amount = krw_balance * INVEST_RATIO * REINVEST_RATIO
                    if invest_amount > 5000:
                        upbit.buy_market_order(coin, invest_amount)
                        new_entry = (entry + price) / 2
                        positions[coin]["entry_price"] = new_entry
                        positions[coin]["added"] = True
                        send_telegram(f"📉 추가 매수: {coin}\n가격: {price:,.0f}")

                # 부분 익절: +2%에서 절반 매도
                if pnl >= 0.02 and not positions[coin]["partial_taken"]:
                    vol = upbit.get_balance(coin)
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol * 0.5)
                        positions[coin]["partial_taken"] = True
                        send_telegram(f"💰 절반 익절: {coin}\n가격: {price:,.0f}\n수익률: {pnl*100:.2f}%")

                # 전량 익절: +5% 이상 or 트레일링 손절 -1.5%
                drawdown = (price - high) / high
                if pnl >= 0.05 or drawdown <= -0.015:
                    vol = upbit.get_balance(coin)
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol)
                        send_telegram(f"🚨 전량 매도: {coin}\n구매가: {entry:,.0f}\n현재가: {price:,.0f}\n수익률: {pnl*100:.2f}")
                        positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

        time.sleep(10)

    except Exception as e:
        err_msg = f"[자동매매 오류 발생]\n{traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg)
        time.sleep(60)
