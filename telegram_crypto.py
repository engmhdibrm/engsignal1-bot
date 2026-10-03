import os
import time
import ccxt
import pandas as pd
import requests

EXCHANGE_NAME = 'kucoin'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8777940737:AAFzbUsFXVxRVlC7dnS8uID8Ag4Xw53UBfI')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '458299401')

TOTAL_CAPITAL = 2000.0
POSITION_SIZE = 200.0 

SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
TIMEFRAME = '1h'

def send_telegram_message(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, json=payload)
        if not response.ok:
            print(f'فشل إرسال التيليجرام: {response.text}')
    except Exception as e:
        print(f'خطأ في الاتصال بتيليجرام: {e}')

exchange_class = getattr(ccxt, EXCHANGE_NAME)
exchange = exchange_class({'enableRateLimit': True})

def check_market_for_symbol(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # حساب مؤشرات EMA و RSI باستخدام بايثون و Pandas مباشرة بدون مكتبات خارجية معقدة
        df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # معادلة حساب RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        if len(df) < 2:
            return

        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        close_price = last_row['close']
        rsi = last_row['RSI']

        buy_signal = (
            prev_row['EMA_9'] <= prev_row['EMA_21']
            and last_row['EMA_9'] > last_row['EMA_21']
            and rsi < 65
        )

        sell_signal = (
            prev_row['EMA_9'] >= prev_row['EMA_21']
            and last_row['EMA_9'] < last_row['EMA_21']
        )

        if buy_signal:
            stop_loss = close_price * 0.98
            take_profit = close_price * 1.05
            msg = (
                f'🚨 *تنبيه إشارة شراء (BUY)* 🚨\n\n'
                f'🪙 *الزوج:* `{symbol}`\n'
                f'💰 *سعر الدخول المقترح:* `{close_price:.2f}`\n'
                f'📊 *مؤشر RSI:* `{rsi:.2f}`\n'
                f'💵 *حجم الصفقة:* `${POSITION_SIZE}`\n\n'
                f'🛑 *وقف الخسارة:* `{stop_loss:.2f}` (-2%)\n'
                f'🎯 *هدف الربح:* `{take_profit:.2f}` (+5%)\n\n'
                f'_تنفيذ يدوي._'
            )
            send_telegram_message(msg)
            print(f'تم إرسال إشارة شراء للعملة {symbol}')

        elif sell_signal:
            msg = (
                f'⚠️ *تنبيه إشارة بيع / خروج (SELL)* ⚠️\n\n'
                f'🪙 *الزوج:* `{symbol}`\n'
                f'📉 *السعر الحالي:* `{close_price:.2f}`\n'
                f'📊 *مؤشر RSI:* `{rsi:.2f}`\n\n'
                f'_تقاطع سلبي._'
            )
            send_telegram_message(msg)
            print(f'تم إرسال إشارة بيع للعملة {symbol}')

    except Exception as e:
        print(f'خطأ في فحص العملة {symbol}: {e}')

def run_bot():
    print(f'--- جاري فحص جميع العملات في {time.strftime("%Y-%m-%d %H:%M:%S")} ---')
    for symbol in SYMBOLS:
        check_market_for_symbol(symbol)
        time.sleep(1)

if __name__ == '__main__':
    run_bot()
