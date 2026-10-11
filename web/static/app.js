(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (x) => {
    if (x == null) return '—';
    const a = Math.abs(x);
    return a < 0.01 ? x.toFixed(6) : a < 1 ? x.toFixed(5) : a < 100 ? x.toFixed(4) : x.toFixed(2);
  };
  const pct = (x, d = 2) => (x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(d) + '%');
  const cls = (x) => (x > 0 ? 'pos' : x < 0 ? 'neg' : '');
  const precisionFor = (p) => (p < 0.01 ? 6 : p < 1 ? 5 : p < 100 ? 4 : 2);
  const dt = (t) => new Date(t * 1000).toLocaleString('fa-IR', { dateStyle: 'short', timeStyle: 'short' });
  const REASON = { SL: 'حد ضرر', TP: 'تارگت', SIGNAL: 'سیگنال خروج', TIME: 'پایان زمان' };

  async function api(path, params) {
    const r = await fetch(`${path}?${new URLSearchParams(params)}`);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `خطای سرور (${r.status})`);
    return data;
  }

  // ───────────── چارت‌ها ─────────────
  const baseOpts = () => ({
    autoSize: true,
    layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
    grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2a2e39' },
    rightPriceScale: { borderColor: '#2a2e39' },
  });
  const candleOpts = { upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' };
  const setPrec = (series, price) => {
    const p = precisionFor(price);
    series.applyOptions({ priceFormat: { type: 'price', precision: p, minMove: Number((1 / 10 ** p).toFixed(p)) } });
  };

  const main = LightweightCharts.createChart($('#chart-main'), baseOpts());
  const candles = main.addCandlestickSeries(candleOpts);
  const line = (color) => main.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const ema = { ema20: line('#f6c23e'), ema50: line('#4e9af1'), ema200: line('#e056fd') };
  const vol = main.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', priceLineVisible: false, lastValueVisible: false });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  candles.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });

  const eqChart = LightweightCharts.createChart($('#chart-eq'), baseOpts());
  const eqSeries = eqChart.addAreaSeries({ lineColor: '#4e9af1', topColor: 'rgba(78,154,241,.35)', bottomColor: 'rgba(78,154,241,0)', lineWidth: 2 });
  const btChart = LightweightCharts.createChart($('#chart-bt'), baseOpts());
  const btCandles = btChart.addCandlestickSeries(candleOpts);

  let priceLines = [];

  // ───────────── ابزارهای رابط ─────────────
  const showError = (m) => { const e = $('#error'); e.textContent = '⚠️ ' + m; e.classList.remove('hidden'); };
  const hideError = () => $('#error').classList.add('hidden');
  const busy = (btn, on, label) => { btn.disabled = on; if (label) btn.textContent = on ? '⏳ در حال انجام...' : label; };

  function params() {
    return {
      buy_threshold: $('#p-buy').value, sell_threshold: $('#p-sell').value, atr_mult: $('#p-atr').value,
      tp_r: $('#p-tp').value, fee: String(Number($('#p-fee').value) / 100), max_hold: $('#p-hold').value,
      daily_filter: $('#p-daily').value,
    };
  }

  // ───────────── تحلیل و پیشنهاد ─────────────
  function renderSignal(s) {
    const title = { BUY: '🟢 خرید', SELL: '🔴 فروش / خروج', HOLD: '🟡 صبر (بدون سیگنال)' }[s.action];
    let h = `<h2>🎯 پیشنهاد معاملاتی — ${esc(s.symbol)}</h2>
      <div><span class="action ${s.action}">${title}</span></div>
      <p class="muted">قیمت فعلی: <b dir="ltr">${fmt(s.price)}</b> · کندل تحلیل‌شده: ${dt(s.candle_time)}<br>
      امتیاز نهایی <b>${s.score.total >= 0 ? '+' : ''}${s.score.total}</b> (تکنیکال ${s.score.tech >= 0 ? '+' : ''}${s.score.tech} | اخبار ${s.score.news >= 0 ? '+' : ''}${s.score.news})
      — آستانه خرید ${s.buy_threshold} / خروج ${s.sell_threshold}<br>
      RSI: ${s.rsi.toFixed(1)} · نوسان ATR: ${s.atr_pct.toFixed(2)}٪ · روند EMA200: ${s.ema200_up ? '✅ صعودی' : '❌ نزولی'} · روند روزانه: ${s.daily_up ? '✅ صعودی' : '❌ غیرصعودی'}</p>`;
    if (s.action === 'BUY') {
      const L = s.levels;
      h += `<table><tr><th>سطح</th><th class="num">قیمت</th><th class="num">درصد</th></tr>
        <tr><td>ورود (حوالی قیمت فعلی)</td><td class="num">${fmt(L.entry)}</td><td class="num">—</td></tr>
        <tr><td>🛑 حد ضرر (استاپ‌لاس)</td><td class="num neg">${fmt(L.stop_loss)}</td><td class="num neg">${pct(L.stop_pct)}</td></tr>
        ${L.targets.map((t, i) => `<tr><td>✅ تارگت ${i + 1} <span class="muted">(${t.r}R)</span></td><td class="num pos">${fmt(t.price)}</td><td class="num pos">${pct(t.pct)}</td></tr>`).join('')}
      </table><p class="muted">پیشنهاد: ریسک هر معامله حداکثر ۱٪ کل سرمایه؛ با رسیدن به تارگت ۱ حد ضرر را به نقطه ورود ببرید.</p>`;
    } else if (s.action === 'SELL') {
      h += `<p>⚠️ شرایط برای خرید جدید مناسب نیست. اگر پوزیشن دارید خروج یا کاهش حجم در قیمت فعلی <b dir="ltr">${fmt(s.price)}</b> را بررسی کنید.</p>
        <table>${s.rebuy.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${fmt(r.price)}</td><td class="num neg">${pct(r.pct)}</td></tr>`).join('')}</table>
        <p class="muted">سطوح بازخرید فقط برای تماشا هستند و تا دریافت سیگنال خرید جدید معتبر نیستند.</p>`;
    } else if (s.blocked) {
      h += `<p>⛔ امتیاز تکنیکال به حد خرید رسیده، اما روند روزانه صعودی نیست؛ برای کاهش سیگنال اشتباه، خرید فیلتر شد.</p>`;
    } else {
      h += `<p>⏳ سیگنال قوی وجود ندارد؛ منتظر تایید بمانید.</p>`;
    }
    h += `<ul class="reasons">${s.reasons.map((r) => `<li><span>${esc(r.label)}</span><b class="${cls(r.points)}" dir="ltr">${r.points > 0 ? '+' : ''}${r.points}</b></li>`).join('')}</ul>
      <div class="warn">تحلیل آماری است و تضمین سود نیست.</div>`;
    $('#signal-card').innerHTML = h;
  }

  function renderNews(n) {
    $('#news-sum').textContent = `ارز: ${n.asset_count} خبر (${n.asset_sent.toFixed(2)}) | بازار: ${n.market_count} خبر (${n.market_sent.toFixed(2)})`;
    const items = n.asset.length ? n.asset : n.market;
    const note = n.asset.length ? '' : '<li class="muted">خبر اختصاصی برای این ارز نبود؛ اخبار عمومی بازار:</li>';
    $('#news').innerHTML = items.length
      ? note + items.map((i) => {
        const c = i.sent > 0.25 ? 'p' : i.sent < -0.25 ? 'n' : 'z';
        return `<li><span class="sent ${c}">${i.sent.toFixed(2)}</span><span class="muted">${esc(i.source)} · ${dt(i.ts)}</span>
          <a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></li>`;
      }).join('')
      : '<li class="muted">خبری در ۴۸ ساعت اخیر پیدا نشد.</li>';
  }

  function renderChart(s) {
    const c = s.chart;
    [candles, ...Object.values(ema)].forEach((x) => setPrec(x, s.price));
    candles.setData(c.candles.map((k) => ({ time: k[0], open: k[1], high: k[2], low: k[3], close: k[4] })));
    vol.setData(c.candles.map((k) => ({ time: k[0], value: k[5], color: k[4] >= k[1] ? 'rgba(38,166,154,.4)' : 'rgba(239,83,80,.4)' })));
    for (const key of Object.keys(ema)) {
      ema[key].setData(c.candles.map((k, i) => ({ time: k[0], value: c[key][i] })).filter((p) => p.value != null));
    }
    priceLines.forEach((l) => candles.removePriceLine(l));
    priceLines = [];
    const add = (price, color, title) => priceLines.push(candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
    if (s.levels) {
      add(s.levels.entry, '#d1d4dc', 'ورود');
      add(s.levels.stop_loss, '#ef5350', 'SL');
      s.levels.targets.forEach((t, i) => add(t.price, '#26a69a', `TP${i + 1}`));
    }
    $('#chart-title').textContent = `${s.symbol} · 1h`;
    main.timeScale().fitContent();
  }

  async function analyze() {
    const symbol = $('#symbol').value.trim();
    if (!symbol) return showError('نماد ارز را وارد کنید.');
    hideError();
    const btn = $('#btn-analyze');
    busy(btn, true, '🔍 تحلیل و پیشنهاد');
    try {
      const s = await api('/api/analyze', { symbol, ...params() });
      $('#symbol').value = s.symbol;
      renderSignal(s); renderNews(s.news); renderChart(s);
    } catch (e) { showError(e.message); } finally { busy(btn, false, '🔍 تحلیل و پیشنهاد'); }
  }

  // ───────────── بک‌تست ─────────────
  function renderBacktest(r) {
    const st = r.stats;
    $('#bt-result').classList.remove('hidden');
    if (st.empty) {
      $('#kpis').innerHTML = '<div class="kpi">در این بازه با این پارامترها هیچ معامله‌ای انجام نشد.</div>';
      ['#period-table', '#month-bars', '#trades'].forEach((q) => { $(q).innerHTML = ''; });
      eqSeries.setData([]); btCandles.setData([]);
      return;
    }
    const k = (label, val, c = '') => `<div class="kpi"><span class="muted">${label}</span><b class="${c}">${val}</b></div>`;
    $('#kpis').innerHTML = [
      k('سود کل استراتژی', pct(st.total_return), cls(st.total_return)),
      k('خرید و نگهداری (همان بازه)', pct(st.buy_hold), cls(st.buy_hold)),
      k('تعداد معاملات', st.trades),
      k('نرخ برد', st.win_rate.toFixed(1) + '%', st.win_rate >= 50 ? 'pos' : ''),
      k('نرخ خطا (معاملات ضررده)', st.loss_rate.toFixed(1) + '%', 'neg'),
      k('میانگین سود هر برد', pct(st.avg_win), 'pos'),
      k('میانگین ضرر هر باخت', pct(st.avg_loss), 'neg'),
      k('ضریب سود (Profit Factor)', st.profit_factor == null ? '—' : st.profit_factor.toFixed(2), st.profit_factor > 1 ? 'pos' : 'neg'),
      k('حداکثر افت سرمایه', st.max_dd.toFixed(2) + '%', 'neg'),
      k('بیشترین باخت متوالی', st.max_loss_streak),
    ].join('');
    const row = (name, b, note = '') => `<tr><td>${name}${note}</td><td class="num ${cls(b.avg)}">${pct(b.avg, 3)}</td>
      <td class="num ${cls(b.avg_active)}">${pct(b.avg_active)}</td><td class="num neg">${b.loss_rate.toFixed(1)}%</td><td class="num">${b.active}</td></tr>`;
    $('#period-table').innerHTML = `<tr><th>دوره</th><th class="num">میانگین سود (هندسی)</th><th class="num">میانگین دوره‌های فعال</th><th class="num">نرخ خطا (دوره زیان‌ده)</th><th class="num">دوره فعال</th></tr>`
      + row('روزانه', st.daily) + row('ماهانه', st.monthly)
      + row('سالانه', st.yearly, st.days < 365 ? ' <span class="muted">(برآورد از کمتر از یک سال)</span>' : '');
    $('#month-bars').innerHTML = st.monthly_returns.map((m) => `<div class="bar">${m.period}<b class="${cls(m.ret)}">${pct(m.ret, 1)}</b></div>`).join('');

    eqSeries.setData(r.equity.map((e) => ({ time: e[0], value: e[1] })).filter((v, i, a) => i === 0 || v.time > a[i - 1].time));
    eqChart.timeScale().fitContent();

    setPrec(btCandles, r.candles[r.candles.length - 1][4]);
    btCandles.setData(r.candles.map((c) => ({ time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] })));
    const marks = [];
    r.trades.forEach((t) => {
      marks.push({ time: t.entry_time, position: 'belowBar', color: '#4e9af1', shape: 'arrowUp', text: 'B' });
      marks.push({ time: t.exit_time, position: 'aboveBar', color: t.ret > 0 ? '#26a69a' : '#ef5350', shape: 'arrowDown', text: pct(t.ret * 100, 1) });
    });
    marks.sort((a, b) => a.time - b.time);
    btCandles.setMarkers(marks);
    btChart.timeScale().fitContent();

    $('#trades').innerHTML = '<tr><th>ورود</th><th class="num">قیمت ورود</th><th>خروج</th><th class="num">قیمت خروج</th><th>دلیل</th><th class="num">نتیجه</th></tr>'
      + r.trades.slice(-60).reverse().map((t) => `<tr><td>${dt(t.entry_time)}</td><td class="num">${fmt(t.entry_price)}</td><td>${dt(t.exit_time)}</td>
        <td class="num">${fmt(t.exit_price)}</td><td>${REASON[t.reason] || t.reason}</td><td class="num ${cls(t.ret)}">${pct(t.ret * 100)}</td></tr>`).join('');
  }

  async function runBacktest() {
    const symbol = $('#symbol').value.trim();
    if (!symbol) return showError('نماد ارز را وارد کنید.');
    hideError();
    const btn = $('#btn-bt');
    busy(btn, true, '▶️ اجرای بک‌تست');
    $('#bt-msg').textContent = 'در حال دریافت تاریخچه و اجرای بک‌تست (ممکن است چند ثانیه طول بکشد)...';
    try {
      const r = await api('/api/backtest', { symbol, days: $('#p-days').value, ...params() });
      $('#symbol').value = r.symbol;
      $('#bt-msg').textContent = `${r.symbol} · ${r.stats.days} روز · ${dt(r.start)} تا ${dt(r.end)}`;
      renderBacktest(r);
    } catch (e) { $('#bt-msg').textContent = ''; showError(e.message); } finally { busy(btn, false, '▶️ اجرای بک‌تست'); }
  }

  // ───────────── راه‌اندازی ─────────────
  $('#btn-analyze').addEventListener('click', analyze);
  $('#btn-bt').addEventListener('click', runBacktest);
  $('#symbol').addEventListener('keydown', (e) => { if (e.key === 'Enter') analyze(); });
  document.querySelectorAll('[data-sym]').forEach((b) => b.addEventListener('click', () => { $('#symbol').value = b.dataset.sym; analyze(); }));

  api('/api/symbols', {}).then((d) => {
    $('#exchange').textContent = `صرافی: ${d.exchange} · ${d.symbols.length} جفت‌ارز اسپات`;
    $('#symbols').innerHTML = d.symbols.map((s) => `<option value="${esc(s)}">`).join('');
  }).catch((e) => { $('#exchange').textContent = 'اتصال به صرافی ناموفق'; showError(e.message); });

  analyze();
})();
