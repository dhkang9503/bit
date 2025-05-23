import os
import pyupbit
import time
import traceback
import datetime
import pandas as pd
import requests

# API 키 입력
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

# 상위 알트코인 가져오기
def get_top_altcoins(limit=5):
    tickers = pyupbit.get_tickers(fiat="KRW")
    tickers = [t for t in tickers if t not in ["KRW-BTC", "KRW-DOGE"]]  # BTC 제외

    volumes = []
    for ticker in tickers:
        try:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                trade_volume = df['volume'].iloc[-1] * df['close'].iloc[-1]  # 거래량 * 가격
                volumes.append((ticker, trade_volume))
        except:
            continue

    volumes.sort(key=lambda x: x[1], reverse=True)
    top = [x[0] for x in volumes[:limit]]
    return top


# RSI 계산
def get_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)

    ma_up = up.rolling(window=period).mean()
    ma_down = down.rolling(window=period).mean()

    rs = ma_up / ma_down
    rsi = 100 - (100 / (1 + rs))
    return rsi

# 현재가 조회
def get_current_price(ticker):
    try:
        return pyupbit.get_current_price(ticker)
    except:
        return None

def initialize_positions():
    positions = {}
    balances = upbit.get_balances()
    for b in [b for b in balances if b['currency'] not in skip_coins]:
        if b['currency'] == 'KRW':
            continue

        coin = f"KRW-{b['currency']}"
        amount = float(b['balance'])
        if amount > 0:
            positions[coin] = {
                "holding": True,
                "entry_price": float(b.get('avg_buy_price', 0))  # 대안: 평균 매수가를 따로 불러올 수도 있음
            }
        else:
            positions[coin] = {"holding": False, "entry_price": 0}
    return positions

REINVEST_RATIO = 0.98  # 100% 재투자

send_telegram(f"✅ initialized: {upbit.get_balance("KRW"):,.0f}")
# send_telegram(f"{ACCESS_KEY[:5]}, {SECRET_KEY[:5]}, {TELEGRAM_TOKEN[:5]}, {TELEGRAM_CHAT_ID[:5]}")

# 메인 루프
while True:
    try:
        # 매수 상태 추적용
        positions = initialize_positions()

        # 1. 상위 알트코인 5개 조회 (매수 감시용)
        top_coins = get_top_altcoins()
        print(f"[{datetime.datetime.now()}] 감시 중인 상위 알트코인: {top_coins}")

        # 2. 매수 로직 (상위 코인 기준)
        if upbit.get_balance("KRW") > 10000:
            for coin in top_coins:
                if coin not in positions:
                    positions[coin] = {"holding": False, "entry_price": 0}

                if positions[coin]["holding"]:
                    continue  # 이미 보유 중이면 매수 안함

                df = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
                if df is None:
                    continue

                sma5 = df['close'].rolling(window=5).mean().iloc[-1]
                sma15 = df['close'].rolling(window=15).mean().iloc[-1]
                prev_sma5 = df['close'].rolling(window=5).mean().iloc[-2]
                prev_sma15 = df['close'].rolling(window=15).mean().iloc[-2]
                rsi = get_rsi(df['close']).iloc[-1]
                price = get_current_price(coin)

                if sma5 > sma15 and prev_sma5 <= prev_sma15 and rsi < 50:
                    krw = upbit.get_balance("KRW")

                    if krw > 10000:
                        invest_amount = krw * REINVEST_RATIO
                        upbit.buy_market_order(coin, invest_amount)

                        positions[coin]["holding"] = True
                        positions[coin]["entry_price"] = price
                        msg = f"✅ 매수: {coin}\n가격: {price:,.0f}\nRSI: {rsi:.2f}"
                        send_telegram(msg)

        # 3. 매도 로직 (내 보유 코인 기준)
        balances = upbit.get_balances()
        for b in [b for b in balances if b['currency'] not in skip_coins]:
            if b['currency'] == 'KRW':
                continue

            coin = f"KRW-{b['currency']}"
            vol = float(b['balance'])

            if vol < 0.00008:  # 업비트 최소 수량 필터
                continue

            df = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
            if df is None:
                continue

            rsi = get_rsi(df['close']).iloc[-1]
            price = get_current_price(coin)
            entry = positions.get(coin, {}).get("entry_price", price)
            pnl = (price - entry) / entry if entry else 0

            if pnl >= 0.02 or pnl <= -0.01: # rsi > 70 or 
                upbit.sell_market_order(coin, vol)
                positions[coin] = {"holding": False, "entry_price": 0}
                msg = f"🚨 매도: {coin}\n구매가: {entry:,.0f}\n현재가: {price:,.0f}\n수익률: {pnl*100:.2f}%\nRSI: {rsi:.2f}"
                send_telegram(msg)

        time.sleep(10)

    except Exception as e:
        err_msg = f"[자동매매 오류 발생]\n{traceback.format_exc()}"
        print(err_msg)
        send_telegram(err_msg)  # 텔레그램으로 오류 알림 전송
        time.sleep(60)  # 60초 후 자동 재시작
