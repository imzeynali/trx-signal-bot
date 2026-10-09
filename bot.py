"""
ربات سیگنال اسپات ارز دیجیتال (شروع با TRX/USDT)
- داده قیمت از صرافی (ccxt) | اندیکاتورها | امتیازدهی | اخبار RSS + سنتیمنت
- حد ضرر و تارگت بر پایه ATR | بک‌تست یک‌ساله با آمار روزانه/ماهانه/سالانه
تحلیل آموزشی است و توصیه مالی نیست.
"""
import asyncio
import html
import json
import logging
import os
import time
from pathlib import Path

import ccxt
import feedparser
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

# ───────────────────────── تنظیمات ─────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
EXCHANGE = os.getenv("EXCHANGE", "binance")       # binance / kucoin / okx / mexc ...
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
BACKTEST_DAYS = int(os.getenv("BACKTEST_DAYS", "365"))
AUTO_INTERVAL_SEC = int(os.getenv("AUTO_INTERVAL_SEC", "3600"))

BUY_THRESHOLD = 4        # امتیاز حداقل برای خرید
SELL_THRESHOLD = -3      # امتیاز حداکثر برای خروج
ATR_MULT = 1.5           # فاصله حد ضرر = 1.5 * ATR  (برابر 1R)
RR_TARGETS = (1.0, 2.0, 3.0)   # تارگت‌ها بر حسب R
BACKTEST_TP_INDEX = 1    # بک‌تست با تارگت دوم (2R) کامل خارج می‌شود
FEE = 0.001              # کارمزد 0.1% هر سمت
MAX_HOLD_CANDLES = 72
DAILY_FILTER = os.getenv("DAILY_FILTER", "1") == "1"   # خرید فقط در روند روزانه صعودی
WARMUP_DAYS = 60         # برای محاسبه EMA روزانه

STATE_FILE = Path("state.json")
NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]
KEYWORDS = {
    "TRX": ["tron", "trx", "justin sun"],
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
}
MARKET_KEYWORDS = ["bitcoin", "crypto market", "fed ", "sec ", "etf", "regulation"]

_vader = SentimentIntensityAnalyzer()
_exchange = None
_stats_cache: dict = {}


# ───────────────────────── وضعیت (watchlist) ─────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"symbols": ["TRX/USDT"], "subs": [], "last": {}}


def save_state(st: dict):
    STATE_FILE.write_text(json.dumps(st, indent=2))


# ───────────────────────── داده بازار ─────────────────────────
def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = getattr(ccxt, EXCHANGE)({"enableRateLimit": True})
        _exchange.load_markets()
    return _exchange


def normalize_symbol(text: str) -> str:
    s = text.upper().replace("-", "/").strip()
    return s if "/" in s else f"{s}/USDT"


def fetch_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    ex = get_exchange()
    step = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - days * 86400 * 1000
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + step
        if len(batch) < 1000 or since >= ex.milliseconds():
            break
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms")
    return df.drop(columns="ts")


# ───────────────────────── اندیکاتورها و امتیاز ─────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l = df["close"], df["high"], df["low"]
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()

    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()

    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    sma = c.rolling(20).mean()
    sd = c.rolling(20).std()
    df["bb_up"], df["bb_lo"] = sma + 2 * sd, sma - 2 * sd
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df


def compute_score(d: pd.DataFrame) -> pd.Series:
    """امتیاز تکنیکال بین حدوداً -8 تا +8"""
    s = pd.Series(0.0, index=d.index)
    s += np.where(d.close > d.ema200, 2, -2)
    s += np.where(d.ema20 > d.ema50, 1, -1)
    rising = d.macd_hist > d.macd_hist.shift()
    s += np.where((d.macd_hist > 0) & rising, 1, np.where((d.macd_hist < 0) & ~rising, -1, 0))
    s += np.select([d.rsi < 30, d.rsi < 45, d.rsi > 75, d.rsi > 65], [2, 1, -2, -1], 0)
    s += np.where(d.close < d.bb_lo, 1, np.where(d.close > d.bb_up, -1, 0))
    strong_vol = d.volume > 1.2 * d.vol_ma
    s += np.where(strong_vol & (d.close > d.open), 1, np.where(strong_vol & (d.close < d.open), -1, 0))
    return s


