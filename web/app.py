"""سرور وب: FastAPI  —  اجرا:  uvicorn web.app:app --host 0.0.0.0 --port 8000"""
import base64
import logging
import secrets
from pathlib import Path

import ccxt
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core import market
from core.backtest import run_backtest
from core.config import APP_PASSWORD, APP_USER, EXCHANGE
from core.strategy import WARMUP_DAYS, Params, build_signal
from core.util import clean

log = logging.getLogger("web")
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Spot Signal", docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD and request.url.path != "/healthz":
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = (secrets.compare_digest(user.encode(), APP_USER.encode())
                      and secrets.compare_digest(pw.encode(), APP_PASSWORD.encode()))
            except Exception:
                ok = False
        if not ok:
            return Response("Unauthorized", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="signals"'})
    return await call_next(request)


def params_dep(
    buy_threshold: float = Query(4.0, ge=1, le=8),
    sell_threshold: float = Query(-3.0, ge=-8, le=0),
    atr_mult: float = Query(1.5, ge=0.5, le=5),
    tp_r: float = Query(2.0, ge=0.5, le=6),
    fee: float = Query(0.001, ge=0, le=0.01),
    max_hold: int = Query(72, ge=6, le=500),
    daily_filter: int = Query(1),
) -> Params:
    return Params(buy_threshold, sell_threshold, atr_mult, tp_r, fee, max_hold, bool(daily_filter))


def guard(fn):
    try:
        return JSONResponse(clean(fn()))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ccxt.BaseError as e:
        msg = str(e)
        if "451" in msg or "restricted" in msg.lower() or "forbidden" in msg.lower():
            msg = f"صرافی {EXCHANGE} از موقعیت این سرور مسدود است؛ مقدار EXCHANGE را (مثلاً kucoin یا okx) تغییر دهید."
        else:
            msg = f"خطا در ارتباط با صرافی {EXCHANGE}: {msg[:200]}"
        raise HTTPException(502, msg)
    except Exception:
        log.exception("unhandled")
        raise HTTPException(500, "خطای داخلی سرور؛ لاگ سرور را بررسی کنید.")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/symbols")
def symbols():
    return guard(lambda: {"exchange": EXCHANGE, "symbols": market.list_symbols()})


@app.get("/api/analyze")
def analyze(symbol: str, p: Params = Depends(params_dep)):
    return guard(lambda: build_signal(market.resolve_symbol(symbol), p))


@app.get("/api/backtest")
def backtest(symbol: str, days: int = Query(365, ge=30, le=730), p: Params = Depends(params_dep)):
    def job():
        sym = market.resolve_symbol(symbol)
        df = market.fetch_ohlcv(sym, "1h", days + WARMUP_DAYS, ttl=900)
        if len(df) < 24 * (WARMUP_DAYS + 10):
            raise ValueError("تاریخچه این ارز برای بک‌تست کافی نیست؛ بازه کوتاه‌تر انتخاب کنید.")
        return {"symbol": sym, **run_backtest(df, p)}
    return guard(job)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
