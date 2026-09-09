/* 조회조건 → 대시보드 뷰모델 계산 (scripts/analytics.py 의 클라이언트 포팅).
   GitHub Pages(정적 호스팅)에서 data/*.json 만으로 동작한다.
   window.ANALYTICS.buildView({granularity, start, end, channels}, {inflow, channels: master, log}) */
(function () {
  "use strict";

  const PALETTE = ["#0067ac", "#ef8a2b", "#3aa757", "#d64550", "#8a63c9", "#00a1b5",
    "#c9a227", "#5b7089", "#e0748a", "#2f8f8f", "#7a6fbe", "#b0611f", "#4c78a8"];

  const GRAN_LABEL = {
    day:   { unit: "일", latest: "금일 유입",     delta: "전일 대비", col: "최근 이용건" },
    month: { unit: "월", latest: "해당 월 이용건", delta: "전월 대비", col: "월 이용건" },
    year:  { unit: "년", latest: "해당 년 이용건", delta: "전년 대비", col: "연 이용건" },
  };

  const round1 = (n) => Math.round(n * 10) / 10;
  function pct(cur, prev) { return prev === 0 ? null : round1(((cur - prev) / prev) * 100); }
  function daysInMonth(y, m) { return new Date(y, m, 0).getDate(); }
  function bucketOf(dateStr, gran) {
    return gran === "year" ? dateStr.slice(0, 4) : gran === "month" ? dateStr.slice(0, 7) : dateStr;
  }
  function todayISO() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function visibleChannels(master) {
    return (master.channels || [])
      .filter((c) => (c.display_yn || "Y") === "Y")
      .slice()
      .sort((a, b) => (a.sort ?? 999) - (b.sort ?? 999));
  }

  function buildView(q, data) {
    const gran = GRAN_LABEL[q.granularity] ? q.granularity : "day";
    const inflow = data.inflow || { records: [] };
    const master = data.channels || { channels: [] };
    const log = data.log || { logs: [] };

    const records = (inflow.records || []).slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    const allDates = records.map((r) => r.date);

    const disp = visibleChannels(master);
    const order = {}; disp.forEach((c, i) => (order[c.code] = i));
    const nameBy = {}, typeBy = {}, colorBy = {};
    disp.forEach((c, i) => { nameBy[c.code] = c.name || c.code; typeBy[c.code] = c.type || ""; colorBy[c.code] = PALETTE[i % PALETTE.length]; });

    let allowed = new Set(disp.map((c) => c.code));
    if (q.channels && q.channels.length) allowed = new Set(q.channels.filter((c) => allowed.has(c)));

    const pool = gran === "day" ? records.filter((r) => (r.period || "daily") === "daily") : records;
    const poolBuckets = [...new Set(pool.map((r) => bucketOf(r.date, gran)))].sort();

    let start = q.start || (poolBuckets[0] || bucketOf(todayISO(), gran));
    let end = q.end || (poolBuckets[poolBuckets.length - 1] || bucketOf(todayISO(), gran));
    if (start > end) { const t = start; start = end; end = t; }

    const inRange = pool.filter((r) => { const b = bucketOf(r.date, gran); return b >= start && b <= end; });

    const agg = {};
    for (const r of inRange) {
      const b = bucketOf(r.date, gran);
      const slot = (agg[b] = agg[b] || {});
      for (const c of r.channels || []) {
        if (!allowed.has(c.channel)) continue;
        const cs = (slot[c.channel] = slot[c.channel] || { y: 0, n: 0, total: 0 });
        cs.y += c.y; cs.n += c.n; cs.total += c.total;
      }
    }
    const buckets = Object.keys(agg).sort();

    const trend = [], cumulative = [];
    let run = 0;
    for (const b of buckets) {
      const rows = Object.values(agg[b]);
      const t = rows.reduce((s, x) => s + x.total, 0);
      const y = rows.reduce((s, x) => s + x.y, 0);
      const n = rows.reduce((s, x) => s + x.n, 0);
      run += t;
      trend.push({ date: b, total: t, y, n });
      cumulative.push({ date: b, total: run });
    }
    const period_total = { total: 0, y: 0, n: 0 };
    trend.forEach((d) => { period_total.total += d.total; period_total.y += d.y; period_total.n += d.n; });

    const latest = trend.length ? trend[trend.length - 1] : { date: null, total: 0, y: 0, n: 0 };

    let latest_partial = false;
    if (trend.length && allDates.length && gran !== "day") {
      const dmax = allDates[allDates.length - 1];
      const [yy, mm, dd] = dmax.split("-").map(Number);
      if (gran === "month") latest_partial = bucketOf(dmax, "month") === latest.date && dd < daysInMonth(yy, mm);
      else latest_partial = dmax.slice(0, 4) === latest.date && (mm < 12 || dd < 31);
    }

    let delta = { prev_date: null, total_pct: null, total_diff: 0, note: null };
    if (trend.length >= 2) {
      const prev = trend[trend.length - 2];
      if (latest_partial) delta.note = `최신 ${GRAN_LABEL[gran].unit}(${latest.date})은 아직 진행 중이라 비교 생략`;
      else delta = { prev_date: prev.date, total_pct: pct(latest.total, prev.total), total_diff: latest.total - prev.total, note: null };
    } else if (trend.length) delta.note = "비교할 이전 구간 없음";

    const lastB = buckets[buckets.length - 1] || null;
    const prevB = latest_partial ? null : (buckets.length >= 2 ? buckets[buckets.length - 2] : null);

    let by_channel = [...allowed].sort((a, b) => (order[a] ?? 999) - (order[b] ?? 999)).map((code) => {
      const series = buckets.map((b) => ({ date: b, total: (agg[b][code] || {}).total || 0 }));
      let p_total = 0, p_y = 0, p_n = 0;
      buckets.forEach((b) => { const x = agg[b][code]; if (x) { p_total += x.total; p_y += x.y; p_n += x.n; } });
      const cur = lastB ? agg[lastB][code] : null;
      const prv = prevB ? agg[prevB][code] : null;
      return {
        channel: code, name: nameBy[code] || code, type: typeBy[code] || "", color: colorBy[code] || "#888",
        today_total: (cur || {}).total || 0, today_y: (cur || {}).y || 0, today_n: (cur || {}).n || 0,
        period_total: p_total, period_y: p_y, period_n: p_n,
        share_pct: period_total.total ? round1((p_total / period_total.total) * 100) : 0,
        dod_pct: cur && prv ? pct(cur.total, prv.total) : null,
        series,
      };
    });
    by_channel.sort((a, b) => b.period_total - a.period_total);

    const lastLog = (log.logs && log.logs.length) ? log.logs[log.logs.length - 1] : {};

    return {
      query: { granularity: gran, start, end, channels: (q.channels || []).slice().sort() },
      labels: GRAN_LABEL[gran],
      buckets,
      as_of: {
        latest_date: allDates[allDates.length - 1] || null,
        record_count: records.length,
        last_collected_at: lastLog.collected_at || null,
        date_min: allDates[0] || null,
        date_max: allDates[allDates.length - 1] || null,
      },
      kpi: {
        today: latest, latest_partial, period_total, dod: delta,
        active_channels: disp.length, channels_in_view: allowed.size,
      },
      daily_trend: trend,
      cumulative_trend: cumulative,
      by_channel,
      channel_share: by_channel.filter((c) => c.period_total > 0)
        .map((c) => ({ name: c.name, total: c.period_total, pct: c.share_pct, color: c.color })),
      channel_master: disp.map((c, i) => Object.assign({}, c, { color: PALETTE[i % PALETTE.length] })),
      available_dates: allDates,
    };
  }

  window.ANALYTICS = { buildView, PALETTE, GRAN_LABEL };
})();
