"""ربات تلگرام معامله اسپات — اجرا روی VPS:  python bot.py"""
import asyncio
import html
import logging
import time
from datetime import datetime, timezone

import ccxt
from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

from core import market, paper
from core.config import ALLOWED_USERS, BOT_TOKEN, EXCHANGE, FEE, MAX_OPEN_TRADES, MONITOR_INTERVAL, PAPER_TRADE_SIZE, TF
from core.strategies import ORDER, STRATEGIES
from core.suggest import backtest_for, build_suggestion

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

FLOWS = {"bt": "📊 بک‌تست استراتژی", "trade": "🚀 شروع معامله", "sug": "💡 پیشنهاد معاملاتی"}
WELCOME = ("👋 <b>ربات معامله اسپات</b>\n"
           "تحلیل، بک‌تست و معامله شبیه‌سازی‌شده — فقط بازار اسپات (بدون اهرم و شورت).\n\n"
           "یکی از گزینه‌ها را انتخاب کنید 👇")
DISCLAIMER = "⚠️ تحلیل آماری است و توصیه مالی یا تضمین سود نیست."


def kb(rows) -> Markup:
    return Markup([[Btn(text, callback_data=data) for text, data in row] for row in rows])


MAIN = kb([[("📊 بک‌تست استراتژی", "m:bt"), ("🚀 شروع معامله", "m:trade")],
           [("📈 گزارش سود و زیان", "m:pnl"), ("💡 پیشنهاد معاملاتی", "m:sug")]])
SYMBOL_KB = kb([[("TRX", "c:TRX"), ("BTC", "c:BTC"), ("ETH", "c:ETH"), ("SOL", "c:SOL")], [("🏠 منوی اصلی", "menu")]])


# ───────────────────────── ابزارها ─────────────────────────
async def show(update: Update, text: str, markup: Markup | None = None):
    kw = dict(parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, **kw)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
    else:
        await update.effective_message.reply_text(text, **kw)


def fp(x: float) -> str:
    return f"{x:.6f}" if x < 0.01 else f"{x:.5f}" if x < 1 else f"{x:.4f}" if x < 100 else f"{x:.2f}"


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def err_text(e: Exception) -> str:
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, ccxt.BaseError):
        m = str(e)
        if "451" in m or "restricted" in m.lower() or "forbidden" in m.lower():
            return f"صرافی {EXCHANGE} از موقعیت این سرور مسدود است؛ EXCHANGE را در .env عوض کنید."
        return f"خطا در ارتباط با صرافی {EXCHANGE}: {html.escape(m[:150])}"
    log.exception("unhandled error")
    return "خطای داخلی؛ لاگ سرور را بررسی کنید."


# ───────────────────────── دسترسی ─────────────────────────
async def gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if ALLOWED_USERS and u and u.id not in ALLOWED_USERS:
        if update.callback_query:
            await update.callback_query.answer("⛔ دسترسی ندارید", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("⛔ شما به این ربات دسترسی ندارید.")
        raise ApplicationHandlerStop


# ───────────────────────── مراحل گفتگو ─────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await show(update, WELCOME, MAIN)


async def ask_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE, flow: str):
    ctx.user_data.clear()
    ctx.user_data.update(flow=flow, await_symbol=True)
    await show(update, f"<b>{FLOWS[flow]}</b>\n\n💱 نماد ارز را بنویسید (مثلاً <code>trx</code> یا <code>btc</code>) "
                       f"یا از دکمه‌های زیر انتخاب کنید:\n<i>فقط جفت‌ارزهای اسپات USDT</i>", SYMBOL_KB)


async def choose_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw: str):
    if len(raw) > 20:
        return await show(update, "❌ نماد نامعتبر است. دوباره بفرستید.", SYMBOL_KB)
    try:
        sym = await asyncio.to_thread(market.resolve_symbol, raw)
    except Exception as e:
        return await show(update, f"❌ {err_text(e)}\nدوباره نماد را بفرستید یا از دکمه‌ها انتخاب کنید.", SYMBOL_KB)
    ctx.user_data.update(symbol=sym, await_symbol=False)
    ctx.user_data.pop("strategy", None)
    await ask_strategy(update, ctx)


async def ask_strategy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    lines = [f"<b>{FLOWS[ud['flow']]}</b>", f"💱 ارز: <b>{ud['symbol']}</b>", "", "🧠 استراتژی مورد نظر را انتخاب کنید:", ""]
    for i, k in enumerate(ORDER, 1):
        lines.append(f"{i}. <b>{STRATEGIES[k].name}</b>\n    <i>{STRATEGIES[k].desc}</i>")
    btns = [(f"{i}. {STRATEGIES[k].short}", f"s:{k}") for i, k in enumerate(ORDER, 1)]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([("💱 تغییر ارز", f"m:{ud['flow']}"), ("🏠 منو", "menu")])
    await show(update, "\n".join(lines), kb(rows))


