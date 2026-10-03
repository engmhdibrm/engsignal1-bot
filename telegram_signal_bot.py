"""
بوت إشارات تداول للعملات الرقمية (Binance) - يرسل الإشارات على تليجرام فقط
المستخدم هو من ينفذ الصفقة يدويًا - لا تنفيذ آلي هنا

الاستراتيجية: تقاطع EMA9/EMA21 + فلتر RSI
"""

import os
import csv
import time
import requests
import pandas as pd
import ta
from datetime import datetime
from binance.client import Client  # pip install python-binance

# ========================
# الإعدادات - عدّل القيم دي
# ========================

# بيانات Binance (API Key للقراءة فقط - لا تعطي صلاحيات تداول أو سحب!)
BINANCE_API_KEY = "ضع_المفتاح_هنا"
BINANCE_API_SECRET = "ضع_السيكرت_هنا"

# بيانات بوت تليجرام
TELEGRAM_BOT_TOKEN = "ضع_التوكن_هنا"
TELEGRAM_CHAT_ID = "ضع_CHAT_ID_هنا"

# العملات والفريم الزمني المراقَب
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]   # عدّل حسب العملات اللي تتابعها
TIMEFRAME = Client.KLINE_INTERVAL_1HOUR  # فريم الساعة - مناسب لمضاربة فترات 1-4 أيام
CHECK_INTERVAL_SECONDS = 900  # كل 15 دقيقة (مفيش داعي لفحص أسرع مع فريم الساعة)

# إعدادات المؤشرات - مُعدّلة لمضاربة الفترات القصيرة (Swing Trading، لا تتجاوز 4 أيام)
EMA_FAST = 20   # يعكس اتجاه متوسط المدى بدل الحركة اللحظية
EMA_SLOW = 50   # اتجاه أوضح وأقل تذبذبًا، مناسب لصفقات تمتد لأيام
RSI_PERIOD = 14

# إعدادات إدارة رأس المال
TOTAL_CAPITAL_USD = 2000       # إجمالي رأس المال
STOP_LOSS_PERCENT = 3.0        # نسبة وقف الخسارة من سعر الدخول
RISK_PER_TRADE_PERCENT = 1.0   # % من رأس المال تقبل خسارته في الصفقة الواحدة لو ضرب الستوب
                                 # (مش حجم الصفقة نفسه - ده بيتحسب تلقائيًا تحت)
RISK_REWARD_RATIO = 2.0        # نسبة هدف الربح لوقف الخسارة (1:2 يعني الهدف ضعف مسافة الستوب)

# إعدادات تتبع الأداء
TRADES_LOG_FILE = "trades_log.csv"
MAX_TRADE_DURATION_HOURS = 96   # 4 أيام - لو الصفقة فضلت مفتوحة أكتر من كده تتقفل تلقائيًا بسعر السوق

# ========================
# تهيئة الاتصال
# ========================
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# لتجنب تكرار نفس الإشارة أكتر من مرة على نفس الشمعة
last_signal = {symbol: None for symbol in SYMBOLS}

CSV_COLUMNS = [
    "trade_id", "symbol", "direction", "entry_price", "stop_loss_price",
    "take_profit_price", "position_size_usd", "max_risk_usd",
    "entry_time", "status", "exit_price", "exit_time", "result", "pnl_usd"
]


