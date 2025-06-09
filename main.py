import os
import time
import traceback
import datetime
import pandas as pd
import requests
import pyupbit

# 환경 변수에서 키 불러오기
ACCESS_KEY = os.environ["UPBIT_ACCESS_KEY"]
SECRET_KEY = os.environ["UPBIT_SECRET_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Upbit 객체 생성
upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
skip_coins = ['KRW', 'DOGE', 'APENFT']

# 투자 기본 비율
BASE_INVEST_RATIO = 0.3
REINVEST_RATIO = 0.98
MAX_DAILY_DRAWDOWN = -0.02  # 하루 기준 -2%
SLIPPAGE_THRESHOLD = 0.002  # 0.2%

# Telegram 전송

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"[Telegram 전송 실패] {e}")

# 다중 타임프레임 상승 추세 확인
def is_uptrend_multi(coin):
    # 일봉 20EMA 위
    df_day = pyupbit.get_ohlcv(coin, interval="day", count=21)
    if df_day is None: return False
    ema20 = df_day['close'].ewm(span=20).mean().iloc[-1]
    if df_day['close'].iloc[-1] < ema20:
        return False
    # 1시간봉 8/21 EMA 골든 크로스
    df_h1 = pyupbit.get_ohlcv(coin, interval="minute60", count=22)
    if df_h1 is None: return False
    ema8  = df_h1['close'].ewm(span=8).mean()
    ema21 = df_h1['close'].ewm(span=21).mean()
    if not (ema8.iloc[-1] > ema21.iloc[-1] and ema8.iloc[-2] <= ema21.iloc[-2]):
        return False
    return True

# ATR 계산 (14)
def get_atr(df, period=14):
    h_l = df['high'] - df['low']
    h_pc = (df['high'] - df['close'].shift()).abs()
    l_pc = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

# RSI 계산 (14)
def get_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(window=period).mean()
    ma_down = down.rolling(window=period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

# 현 가격 조회
def get_current_price(ticker):
    try:
        return pyupbit.get_current_price(ticker)
    except:
        return None

# 포지션 초기화
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

# 총 자산 계산 (KRW + 코인 평가액)
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

# 프로그램 시작
send_telegram(f"✅ Bot 시작! KRW 잔고: {upbit.get_balance('KRW'):,.0f}")
positions = initialize_positions()

# 일일 드로다운 트래킹
last_date = datetime.datetime.now().date()
daily_start_equity = get_total_equity()

def reset_daily():
    global last_date, daily_start_equity
    last_date = datetime.datetime.now().date()
    daily_start_equity = get_total_equity()
    send_telegram("🔄 새로운 거래일 시작, 일일 밸런스 리셋.")

# 메인 루프
while True:
    try:
        now = datetime.datetime.now()
        # 날짜 변경 시 드로다운 초기화
        if now.date() != last_date:
            reset_daily()

        # 밤 23:30 이후 전량 청산
        if now.hour == 23 and now.minute >= 30:
            for coin, pos in positions.items():
                if pos['holding']:
                    vol = upbit.get_balance(coin)
                    if vol > 0.00008:
                        upbit.sell_market_order(coin, vol)
                        send_telegram(f"🕤 타임클로즈 전량매도: {coin}")
                        positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}
            time.sleep(1800)
            continue

        # 일일 드로다운 계산
        current_equity = get_total_equity()
        drawdown = (current_equity - daily_start_equity) / daily_start_equity

        # 매매 대상 상위 5개 코인
        tickers = pyupbit.get_tickers(fiat="KRW")
        top5 = sorted(
            [(t, (pyupbit.get_ohlcv(t, 'day', 1)['volume'].iloc[-1] * pyupbit.get_ohlcv(t, 'day', 1)['close'].iloc[-1])) for t in tickers if t not in ["KRW-BTC","KRW-DOGE"]],
            key=lambda x: x[1], reverse=True
        )[:5]
        top_coins = [t[0] for t in top5]

        for coin in top_coins:
            # 신규 진입 제한: 일일 드로다운 -2% 밑이면 스킵
            if drawdown <= MAX_DAILY_DRAWDOWN:
                print("[드로다운 과다] 신규 진입 중지")
                break

            # 포지션 초기화
            if coin not in positions:
                positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

            # 5분봉 데이터
            df5 = pyupbit.get_ohlcv(coin, interval="minute5", count=50)
            if df5 is None: continue

            # 거래량 필터
            vol_ma20 = df5['volume'].rolling(20).mean().iloc[-1]
            if df5['volume'].iloc[-1] < vol_ma20:
                continue

            last_close = df5['close'].iloc[-1]
            price = get_current_price(coin)
            if price is None or abs(price/last_close - 1) > SLIPPAGE_THRESHOLD:
                continue

            # 보조지표
            sma5 = df5['close'].rolling(5).mean().iloc[-1]
            sma15= df5['close'].rolling(15).mean().iloc[-1]
            rsi  = get_rsi(df5['close']).iloc[-1]
            atr  = get_atr(df5)

            pos = positions[coin]
            krw_balance = upbit.get_balance('KRW')

            # 진입 조건
            if not pos['holding']:
                if is_uptrend_multi(coin) and sma5 > sma15 and rsi < 40:
                    # 적응적 포지션 사이징
                    vol_ratio = max(0.1, 1 - (atr / price))
                    invest_ratio = BASE_INVEST_RATIO * vol_ratio * REINVEST_RATIO
                    amount_krw = krw_balance * invest_ratio
                    if amount_krw > 5000:
                        vol_to_buy = amount_krw / price
                        upbit.buy_limit_order(coin, price * 1.0005, vol_to_buy)
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
                # 최고가 업데이트
                if price > pos['high_price']:
                    pos['high_price'] = price

                # 추가 매수
                if not pos['added'] and pnl <= -0.02:
                    amount_krw = krw_balance * BASE_INVEST_RATIO * REINVEST_RATIO
                    if amount_krw > 5000:
                        vol2 = amount_krw / price
                        upbit.buy_limit_order(coin, price * 1.0005, vol2)
                        # 평균 단가 계산
                        new_entry = (entry + price) / 2
                        pos.update({'entry_price': new_entry, 'added': True})
                        send_telegram(f"📉 추가 매수: {coin}\n가격: {price:,.0f}")

                # ATR 기반 익절/손절
                stop_loss = entry - atr * 1.2
                tp1 = entry + atr * 1.0
                tp2 = entry + atr * 2.0

                # 부분 익절 (첫 TP)
                if not pos['partial_taken'] and price >= tp1:
                    vol = upbit.get_balance(coin)
                    if vol > 0.00008:
                        upbit.sell_limit_order(coin, price * 0.9995, vol * 0.5)
                        pos['partial_taken'] = True
                        send_telegram(f"💰 부분 익절: {coin}\n가격: {price:,.0f}\n수익률: {pnl*100:.2f}%")

                # 전량 청산 (두번째 TP 또는 STOP)
                if price >= tp2 or price <= stop_loss:
                    vol = upbit.get_balance(coin)
                    if vol > 0.00008:
                        upbit.sell_limit_order(coin, price * 0.9995, vol)
                        send_telegram(f"🚨 전량 청산: {coin}\n진입가: {entry:,.0f}\n현재가: {price:,.0f}\n수익률: {pnl*100:.2f}%")
                        positions[coin] = {"holding": False, "entry_price": 0, "high_price": 0, "added": False, "partial_taken": False}

        time.sleep(5)

    except Exception:
        err = traceback.format_exc()
        print(err)
        send_telegram(f"[자동매매 오류]\n{err}")
        time.sleep(60)