async def ask_tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    await show(update, f"<b>{FLOWS[ud['flow']]}</b>\n💱 <b>{ud['symbol']}</b> | 🧠 <b>{STRATEGIES[ud['strategy']].name}</b>\n\n"
                       f"⏱ تایم‌فریم را انتخاب کنید:",
               kb([[("⏱ 1H", "t:1h"), ("⏱ 4H", "t:4h")],
                   [("◀️ تغییر استراتژی", "re:strat"), ("🏠 منو", "menu")]]))


# ───────────────────────── بک‌تست ─────────────────────────
def verdict(st: dict) -> str:
    if st.get("empty"):
        return "ℹ️ در این بازه هیچ معامله‌ای انجام نشد."
    pf = st["profit_factor"] or 0
    if st["trades"] < 30:
        return f"⚠️ تعداد معاملات کم است ({st['trades']})؛ نتیجه از نظر آماری قابل اتکا نیست."
    if st["total_return"] > 0 and pf >= 1.3 and st["total_return"] > st["buy_hold"]:
        return "✅ در این بازه از «خرید و نگهداری» بهتر عمل کرده؛ اما تضمینی برای آینده نیست."
    if st["total_return"] > 0 and pf > 1:
        return "🟡 سودده بوده ولی برتری روشنی ندارد؛ با حجم کم و محتاطانه استفاده کنید."
    return "❌ در این بازه سودآور نبوده؛ استفاده از آن روی این ارز/تایم‌فریم توصیه نمی‌شود."


def format_backtest(sym: str, res: dict) -> str:
    st, strat, tf = res["stats"], STRATEGIES[res["strategy"]], res["tf"]
    head = (f"📊 <b>بک‌تست {sym}</b>\n🧠 {strat.name} | ⏱ {TF[tf]['label']}\n"
            f"📅 {day(res['start'])} تا {day(res['end'])} ({st['days']} روز)\n"
            f"🔰 فقط اسپات • کارمزد {FEE * 100:.1f}٪ هر سمت • ورود روی close کندل سیگنال\n")
    if st.get("empty"):
        return head + "\n" + verdict(st)
    pf = "—" if st["profit_factor"] is None else f"{st['profit_factor']:.2f}"
    per = lambda n, b: f"{n}: میانگین <b>{b['avg']:+.3f}%</b> | نرخ خطا {b['loss_rate']:.0f}% (از {b['active']} دوره فعال)"
    months = " | ".join(f"{m['period'][2:]}: {m['ret']:+.1f}%" for m in st["monthly_returns"][-6:])
    reasons = {"SL": "حد ضرر", "BE": "سر‌به‌سر", "TP3": "تارگت ۳", "SIGNAL": "سیگنال خروج", "TIME": "زمان"}
    rs = "، ".join(f"{reasons[k]} {v}" for k, v in st["reasons"].items() if k in reasons)
    return "\n".join([
        head,
        f"🧾 معاملات: <b>{st['trades']}</b> | نرخ برد: <b>{st['win_rate']:.1f}%</b> | نرخ خطا: <b>{st['loss_rate']:.1f}%</b>",
        f"💰 سود کل: <b>{st['total_return']:+.2f}%</b> | خرید و نگهداری: {st['buy_hold']:+.2f}%",
        f"📉 حداکثر افت سرمایه: {st['max_dd']:.2f}% | ضریب سود: {pf} | بیشترین باخت متوالی: {st['max_loss_streak']}",
        f"✅ میانگین هر برد: {st['avg_win']:+.2f}% | ❌ میانگین هر باخت: {st['avg_loss']:+.2f}%",
        f"🎯 رسیدن به تارگت ۱: {st['tp1_rate']:.0f}% معاملات | خروج‌ها: {rs}",
        "",
        "<b>سود و خطا بر حسب دوره</b>",
        per("روزانه", st["daily"]), per("ماهانه", st["monthly"]),
        per("سالانه" + (" (برآورد)" if st["days"] < 360 else ""), st["yearly"]),
        f"\n🗓 بازده ماهانه اخیر: {months}",
        f"\n{verdict(st)}\n{DISCLAIMER}\nاخبار در بک‌تست لحاظ نمی‌شود.",
    ])