def daily_trend(daily_close: pd.Series) -> pd.Series:
    """روند روزانه صعودی: close > EMA50 و EMA20 > EMA50 (بر اساس روزهای بسته‌شده)"""
    e20 = daily_close.ewm(span=20, adjust=False).mean()
    e50 = daily_close.ewm(span=50, adjust=False).mean()
    ok = (daily_close > e50) & (e20 > e50)
    ok.iloc[:50] = False                      # دوره گرم‌شدن
    return ok


# ───────────────────────── اخبار ─────────────────────────
def fetch_news(symbol: str) -> dict:
    base = symbol.split("/")[0]
    keys = KEYWORDS.get(base, [base.lower()])
    cutoff = time.time() - 48 * 3600
    asset, market = [], []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.warning("feed error %s: %s", url, e)
            continue
        for e in feed.entries[:50]:
            ts = time.mktime(e.published_parsed) if getattr(e, "published_parsed", None) else time.time()
            if ts < cutoff:
                continue
            text = f"{e.get('title', '')}. {e.get('summary', '')}".lower()
            item = {
                "title": e.get("title", ""),
                "link": e.get("link", ""),
                "sent": _vader.polarity_scores(text)["compound"],
            }
            if any(k in text for k in keys):
                asset.append(item)
            elif any(k in text for k in MARKET_KEYWORDS):
                market.append(item)
    avg = lambda xs: float(np.mean([x["sent"] for x in xs])) if xs else 0.0
    return {"asset": asset, "market": market, "asset_sent": avg(asset), "market_sent": avg(market)}


def news_adjustment(news: dict) -> int:
    adj = 0
    if news["asset"]:
        adj += 1 if news["asset_sent"] > 0.25 else -1 if news["asset_sent"] < -0.25 else 0
    if news["market"]:
        adj += 1 if news["market_sent"] > 0.25 else -1 if news["market_sent"] < -0.25 else 0
    return adj


# ───────────────────────── سیگنال ─────────────────────────
def build_signal(symbol: str) -> dict:
    df = add_indicators(fetch_ohlcv(symbol, TIMEFRAME, 30))
    row = df.iloc[-2]                       # آخرین کندل بسته‌شده
    tech = float(compute_score(df).iloc[-2])
    news = fetch_news(symbol)
    adj = news_adjustment(news)
    total = tech + adj

    price = float(df["close"].iloc[-1])
    entry, atr = float(row.close), float(row.atr)
    risk = atr * ATR_MULT
    dfd = fetch_ohlcv(symbol, "1d", 150)
    daily_ok = bool(daily_trend(dfd["close"]).iloc[-2])   # آخرین روز بسته‌شده
    blocked = False
    if total >= BUY_THRESHOLD:
        action = "BUY"
        if DAILY_FILTER and not daily_ok:
            action, blocked = "HOLD", True
    elif total <= SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"
    sl = entry - risk
    targets = [entry + risk * r for r in RR_TARGETS]
    return dict(symbol=symbol, action=action, price=price, entry=entry, sl=sl,
                targets=targets, risk_pct=risk / entry * 100, tech=tech, adj=adj,
                total=total, daily_ok=daily_ok, blocked=blocked, rsi=float(row.rsi), ema_trend=bool(row.close > row.ema200),
                news=news, atr=atr)


def pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.2f}%"


def fmt_price(x: float) -> str:
    return f"{x:.5f}" if x < 1 else f"{x:.4f}" if x < 100 else f"{x:.2f}"


