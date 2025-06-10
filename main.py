import os
import time
import traceback
import datetime
import pandas as pd
import requests
import pyupbit

ACCESS_KEY = os.environ["UPBIT_ACCESS_KEY"]
SECRET_KEY = os.environ["UPBIT_SECRET_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
skip_coins = ['KRW', 'DOGE', 'APENFT']

BASE_INVEST_RATIO = 0.3
REINVEST_RATIO = 0.98
MAX_DAILY_DRAWDOWN = -0.02
SLIPPAGE_PCT = 0.005  # 슬리피지 완화


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"[Telegram 전송 실패] {e}")


def is_uptrend_multi(coin):
    df_day = pyupbit.get_ohlcv(coin, interval="day", count=21)
    if df_day is None: return False
    ema20 = df_day['close'].ewm(span=20).mean().iloc[-1]
    if df_day['close'].iloc[-1] < ema20:
        return False
    df_h1 = pyupbit.get_ohlcv(coin, interval="minute60", count=22)
    if df_h1 is None: return False
    ema8 = df_h1['close'].ewm(span=8).mean()
    ema21 = df_h1['close'].ewm(span=21).mean()
    if not (ema8.iloc[-1] > ema21.iloc[-1] and ema8.iloc[-2] <= ema21.iloc[-2]):
        return False
    return True


def get_atr(df, period=14):
    h_l = df['high'] - df['low']
    h_pc = (df['high'] - df['close'].shift()).abs()
    l_pc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def get_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(window=period).mean()
    ma_down = down.rolling(window=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def get_current_price(ticker):
    try:
        return pyupbit.get_current_price(ticker)
    except:
        return None


def initialize_positions():
    positions = {}
    balances = upbit.get_balances()
    for b in balances:
        cur = b['currency']
        if cur in skip_coins: continue
        coin = f"KRW-{cur}"
        amt = float(b['balance'])
        if amt > 0:
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


def get_total_equity():
    krw = upbit.get_balance('KRW') or 0
    balances = upbit.get_balances()
    total = krw
    for b in balances:
        cur = b['currency']
        if cur in skip_coins: continue
        vol = float(b['balance'])
        if vol > 0:
            price = get_current_price(f"KRW-{cur}") or 0
            total += vol * price
    return total


send_telegram(f"✅ Bot 시작! KRW 잔고: {upbit.get_balance('KRW'):,.0f}")
positions = initialize_positions()

last_date = datetime.datetime.now().date()
daily_start_equity = get_total_equity()


def reset_daily():
    global last_date, daily_start_equity
    last_date = datetime.datetime.now().date()
    daily_start_equity = get_total_equity()
    send_telegram("🔄 새로운 거래일 시작, 일일 밸런스 리셋.")


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
                        positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}
            time.sleep(1800)
            continue

        current_equity = get_total_equity()
        drawdown = (current_equity - daily_start_equity) / daily_start_equity

        tickers = pyupbit.get_tickers(fiat="KRW")
        top5 = sorted(
            [(t, (pyupbit.get_ohlcv(t, 'day', 1)['volume'].iloc[-1] * pyupbit.get_ohlcv(t, 'day', 1)['close'].iloc[-1])) for t in tickers if t not in ["KRW-BTC","KRW-DOGE"]],
            key=lambda x: x[1], reverse=True
        )[:5]
        top_coins = [t[0] for t in top5]

        for coin in top_coins:
            if drawdown <= MAX_DAILY_DRAWDOWN:
                break

            if coin not in positions:
                positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

            df5 = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
            if df5 is None: continue

            vol_ma20 = df5['volume'].rolling(20).mean().iloc[-1]
            if df5['volume'].iloc[-1] < vol_ma20 * 0.8:
                continue

            last_close = df5['close'].iloc[-1]
            price = get_current_price(coin)
            if price is None or abs(price/last_close - 1) > SLIPPAGE_PCT:
                continue

            sma5 = df5['close'].rolling(5).mean().iloc[-1]
            sma15= df5['close'].rolling(15).mean().iloc[-1]
            rsi  = get_rsi(df5['close']).iloc[-1]
            atr  = get_atr(df5)

            pos = positions[coin]
            krw_balance = upbit.get_balance('KRW')

            if not pos['holding']:
                if is_uptrend_multi(coin) and sma5 > sma15 and rsi < 40:
                    vol_ratio = max(0.1, 1 - (atr / price))
                    invest_ratio = BASE_INVEST_RATIO * vol_ratio * REINVEST_RATIO
                    amount_krw = krw_balance * invest_ratio
                    if amount_krw > 5000:
                        upbit.buy_market_order(coin, amount_krw)
                        positions[coin].update({
                            'holding': True,
                            'entry_price': price,
                            'high_price': price,
                            'added': False,
                            'partial_taken': False
                        })
                        send_telegram(f"✅ 매수: {coin}\n가격: {price:,.0f}\nATR: {atr:.2f}")

            else:
                entry = pos['entry_price']
                pnl = (price - entry) / entry
                if price > pos['high_price']:
                    pos['high_price'] = price

                if not pos['added'] and pnl <= -0.02:
                    amount_krw = krw_balance * BASE_INVEST_RATIO * REINVEST_RATIO
                    if amount_krw > 5000:
                        upbit.buy_market_order(coin, amount_krw)
                        new_entry = (entry + price) / 2
                        pos.update({'entry_price': new_entry, 'added': True})
                        send_telegram(f"📉 추가 매수: {coin}\n가격: {price:,.0f}")

                stop_loss = entry - atr * 1.2
                tp1 = entry + atr * 1.0
                tp2 = entry + atr * 2.0

                if not pos['partial_taken'] and price >= tp1:
                    vol = upbit.get_balance(coin.split('-')[1])
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol * 0.5)
                        pos['partial_taken'] = True
                        send_telegram(f"💰 부분 익절: {coin}\n가격: {price:,.0f}\n수익률: {pnl*100:.2f}%")

                if price >= tp2 or price <= stop_loss:
                    vol = upbit.get_balance(coin.split('-')[1])
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol)
                        send_telegram(f"🚨 전량 청산: {coin}\n진입가: {entry:,.0f}\n현재가: {price:,.0f}\n수익률: {pnl*100:.2f}%")
                        positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

        time.sleep(10)

    except Exception:
        err = traceback.format_exc()
        print(err)
        send_telegram(f"[자동매매 오류]\n{err}")
        time.sleep(60)