async def flow_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    await show(update, f"⏳ در حال دریافت داده‌های یک‌ساله {ud['symbol']} ({TF[ud['tf']]['label']}) و اجرای بک‌تست...\n"
                       f"ممکن است تا یک دقیقه طول بکشد.")
    try:
        res = await asyncio.to_thread(backtest_for, ud["symbol"], ud["strategy"], ud["tf"])
        text = format_backtest(ud["symbol"], res)
    except Exception as e:
        text = f"❌ {err_text(e)}"
    await show(update, text, kb([[("🔁 استراتژی دیگر", "re:strat"), ("⏱ تایم‌فریم دیگر", "re:tf")],
                                 [("💡 پیشنهاد با همین تنظیمات", "go:sug"), ("💱 ارز دیگر", "m:bt")],
                                 [("🏠 منوی اصلی", "menu")]]))


# ───────────────────────── پیشنهاد / شروع معامله ─────────────────────────
def format_suggestion(s: dict, trade_mode: bool) -> str:
    act = {"BUY": "🟢 خرید", "SELL": "🔴 خروج / عدم خرید", "WAIT": "🟡 صبر"}[s["action"]]
    L, strat = s["levels"], STRATEGIES[s["key"]]
    out = [f"{'🚀' if trade_mode else '💡'} <b>{s['symbol']}</b> — {act}",
           f"🧠 {strat.name} | ⏱ {TF[s['tf']]['label']} | فقط اسپات",
           f"💰 قیمت فعلی: <code>{fp(s['price'])}</code>", f"ℹ️ {s['note']}"]
    if s["action"] == "BUY" or trade_mode:
        head = "\n📌 سطوح پیشنهادی:" if s["action"] == "BUY" else "\n📌 سطوح در صورت ورود با ریسک خودتان:"
        out += [head, f"🎯 ورود: <code>{fp(L['entry'])}</code>",
                f"🛑 حد ضرر: <code>{fp(L['stop_loss'])}</code> ({L['stop_pct']:+.2f}%)"]
        out += [f"✅ تارگت {i} ({t['r']:g}R): <code>{fp(t['price'])}</code> ({t['pct']:+.2f}%)"
                for i, t in enumerate(L["targets"], 1)]
        out.append("با هر تارگت ۱/۳ حجم فروخته می‌شود و پس از تارگت ۱ حد ضرر به نقطه ورود می‌رود.")
        out.append(f"💡 ریسک هر معامله را حداکثر ۱٪ کل سرمایه نگه دارید (فاصله حد ضرر {s['risk_pct']:.2f}٪).")
    out.append(f"\n📈 EMA200: {'صعودی ✅' if s['ema200_up'] else 'نزولی ❌'} | EMA20/50: "
               f"{'صعودی ✅' if s['ema_up'] else 'نزولی ❌'} | RSI {s['rsi']:.0f} | نوسان ATR {s['atr_pct']:.2f}٪")
    n = s["news"]
    out.append(f"📰 اخبار ۴۸ ساعت: {n['asset_count']} خبر ارز (سنتیمنت {n['asset_sent']:+.2f}) | "
               f"{n['market_count']} خبر بازار ({n['market_sent']:+.2f})")
    for it in n["asset"][:2]:
        out.append(f"• <a href=\"{html.escape(it['link'])}\">{html.escape(it['title'][:80])}</a>")
    if s["news_warning"]:
        out.append("⚠️ اخبار اخیر منفی است؛ احتیاط بیشتر داشته باشید.")
    st = s["stats"]
    if st and not st.get("empty"):
        out.append(f"\n📊 بک‌تست ۱ ساله همین ترکیب: نرخ برد {st['win_rate']:.0f}% | نرخ خطا {st['loss_rate']:.0f}% | "
                   f"سود {st['total_return']:+.1f}% | افت {st['max_dd']:.1f}% ({st['trades']} معامله)")
    elif st:
        out.append("\n📊 بک‌تست ۱ ساله: معامله‌ای ثبت نشد.")
    out.append("\n" + DISCLAIMER)
    return "\n".join(out)


async def flow_suggest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = ctx.user_data
    trade_mode = ud["flow"] == "trade"
    await show(update, f"⏳ در حال تحلیل چارت و اخبار {ud['symbol']} ({TF[ud['tf']]['label']})...")
    rows = [[("🔄 بروزرسانی", "go:refresh"), ("🔁 استراتژی دیگر", "re:strat")], [("🏠 منوی اصلی", "menu")]]
    try:
        s = await asyncio.to_thread(build_suggestion, ud["symbol"], ud["strategy"], ud["tf"])
        ud["pending"] = {"s": s, "ts": time.time(), "forced": s["action"] != "BUY"}
        text = format_suggestion(s, trade_mode)
        if trade_mode:
            label = "✅ تایید و شروع معامله (شبیه‌سازی)" if s["action"] == "BUY" else "⚠️ ورود به‌هرحال با قیمت فعلی (شبیه‌سازی)"
            rows.insert(0, [(label, "open")])
        elif s["action"] == "BUY":
            rows.insert(0, [("🚀 شروع معامله با این پیشنهاد", "open")])
    except Exception as e:
        text = f"❌ {err_text(e)}"
    await show(update, text, kb(rows))


