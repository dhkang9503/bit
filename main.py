import os
import time
import datetime
import traceback
import pandas as pd
import requests
import pyupbit

# 환경 변수 로드
ACCESS_KEY = os.environ.get("UPBIT_ACCESS_KEY")
SECRET_KEY = os.environ.get("UPBIT_SECRET_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Upbit 객체 생성
upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# 상수 설정
SKIP_COINS = {'KRW', 'DOGE', 'APENFT'}
BASE_INVEST_RATIO = 0.3
REINVEST_RATIO = 0.98
MAX_DAILY_DRAWDOWN = -0.02
SLIPPAGE_PCT = 0.005
MIN_ORDER_KRW = 5000

positions = {}
last_date = datetime.datetime.now().date()
daily_start_equity = 0

# 텔레그램 전송 함수
def send_telegram(message):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message}
        )
    except Exception as e:
        print(f"[Telegram 전송 실패] {e}")

# 기술적 지표 함수들
def get_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

def get_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def is_uptrend_multi(coin):
    df_day = pyupbit.get_ohlcv(coin, interval="day", count=21)
    if df_day is None or len(df_day) < 21:
        return False
    if df_day['close'].iloc[-1] < df_day['close'].ewm(span=20).mean().iloc[-1]:
        return False

    df_hour = pyupbit.get_ohlcv(coin, interval="minute60", count=22)
    if df_hour is None or len(df_hour) < 22:
        return False

    ema8 = df_hour['close'].ewm(span=8).mean()
    ema21 = df_hour['close'].ewm(span=21).mean()
    return ema8.iloc[-1] > ema21.iloc[-1] and ema8.iloc[-2] <= ema21.iloc[-2]

# 자산 계산
def get_total_equity():
    try:
        krw = upbit.get_balance('KRW') or 0
        total = krw
        for b in upbit.get_balances():
            cur = b['currency']
            if cur in SKIP_COINS:
                continue
            vol = float(b['balance'])
            if vol > 0:
                price = pyupbit.get_current_price(f"KRW-{cur}") or 0
                total += vol * price
        return total
    except:
        return 0

# 포지션 초기화
def initialize_positions():
    pos = {}
    for b in upbit.get_balances():
        cur = b['currency']
        if cur in SKIP_COINS:
            continue
        coin = f"KRW-{cur}"
        amt = float(b['balance'])
        entry = float(b.get('avg_buy_price', 0))
        pos[coin] = {
            "holding": amt > 0,
            "entry_price": entry if amt > 0 else 0,
            "high_price": entry if amt > 0 else 0,
            "added": False,
            "partial_taken": False
        }
    return pos

# 매일 리셋
def reset_daily():
    global last_date, daily_start_equity
    last_date = datetime.datetime.now().date()
    daily_start_equity = get_total_equity()
    send_telegram("🔄 새로운 거래일 시작, 일일 밸런스 리셋.")

# 시작
positions = initialize_positions()
daily_start_equity = get_total_equity()
send_telegram(f"✅ Bot 시작! KRW 잔고: {upbit.get_balance('KRW'):,.0f}")

# 메인 루프
while True:
    try:
        now = datetime.datetime.now()
        if now.date() != last_date:
            reset_daily()

        if now.hour == 23 and now.minute >= 30:
            for coin, pos in positions.items():
                if pos['holding']:
                    vol = upbit.get_balance(coin.split('-')[1])
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol)
                        send_telegram(f"🕤 타임클로즈 전량매도: {coin}")
                        positions[coin] = initialize_positions().get(coin, pos)
            time.sleep(600)
            continue

        equity = get_total_equity()
        drawdown = (equity - daily_start_equity) / daily_start_equity

        if drawdown <= MAX_DAILY_DRAWDOWN:
            time.sleep(10)
            continue

        tickers = pyupbit.get_tickers(fiat="KRW")
        top5 = []
        for t in tickers:
            if t in ["KRW-BTC", "KRW-DOGE"]:
                continue
            df = pyupbit.get_ohlcv(t, 'day', count=1)
            if df is None or df.empty:
                continue
            volume = df['volume'].iloc[-1]
            close = df['close'].iloc[-1]
            top5.append((t, volume * close))

        top5 = sorted(top5, key=lambda x: x[1], reverse=True)[:5]

        for coin, _ in top5:
            if coin not in positions:
                positions[coin] = {
                    "holding": False, "entry_price": 0, "high_price": 0,
                    "added": False, "partial_taken": False
                }
            pos = positions[coin]

            df5 = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
            if df5 is None or len(df5) < 20:
                continue

            vol_ma20 = df5['volume'].rolling(20).mean().iloc[-1]
            if df5['volume'].iloc[-1] < vol_ma20 * 0.8:
                continue

            price = pyupbit.get_current_price(coin)
            last_close = df5['close'].iloc[-1]
            if price is None or abs(price / last_close - 1) > SLIPPAGE_PCT:
                continue

            sma5 = df5['close'].rolling(5).mean().iloc[-1]
            sma15 = df5['close'].rolling(15).mean().iloc[-1]
            rsi = get_rsi(df5['close']).iloc[-1]
            atr = get_atr(df5)
            krw_balance = upbit.get_balance('KRW')

            if not pos['holding'] and is_uptrend_multi(coin) and sma5 > sma15 and rsi < 40:
                invest_ratio = BASE_INVEST_RATIO * max(0.1, 1 - (atr / price)) * REINVEST_RATIO
                amount_krw = krw_balance * invest_ratio
                if amount_krw > MIN_ORDER_KRW:
                    upbit.buy_market_order(coin, amount_krw)
                    positions[coin].update({
                        'holding': True, 'entry_price': price,
                        'high_price': price, 'added': False, 'partial_taken': False
                    })
                    send_telegram(f"✅ 매수: {coin}\n가격: {price:,.0f}\nATR: {atr:.2f}")

            elif pos['holding']:
                entry = pos['entry_price']
                pnl = (price - entry) / entry
                if price > pos['high_price']:
                    pos['high_price'] = price

                if not pos['added'] and pnl <= -0.02:
                    amount_krw = krw_balance * BASE_INVEST_RATIO * REINVEST_RATIO
                    if amount_krw > MIN_ORDER_KRW:
                        upbit.buy_market_order(coin, amount_krw)
                        new_entry = (entry + price) / 2
                        pos.update({'entry_price': new_entry, 'added': True})
                        send_telegram(f"📉 추가 매수: {coin}\n가격: {price:,.0f}")

                stop_loss = entry - atr * 1.2
                tp1 = entry + atr * 1.0
                tp2 = entry + atr * 2.0

                vol = upbit.get_balance(coin.split('-')[1])
                if not pos['partial_taken'] and price >= tp1 and vol > 0.00008:
                    upbit.sell_market_order(coin, vol * 0.5)
                    pos['partial_taken'] = True
                    send_telegram(f"💰 부분 익절: {coin}\n가격: {price:,.0f}\n수익률: {pnl * 100:.2f}%")

                if (price >= tp2 or price <= stop_loss) and vol > 0.00008:
                    upbit.sell_market_order(coin, vol)
                    send_telegram(f"🚨 전량 청산: {coin}\n진입가: {entry:,.0f}\n현재가: {price:,.0f}\n수익률: {pnl * 100:.2f}%")
                    positions[coin] = {
                        "holding": False, "entry_price": 0, "high_price": 0,
                        "added": False, "partial_taken": False
                    }

        time.sleep(10)

    except Exception:
        err = traceback.format_exc()
        print(err)
        send_telegram(f"[자동매매 오류]\n{err}")
        time.sleep(60)