def format_signal(s: dict) -> str:
    emoji = {"BUY": "🟢 خرید", "SELL": "🔴 خروج / فروش", "HOLD": "🟡 صبر / نگهداری"}[s["action"]]
    lines = [
        f"📊 <b>{s['symbol']}</b> — {emoji}",
        f"💰 قیمت فعلی: <code>{fmt_price(s['price'])}</code>",
        f"🧮 امتیاز نهایی: <b>{s['total']:+.0f}</b> (تکنیکال {s['tech']:+.0f} | اخبار {s['adj']:+d})",
        f"📈 روند بلندمدت (EMA200): {'صعودی ✅' if s['ema_trend'] else 'نزولی ❌'} | RSI: {s['rsi']:.1f}",
    ]
    lines.append(f"🗓 روند روزانه: {'صعودی ✅' if s['daily_ok'] else 'غیرصعودی ❌'}")
    if s["action"] == "BUY":
        lines += [
            "",
            f"🎯 ورود: <code>{fmt_price(s['entry'])}</code>",
            f"🛑 حد ضرر: <code>{fmt_price(s['sl'])}</code> ({pct(s['sl'], s['entry'])})",
        ]
        for i, t in enumerate(s["targets"], 1):
            lines.append(f"✅ تارگت {i}: <code>{fmt_price(t)}</code> ({pct(t, s['entry'])})")
        lines.append("⚖️ ریسک به ریوارد: 1:1 / 1:2 / 1:3")
        lines.append("💡 پیشنهاد: در هر معامله حداکثر ۱٪ از کل سرمایه را ریسک کنید.")
    elif s["action"] == "SELL":
        lines.append("\n⚠️ شرایط برای خرید جدید مناسب نیست؛ اگر پوزیشن دارید خروج/کاهش را بررسی کنید.")
    elif s["blocked"]:
        lines.append("\n⛔ سیگنال تکنیکال خرید بود ولی روند روزانه صعودی نیست؛ برای کاهش سیگنال اشتباه، خرید فیلتر شد.")
    else:
        lines.append("\n⏳ سیگنال قوی وجود ندارد؛ منتظر تایید بمانید.")

    n = s["news"]
    lines.append(f"\n📰 اخبار ۴۸ ساعت اخیر: {len(n['asset'])} خبر مرتبط با ارز | {len(n['market'])} خبر بازار")
    lines.append(f"   سنتیمنت ارز: {n['asset_sent']:+.2f} | بازار: {n['market_sent']:+.2f}")
    for it in sorted(n["asset"], key=lambda x: -abs(x["sent"]))[:3]:
        lines.append(f"• <a href=\"{html.escape(it['link'])}\">{html.escape(it['title'][:90])}</a>")
    lines.append("\n⚠️ این تحلیل آماری است و تضمین سود نیست.")
    return "\n".join(lines)


# ───────────────────────── بک‌تست و آمار ─────────────────────────
def backtest(df: pd.DataFrame) -> pd.Series:
    d = add_indicators(df).dropna()
    score = compute_score(d).to_numpy()
    close, high, low, atr = (d[c].to_numpy() for c in ("close", "high", "low", "atr"))
    idx = d.index
    if DAILY_FILTER:
        daily_close = df["close"].resample("1D").last().dropna()
        tr = daily_trend(daily_close).shift(1).fillna(False)   # روز قبل بسته‌شده، بدون نگاه به آینده
        allow = tr.reindex(idx.floor("D")).fillna(False).to_numpy(dtype=bool)
    else:
        allow = np.ones(len(d), dtype=bool)
    pos, trades = None, []
    for i in range(len(d)):
        if pos:
            ex_price = None
            if low[i] <= pos["sl"]:
                ex_price = pos["sl"]                       # محافظه‌کارانه: اول حد ضرر
            elif high[i] >= pos["tp"]:
                ex_price = pos["tp"]
            elif score[i] <= SELL_THRESHOLD or i - pos["i"] >= MAX_HOLD_CANDLES:
                ex_price = close[i]
            if ex_price is not None:
                ret = ex_price * (1 - FEE) / (pos["entry"] * (1 + FEE)) - 1
                trades.append((idx[i], ret))
                pos = None
        elif score[i] >= BUY_THRESHOLD and allow[i]:
            risk = atr[i] * ATR_MULT
            pos = dict(i=i, entry=close[i], sl=close[i] - risk,
                       tp=close[i] + risk * RR_TARGETS[BACKTEST_TP_INDEX])
    if not trades:
        return pd.Series(dtype=float)
    return pd.Series([r for _, r in trades], index=[t for t, _ in trades])