def init_trades_log():
    """ينشئ ملف تسجيل الصفقات لو مش موجود"""
    if not os.path.exists(TRADES_LOG_FILE):
        with open(TRADES_LOG_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def log_new_trade(symbol: str, signal: str, price: float, sizing: dict):
    """يسجّل صفقة جديدة بحالة OPEN في ملف CSV"""
    trade_id = f"{symbol}_{int(time.time())}"
    with open(TRADES_LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writerow({
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": signal,
            "entry_price": price,
            "stop_loss_price": sizing["stop_loss_price"],
            "take_profit_price": sizing["take_profit_price"],
            "position_size_usd": sizing["position_size_usd"],
            "max_risk_usd": sizing["max_risk_usd"],
            "entry_time": datetime.now().isoformat(),
            "status": "OPEN",
            "exit_price": "",
            "exit_time": "",
            "result": "",
            "pnl_usd": ""
        })
    return trade_id


def read_open_trades() -> list:
    """يرجع كل الصفقات المفتوحة حاليًا من ملف CSV"""
    if not os.path.exists(TRADES_LOG_FILE):
        return []
    with open(TRADES_LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if row["status"] == "OPEN"]


def update_trade_result(trade_id: str, exit_price: float, result: str, pnl_usd: float):
    """يحدّث حالة صفقة معينة بعد إغلاقها (TP / SL / منتهية الوقت)"""
    rows = []
    with open(TRADES_LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        if row["trade_id"] == trade_id:
            row["status"] = "CLOSED"
            row["exit_price"] = exit_price
            row["exit_time"] = datetime.now().isoformat()
            row["result"] = result
            row["pnl_usd"] = round(pnl_usd, 2)

    with open(TRADES_LOG_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def check_open_trades():
    """
    يفحص كل الصفقات المفتوحة، ويقفلها تلقائيًا لو السعر لمس
    وقف الخسارة أو هدف الربح، أو لو تجاوزت المدة القصوى المسموحة
    """
    open_trades = read_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        symbol = trade["symbol"]
        direction = trade["direction"]
        entry_price = float(trade["entry_price"])
        sl_price = float(trade["stop_loss_price"])
        tp_price = float(trade["take_profit_price"])
        position_size = float(trade["position_size_usd"])

        try:
            current_price = float(client.get_symbol_ticker(symbol=symbol)["price"])
        except Exception as e:
            print(f"⚠️ تعذر جلب السعر الحالي لـ {symbol}: {e}")
            continue

        hit_tp = (direction == "LONG" and current_price >= tp_price) or \
                 (direction == "SHORT" and current_price <= tp_price)
        hit_sl = (direction == "LONG" and current_price <= sl_price) or \
                 (direction == "SHORT" and current_price >= sl_price)

        # فحص انتهاء المدة القصوى (4 أيام)
        entry_time = datetime.fromisoformat(trade["entry_time"])
        hours_elapsed = (datetime.now() - entry_time).total_seconds() / 3600
        expired = hours_elapsed >= MAX_TRADE_DURATION_HOURS

        if hit_tp or hit_sl or expired:
            if direction == "LONG":
                pnl_percent = (current_price - entry_price) / entry_price
            else:
                pnl_percent = (entry_price - current_price) / entry_price

            pnl_usd = position_size * pnl_percent
            result = "TP" if hit_tp else ("SL" if hit_sl else "EXPIRED")

            update_trade_result(trade["trade_id"], current_price, result, pnl_usd)

            result_ar = {"TP": "✅ تحقق هدف الربح", "SL": "❌ ضرب وقف الخسارة", "EXPIRED": "⏱️ انتهت المدة (4 أيام)"}[result]
            pnl_sign = "+" if pnl_usd >= 0 else ""
            send_telegram_message(
                f"{result_ar}\n"
                f"العملة: <b>{symbol}</b>\n"
                f"سعر الخروج: <b>{round(current_price, 4)}</b>\n"
                f"النتيجة: <b>{pnl_sign}{round(pnl_usd, 2)}$</b>"
            )
            print(f"[{datetime.now()}] صفقة {trade['trade_id']} اتقفلت: {result} | PnL: {round(pnl_usd, 2)}$")


def get_performance_summary() -> str:
    """يحسب إحصائيات الأداء الكلي من الصفقات المغلقة ويرجعها كنص جاهز للإرسال"""
    if not os.path.exists(TRADES_LOG_FILE):
        return "لا توجد صفقات مسجلة بعد."

    with open(TRADES_LOG_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        closed_trades = [row for row in reader if row["status"] == "CLOSED"]

    if not closed_trades:
        return "لا توجد صفقات مغلقة بعد لحساب الأداء."

    total = len(closed_trades)
    wins = [t for t in closed_trades if float(t["pnl_usd"]) > 0]
    losses = [t for t in closed_trades if float(t["pnl_usd"]) <= 0]
    total_pnl = sum(float(t["pnl_usd"]) for t in closed_trades)
    win_rate = (len(wins) / total) * 100

    return (
        f"📊 <b>تقرير الأداء</b>\n"
        f"عدد الصفقات المغلقة: {total}\n"
        f"صفقات رابحة: {len(wins)} | صفقات خاسرة: {len(losses)}\n"
        f"نسبة النجاح: {round(win_rate, 1)}%\n"
        f"إجمالي الربح/الخسارة: {'+' if total_pnl >= 0 else ''}{round(total_pnl, 2)}$\n"
        f"رأس المال الحالي التقريبي: {round(TOTAL_CAPITAL_USD + total_pnl, 2)}$"
    )


def send_telegram_message(message: str):
    """إرسال رسالة على تليجرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ خطأ في إرسال رسالة تليجرام: {response.text}")
    except Exception as e:
        print(f"⚠️ فشل الاتصال بتليجرام: {e}")


def get_klines_df(symbol: str, interval: str, limit: int = 150) -> pd.DataFrame:
    """جلب بيانات الشموع من Binance وتحويلها لـ DataFrame"""
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def check_signal(df: pd.DataFrame) -> str | None:
    """
    يحسب المؤشرات ويرجع إشارة: 'LONG', 'SHORT', أو None
    """
    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()

    # الشمعة الحالية (آخر واحدة) والشمعة اللي قبلها (عشان نتأكد إن التقاطع حصل الآن)
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # شرط تقاطع EMA صاعد (Golden Cross محلي)
    bullish_cross = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    # شرط تقاطع EMA هابط
    bearish_cross = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    if bullish_cross and 40 <= curr["rsi"] <= 70:
        return "LONG"
    elif bearish_cross and 30 <= curr["rsi"] <= 60:
        return "SHORT"

    return None


def calculate_position_sizing(entry_price: float, signal: str) -> dict:
    """
    يحسب سعر وقف الخسارة، والمبلغ المقترح للمخاطرة، وحجم الصفقة التقريبي
    بناءً على: رأس المال الكلي، نسبة المخاطرة المسموحة للصفقة الواحدة، ونسبة الستوب لوز
    """
    take_profit_percent = STOP_LOSS_PERCENT * RISK_REWARD_RATIO

    if signal == "LONG":
        stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENT / 100)
        take_profit_price = entry_price * (1 + take_profit_percent / 100)
    else:  # SHORT
        stop_loss_price = entry_price * (1 + STOP_LOSS_PERCENT / 100)
        take_profit_price = entry_price * (1 - take_profit_percent / 100)

    # أقصى مبلغ تقبل خسارته في هذه الصفقة تحديدًا
    max_risk_usd = TOTAL_CAPITAL_USD * (RISK_PER_TRADE_PERCENT / 100)

    # حجم الصفقة (قيمة الدخول بالدولار) بحيث لو ضرب الستوب، الخسارة = max_risk_usd فقط
    position_size_usd = max_risk_usd / (STOP_LOSS_PERCENT / 100)

    # لا تتجاوز حجم الصفقة إجمالي رأس المال (في حال كانت النسب المدخلة كبيرة)
    position_size_usd = min(position_size_usd, TOTAL_CAPITAL_USD)

    # الربح المتوقع لو تحقق هدف الربح بالكامل
    potential_profit_usd = max_risk_usd * RISK_REWARD_RATIO

    return {
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "max_risk_usd": max_risk_usd,
        "position_size_usd": position_size_usd,
        "potential_profit_usd": potential_profit_usd
    }


def format_signal_message(symbol: str, signal: str, price: float, rsi: float) -> str:
    emoji = "🟢" if signal == "LONG" else "🔴"
    direction_ar = "شراء (Long)" if signal == "LONG" else "بيع (Short)"

    sizing = calculate_position_sizing(price, signal)

    return (
        f"{emoji} <b>إشارة {direction_ar}</b>\n"
        f"العملة: <b>{symbol}</b>\n"
        f"سعر الدخول المقترح: <b>{round(price, 4)}</b>\n"
        f"وقف الخسارة (-{STOP_LOSS_PERCENT}%): <b>{round(sizing['stop_loss_price'], 4)}</b>\n"
        f"هدف الربح (نسبة 1:{RISK_REWARD_RATIO:.0f}): <b>{round(sizing['take_profit_price'], 4)}</b>\n"
        f"حجم الصفقة المقترح: <b>{round(sizing['position_size_usd'], 2)}$</b> "
        f"(من إجمالي {TOTAL_CAPITAL_USD}$)\n"
        f"أقصى خسارة محتملة: <b>{round(sizing['max_risk_usd'], 2)}$</b> "
        f"({RISK_PER_TRADE_PERCENT}% من رأس المال)\n"
        f"الربح المحتمل عند تحقق الهدف: <b>{round(sizing['potential_profit_usd'], 2)}$</b>\n"
        f"RSI: {round(rsi, 2)}\n"
        f"الفريم: ساعة (1h)\n"
        f"الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"⚠️ هذه إشارة آلية وليست نصيحة استثمارية. راجع السوق وقرر بنفسك قبل التنفيذ."
    )


def run_bot():
    print("🚀 بدأ تشغيل بوت الإشارات...")
    init_trades_log()
    send_telegram_message("✅ بوت الإشارات بدأ العمل وجاهز لمراقبة السوق.")

    while True:
        # 1. فحص الصفقات المفتوحة حاليًا (TP/SL/انتهاء المدة)
        check_open_trades()

        # 2. فحص إشارات دخول جديدة
        for symbol in SYMBOLS:
            try:
                df = get_klines_df(symbol, TIMEFRAME)
                signal = check_signal(df)

                # إرسال وتسجيل فقط لو الإشارة جديدة (مختلفة عن آخر إشارة لنفس العملة)
                if signal and signal != last_signal[symbol]:
                    price = df.iloc[-1]["close"]
                    rsi_value = df.iloc[-1]["rsi"] if "rsi" in df.columns else 0
                    sizing = calculate_position_sizing(price, signal)

                    message = format_signal_message(symbol, signal, price, rsi_value)
                    send_telegram_message(message)

                    trade_id = log_new_trade(symbol, signal, price, sizing)
                    print(f"[{datetime.now()}] إشارة {signal} على {symbol} - تم تسجيلها ({trade_id})")
                    last_signal[symbol] = signal

            except Exception as e:
                print(f"⚠️ خطأ أثناء معالجة {symbol}: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