async def do_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pend = ctx.user_data.get("pending")
    if not pend or time.time() - pend["ts"] > 300:
        return await show(update, "⌛ پیشنهاد منقضی شده (قیمت تغییر کرده است). دوباره تحلیل بگیرید.",
                          kb([[("🔄 تحلیل مجدد", "go:refresh")], [("🏠 منو", "menu")]]))
    u, s = update.effective_user, pend["s"]
    try:
        tid = await asyncio.to_thread(paper.open_from_suggestion, u.id, update.effective_chat.id, s, pend["forced"])
    except Exception as e:
        return await show(update, f"❌ {err_text(e)}", kb([[("📈 گزارش معاملات", "m:pnl"), ("🏠 منو", "menu")]]))
    ctx.user_data.pop("pending", None)
    L = s["levels"]
    await show(update, f"✅ <b>معامله شبیه‌سازی‌شده #{tid} باز شد</b>\n{s['symbol']} | {STRATEGIES[s['key']].name} | {TF[s['tf']]['label']}\n"
                       f"💵 سرمایه مجازی: {PAPER_TRADE_SIZE:g} USDT\n🎯 ورود: <code>{fp(L['entry'])}</code>\n"
                       f"🛑 حد ضرر: <code>{fp(L['stop_loss'])}</code>\n"
                       + "\n".join(f"✅ تارگت {i}: <code>{fp(t['price'])}</code>" for i, t in enumerate(L["targets"], 1))
                       + "\n\n🔔 ربات هر دقیقه قیمت را بررسی می‌کند و با رسیدن به تارگت/حد ضرر یا سیگنال خروج به شما پیام می‌دهد."
                       "\n<i>این معامله فقط شبیه‌سازی است و پولی جابه‌جا نمی‌شود.</i>",
               kb([[("📈 گزارش سود و زیان", "m:pnl"), ("🏠 منو", "menu")]]))


# ───────────────────────── گزارش سود و زیان ─────────────────────────
def format_report(r: dict) -> str:
    P = r["periods"]
    line = lambda name, k: f"{name}: <b>{P[k]['usdt']:+.2f} USDT</b> ({P[k]['pct']:+.2f}%) — {P[k]['n']} معامله"
    out = ["📈 <b>گزارش سود و زیان (معاملات شبیه‌سازی‌شده)</b>", f"💵 سرمایه مجازی هر معامله: {r['size']:g} USDT", "",
           line("📅 امروز", "today"), line("۷ روز اخیر", "d7"), line("۳۰ روز اخیر (ماهانه)", "d30"),
           line("۳۶۵ روز اخیر (سالانه)", "d365"), line("کل", "all")]
    if r["closed_n"]:
        out.append(f"\n🧾 معاملات بسته: {r['closed_n']} | برد {r['wins']} | باخت {r['losses']} | "
                   f"نرخ برد {r['wins'] / r['closed_n'] * 100:.0f}% | نرخ خطا {r['losses'] / r['closed_n'] * 100:.0f}%")
        out.append(f"میانگین هر برد: {r['avg_win']:+.2f}% | میانگین هر باخت: {r['avg_loss']:+.2f}%")
    else:
        out.append("\nهنوز معامله بسته‌شده‌ای ندارید.")
    if r["open"]:
        out.append(f"\n🟢 <b>معاملات باز ({len(r['open'])})</b>")
        for t in r["open"]:
            tp = " ".join(f"TP{i}{'✅' if t[f'hit{i}'] else '⏳'}" for i in (1, 2, 3))
            now = f"{fp(t['price'])}" if t["price"] else "—"
            out.append(f"#{t['id']} <b>{t['symbol']}</b> | {STRATEGIES[t['strategy']].short} | {TF[t['tf']]['label']}\n"
                       f"   ورود {fp(t['entry_price'])} → فعلی {now} | SL {fp(t['sl'])} | {tp}\n"
                       f"   سود/زیان فعلی: <b>{t['total'] * 100:+.2f}%</b> (≈ {t['total'] * t['size']:+.2f} USDT)")
    if r["last_closed"]:
        out.append("\n🔒 <b>آخرین معاملات بسته</b>")
        names = {"SL": "حد ضرر", "BE": "سر‌به‌سر", "TP3": "تارگت ۳", "SIGNAL": "سیگنال خروج", "TIME": "زمان", "MANUAL": "دستی"}
        for t in r["last_closed"]:
            out.append(f"#{t['id']} {t['symbol']} {'🟢' if t['realized'] > 0 else '🔴'} {t['realized'] * 100:+.2f}% "
                       f"({names.get(t['close_reason'], t['close_reason'])})")
    out.append("\n<i>نتایج شبیه‌سازی است؛ هزینه لغزش قیمت (slippage) لحاظ نشده.</i>")
    return "\n".join(out)