def summarize(ret: pd.Series, start, end) -> dict | None:
    if ret.empty:
        return None
    eq = (1 + ret).cumprod()
    total = float(eq.iloc[-1])
    days = max((end - start).days, 1)
    months, years = days / 30.44, days / 365.0

    def periods(freq):
        g = ret.groupby(ret.index.to_period(freq)).apply(lambda x: (1 + x).prod() - 1)
        return {"active": len(g), "loss_rate": float((g < 0).mean() * 100),
                "avg_active": float(g.mean() * 100)}

    return dict(
        trades=len(ret), win_rate=float((ret > 0).mean() * 100),
        loss_rate=float((ret <= 0).mean() * 100), total_return=(total - 1) * 100,
        max_dd=float((eq / eq.cummax() - 1).min() * 100), days=days,
        avg_win=float(ret[ret > 0].mean() * 100) if (ret > 0).any() else 0.0,
        avg_loss=float(ret[ret <= 0].mean() * 100) if (ret <= 0).any() else 0.0,
        daily=dict(avg=(total ** (1 / days) - 1) * 100, **periods("D")),
        monthly=dict(avg=(total ** (1 / months) - 1) * 100, **periods("M")),
        yearly=dict(avg=(total ** (1 / years) - 1) * 100, **periods("Y")),
    )


def get_stats(symbol: str) -> dict | None:
    cached = _stats_cache.get(symbol)
    if cached and time.time() - cached[0] < 6 * 3600:
        return cached[1]
    df = fetch_ohlcv(symbol, TIMEFRAME, BACKTEST_DAYS + WARMUP_DAYS)
    stats = summarize(backtest(df), df.index[0] + pd.Timedelta(days=WARMUP_DAYS), df.index[-1])
    _stats_cache[symbol] = (time.time(), stats)
    return stats


def format_stats(symbol: str, st: dict | None) -> str:
    if not st:
        return f"برای {symbol} در بازه بک‌تست معامله‌ای ثبت نشد."
    row = lambda name, p: (f"<b>{name}</b>\n   میانگین سود (هندسی): {p['avg']:+.3f}%\n"
                           f"   نرخ خطا (دوره‌های زیان‌ده): {p['loss_rate']:.1f}% از {p['active']} دوره فعال")
    return "\n".join([
        f"📉📈 <b>آمار بک‌تست {symbol}</b> ({st['days']} روز، تایم‌فریم {TIMEFRAME})",
        f"تعداد معاملات: {st['trades']} | نرخ برد: {st['win_rate']:.1f}% | نرخ خطا: {st['loss_rate']:.1f}%",
        f"میانگین سود هر برد: {st['avg_win']:+.2f}% | میانگین ضرر هر باخت: {st['avg_loss']:+.2f}%",
        f"سود کل: {st['total_return']:+.2f}% | حداکثر افت سرمایه: {st['max_dd']:.2f}%",
        "", row("روزانه", st["daily"]), row("ماهانه", st["monthly"]), row("سالانه", st["yearly"]),
        "\n⚠️ نتایج گذشته تضمین آینده نیست. کارمزد 0.1% هر سمت لحاظ شده؛ اخبار در بک‌تست لحاظ نمی‌شود.",
    ])


# ───────────────────────── دستورات تلگرام ─────────────────────────
HELP = (
    "🤖 <b>ربات سیگنال اسپات</b>\n\n"
    "/signal [نماد] — سیگنال کامل با حد ضرر و تارگت (پیش‌فرض TRX)\n"
    "/stats [نماد] — آمار بک‌تست: سود و خطای روزانه/ماهانه/سالانه\n"
    "/news [نماد] — اخبار مرتبط\n"
    "/list — لیست ارزها\n"
    "/add نماد — افزودن ارز (فقط ادمین)\n"
    "/remove نماد — حذف ارز (فقط ادمین)\n"
    "/subscribe — دریافت خودکار سیگنال‌های جدید\n"
    "/unsubscribe — لغو دریافت خودکار\n\n"
    "⚠️ توصیه مالی نیست؛ همیشه مدیریت سرمایه را رعایت کنید."
)


