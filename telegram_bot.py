"""ربات تلگرام اختیاری؛ از همان هسته (core) وب استفاده می‌کند.  اجرا: python telegram_bot.py"""
import asyncio
import html
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from core import market
from core.backtest import run_backtest
from core.strategy import WARMUP_DAYS, Params, build_signal

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
AUTO_INTERVAL_SEC = int(os.getenv("AUTO_INTERVAL_SEC", "3600"))
STATE = Path("state.json")


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"symbols": ["TRX/USDT"], "subs": [], "last": {}}


def save_state(st: dict):
    STATE.write_text(json.dumps(st, indent=2))


def fp(x: float) -> str:
    return f"{x:.5f}" if x < 1 else f"{x:.4f}" if x < 100 else f"{x:.2f}"


def format_signal(s: dict) -> str:
    title = {"BUY": "🟢 خرید", "SELL": "🔴 خروج / فروش", "HOLD": "🟡 صبر"}[s["action"]]
    sc = s["score"]
    out = [f"📊 <b>{s['symbol']}</b> — {title}", f"💰 قیمت: <code>{fp(s['price'])}</code>",
           f"🧮 امتیاز: <b>{sc['total']:+.0f}</b> (تکنیکال {sc['tech']:+.0f} | اخبار {sc['news']:+d})",
           f"📈 EMA200: {'صعودی' if s['ema200_up'] else 'نزولی'} | روزانه: {'صعودی' if s['daily_up'] else 'غیرصعودی'} | RSI {s['rsi']:.1f}"]
    if s["levels"]:
        L = s["levels"]
        out += ["", f"🎯 ورود: <code>{fp(L['entry'])}</code>",
                f"🛑 حد ضرر: <code>{fp(L['stop_loss'])}</code> ({L['stop_pct']:+.2f}%)"]
        out += [f"✅ تارگت {i}: <code>{fp(t['price'])}</code> ({t['pct']:+.2f}%)" for i, t in enumerate(L["targets"], 1)]
    elif s["blocked"]:
        out.append("\n⛔ سیگنال خرید بود ولی روند روزانه صعودی نیست؛ فیلتر شد.")
    for it in s["news"]["asset"][:3]:
        out.append(f"• <a href=\"{html.escape(it['link'])}\">{html.escape(it['title'][:90])}</a>")
    out.append("\n⚠️ تحلیل آماری است؛ تضمین سود نیست.")
    return "\n".join(out)


def format_stats(sym: str, st: dict) -> str:
    if st.get("empty"):
        return f"برای {sym} معامله‌ای در بازه بک‌تست ثبت نشد."
    r = lambda n, b: f"<b>{n}</b>: میانگین {b['avg']:+.3f}% | نرخ خطا {b['loss_rate']:.1f}% ({b['active']} دوره)"
    return "\n".join([f"📉📈 <b>بک‌تست {sym}</b> ({st['days']} روز)",
                      f"معاملات {st['trades']} | برد {st['win_rate']:.1f}% | خطا {st['loss_rate']:.1f}%",
                      f"سود کل {st['total_return']:+.2f}% | خرید‌ونگهداری {st['buy_hold']:+.2f}% | افت {st['max_dd']:.2f}%",
                      r("روزانه", st["daily"]), r("ماهانه", st["monthly"]), r("سالانه", st["yearly"]),
                      "\n⚠️ نتایج گذشته تضمین آینده نیست."])


def sym_arg(ctx) -> str:
    return market.resolve_symbol(ctx.args[0] if ctx.args else "TRX")


async def reply(u: Update, t: str):
    await u.message.reply_text(t, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await reply(u, "🤖 /signal [نماد] — سیگنال\n/stats [نماد] — بک‌تست\n/list\n/add نماد (ادمین)\n/remove نماد (ادمین)\n"
                   "/subscribe — دریافت خودکار\n/unsubscribe\n\n⚠️ توصیه مالی نیست.")


async def cmd_signal(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        s = await asyncio.to_thread(lambda: build_signal(sym_arg(c)))
        await reply(u, format_signal(s))
    except Exception as e:
        await u.message.reply_text(f"خطا: {e}")


async def cmd_stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    def job():
        sym = sym_arg(c)
        df = market.fetch_ohlcv(sym, "1h", 365 + WARMUP_DAYS, ttl=900)
        return sym, run_backtest(df, Params())["stats"]
    try:
        await u.message.reply_text("⏳ در حال بک‌تست...")
        sym, st = await asyncio.to_thread(job)
        await reply(u, format_stats(sym, st))
    except Exception as e:
        await u.message.reply_text(f"خطا: {e}")


async def cmd_list(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("ارزها: " + ", ".join(load_state()["symbols"]))


async def cmd_add(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        return await u.message.reply_text("فقط ادمین.")
    try:
        sym = await asyncio.to_thread(lambda: sym_arg(c))
    except Exception as e:
        return await u.message.reply_text(f"خطا: {e}")
    st = load_state()
    if sym not in st["symbols"]:
        st["symbols"].append(sym)
        save_state(st)
    await u.message.reply_text(f"✅ {sym} اضافه شد.")


async def cmd_remove(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_user.id != ADMIN_ID:
        return await u.message.reply_text("فقط ادمین.")
    st = load_state()
    sym = (c.args[0].upper() if c.args else "")
    sym = sym if "/" in sym else sym + "/USDT"
    if sym in st["symbols"]:
        st["symbols"].remove(sym)
        save_state(st)
    await u.message.reply_text(f"🗑 {sym} حذف شد.")


async def cmd_sub(u: Update, c: ContextTypes.DEFAULT_TYPE):
    st, cid = load_state(), u.effective_chat.id
    if cid not in st["subs"]:
        st["subs"].append(cid)
        save_state(st)
    await u.message.reply_text("🔔 فعال شد.")


async def cmd_unsub(u: Update, c: ContextTypes.DEFAULT_TYPE):
    st, cid = load_state(), u.effective_chat.id
    if cid in st["subs"]:
        st["subs"].remove(cid)
        save_state(st)
    await u.message.reply_text("🔕 لغو شد.")


async def auto_job(c: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    if not st["subs"]:
        return
    for sym in st["symbols"]:
        try:
            s = await asyncio.to_thread(build_signal, sym)
        except Exception:
            logging.exception("auto %s", sym)
            continue
        prev, st["last"][sym] = st["last"].get(sym), s["action"]
        if s["action"] in ("BUY", "SELL") and s["action"] != prev:
            for cid in st["subs"]:
                await c.bot.send_message(cid, format_signal(s), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    save_state(st)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است.")
    app = Application.builder().token(BOT_TOKEN).build()
    for n, f in [("start", cmd_start), ("help", cmd_start), ("signal", cmd_signal), ("stats", cmd_stats),
                 ("list", cmd_list), ("add", cmd_add), ("remove", cmd_remove),
                 ("subscribe", cmd_sub), ("unsubscribe", cmd_unsub)]:
        app.add_handler(CommandHandler(n, f))
    app.job_queue.run_repeating(auto_job, interval=AUTO_INTERVAL_SEC, first=30)
    app.run_polling()


if __name__ == "__main__":
    main()