async def show_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show(update, "⏳ در حال تهیه گزارش...")
    try:
        r = await asyncio.to_thread(paper.report, update.effective_user.id)
        rows = [[(f"❌ بستن #{t['id']} {t['symbol']}", f"x:{t['id']}")] for t in r["open"]]
        text = format_report(r)
    except Exception as e:
        rows, text = [], f"❌ {err_text(e)}"
    rows.append([("🔄 بروزرسانی", "m:pnl"), ("🏠 منو", "menu")])
    await show(update, text, kb(rows))


async def do_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE, tid: int):
    try:
        text = await asyncio.to_thread(paper.close_manual, tid, update.effective_user.id)
    except Exception as e:
        text = f"❌ {err_text(e)}"
    await show(update, text, kb([[("📈 گزارش", "m:pnl"), ("🏠 منو", "menu")]]))


# ───────────────────────── مسیریابی دکمه‌ها و متن ─────────────────────────
async def run_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data["flow"] == "bt":
        await flow_backtest(update, ctx)
    else:
        await flow_suggest(update, ctx)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q, ud, data = update.callback_query, ctx.user_data, update.callback_query.data
    await q.answer()
    if ud.get("busy"):
        return                                   # درخواست قبلی در حال انجام است
    ud["busy"] = True
    try:
        if data == "menu":
            ud.clear()
            await show(update, WELCOME, MAIN)
        elif data == "m:pnl":
            await show_pnl(update, ctx)
        elif data.startswith("m:"):
            await ask_symbol(update, ctx, data[2:])
        elif data.startswith("c:"):
            if "flow" not in ud:
                return await show(update, WELCOME, MAIN)
            await choose_symbol(update, ctx, data[2:])
        elif data.startswith("s:") and data[2:] in STRATEGIES and "symbol" in ud:
            ud["strategy"] = data[2:]
            await ask_tf(update, ctx)
        elif data.startswith("t:") and data[2:] in TF and "strategy" in ud:
            ud["tf"] = data[2:]
            await run_flow(update, ctx)
        elif data == "re:strat" and "symbol" in ud:
            await ask_strategy(update, ctx)
        elif data == "re:tf" and "strategy" in ud:
            await ask_tf(update, ctx)
        elif data == "go:sug" and "tf" in ud:
            ud["flow"] = "sug"
            await run_flow(update, ctx)
        elif data == "go:refresh" and "tf" in ud:
            await run_flow(update, ctx)
        elif data == "open":
            await do_open(update, ctx)
        elif data.startswith("x:") and data[2:].isdigit():
            await do_close(update, ctx, int(data[2:]))
        else:
            ud.clear()
            await show(update, "⌛ این منو منقضی شده است. از ابتدا شروع کنید 👇", MAIN)
    except Exception as e:
        log.exception("button error")
        await show(update, f"❌ {err_text(e)}", kb([[("🏠 منو", "menu")]]))
    finally:
        ud.pop("busy", None)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("await_symbol"):
        return await show(update, "از دکمه‌های منو استفاده کنید 👇", MAIN)
    if ctx.user_data.get("busy"):
        return
    ctx.user_data["busy"] = True
    try:
        await choose_symbol(update, ctx, update.message.text.strip())
    finally:
        ctx.user_data.pop("busy", None)


async def monitor(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        msgs = await asyncio.to_thread(paper.check_all)
    except Exception:
        log.exception("monitor failed")
        return
    for chat_id, text in msgs:
        try:
            await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            log.exception("send failed")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled exception", exc_info=ctx.error)


async def post_init(app: Application):
    paper.init()
    try:
        await asyncio.to_thread(market.get_exchange)
        log.info("Exchange %s ready", EXCHANGE)
    except Exception as e:
        log.warning("Exchange init failed (%s) — will retry on demand", e)


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN در فایل .env تنظیم نشده است.")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler(["start", "menu"], cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(monitor, interval=MONITOR_INTERVAL, first=20)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