def arg_symbol(ctx: ContextTypes.DEFAULT_TYPE, default="TRX/USDT") -> str:
    return normalize_symbol(ctx.args[0]) if ctx.args else default


async def reply(update: Update, text: str):
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await reply(update, HELP)


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = arg_symbol(ctx)
    await update.message.reply_text("⏳ در حال تحلیل چارت و اخبار...")
    try:
        s = await asyncio.to_thread(build_signal, sym)
        await reply(update, format_signal(s))
    except Exception as e:
        log.exception("signal")
        await update.message.reply_text(f"خطا: {e}")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = arg_symbol(ctx)
    await update.message.reply_text("⏳ در حال بک‌تست داده‌های یک سال اخیر...")
    try:
        st = await asyncio.to_thread(get_stats, sym)
        await reply(update, format_stats(sym, st))
    except Exception as e:
        log.exception("stats")
        await update.message.reply_text(f"خطا: {e}")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sym = arg_symbol(ctx)
    n = await asyncio.to_thread(fetch_news, sym)
    items = (n["asset"] or n["market"])[:6]
    if not items:
        return await update.message.reply_text("خبر مرتبطی در ۴۸ ساعت اخیر پیدا نشد.")
    out = [f"📰 <b>اخبار {sym}</b> (سنتیمنت ارز {n['asset_sent']:+.2f} | بازار {n['market_sent']:+.2f})"]
    for it in items:
        out.append(f"• {it['sent']:+.2f} <a href=\"{html.escape(it['link'])}\">{html.escape(it['title'][:100])}</a>")
    await reply(update, "\n".join(out))


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ارزها: " + ", ".join(load_state()["symbols"]))


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("فقط ادمین می‌تواند ارز اضافه کند.")
    if not ctx.args:
        return await update.message.reply_text("مثال: /add BTC")
    sym = normalize_symbol(ctx.args[0])
    ex = await asyncio.to_thread(get_exchange)
    if sym not in ex.markets:
        return await update.message.reply_text(f"{sym} در {EXCHANGE} پیدا نشد.")
    st = load_state()
    if sym not in st["symbols"]:
        st["symbols"].append(sym)
        save_state(st)
    await update.message.reply_text(f"✅ {sym} اضافه شد.")


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("فقط ادمین.")
    if not ctx.args:
        return await update.message.reply_text("مثال: /remove BTC")
    sym = normalize_symbol(ctx.args[0])
    st = load_state()
    if sym in st["symbols"]:
        st["symbols"].remove(sym)
        save_state(st)
    await update.message.reply_text(f"🗑 {sym} حذف شد.")


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    cid = update.effective_chat.id
    if cid not in st["subs"]:
        st["subs"].append(cid)
        save_state(st)
    await update.message.reply_text("🔔 فعال شد. با تغییر سیگنال به خرید/خروج پیام می‌گیرید.")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    cid = update.effective_chat.id
    if cid in st["subs"]:
        st["subs"].remove(cid)
        save_state(st)
    await update.message.reply_text("🔕 لغو شد.")


async def auto_job(ctx: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    if not st["subs"]:
        return
    for sym in st["symbols"]:
        try:
            s = await asyncio.to_thread(build_signal, sym)
        except Exception:
            log.exception("auto %s", sym)
            continue
        prev = st["last"].get(sym)
        st["last"][sym] = s["action"]
        if s["action"] in ("BUY", "SELL") and s["action"] != prev:
            for cid in st["subs"]:
                await ctx.bot.send_message(cid, format_signal(s), parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
    save_state(st)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است (فایل .env).")
    app = Application.builder().token(BOT_TOKEN).build()
    for name, fn in [("start", cmd_start), ("help", cmd_start), ("signal", cmd_signal),
                     ("stats", cmd_stats), ("news", cmd_news), ("list", cmd_list),
                     ("add", cmd_add), ("remove", cmd_remove),
                     ("subscribe", cmd_subscribe), ("unsubscribe", cmd_unsubscribe)]:
        app.add_handler(CommandHandler(name, fn))
    app.job_queue.run_repeating(auto_job, interval=AUTO_INTERVAL_SEC, first=30)
    app.run_polling()


if __name__ == "__main__":
    main()
