const AUTO_REFRESH_MS = 60 * 1000;
const STALE_THRESHOLD_MS = 15 * 60 * 1000;
const FLEX_MODE_KEY = 'ashare_flex_mode_v1';
const FLEX_BOOK_KEY = 'ashare_flex_book_v1'; // real | sim
const FLEX_LEDGER_KEY_REAL = 'ashare_flex_exec_ledger_v1';
const FLEX_LEDGER_KEY_SIM = 'ashare_flex_exec_ledger_sim_v1';

function loadFlexModePreference() {
  // Product lock: Flex desk only uses aggressive sizing.
  return 'aggressive';
}

function saveFlexModePreference(_mode) {
  try {
    localStorage.setItem(FLEX_MODE_KEY, 'aggressive');
  } catch (_) { /* ignore */ }
}

function loadFlexBookPreference() {
  try {
    const b = localStorage.getItem(FLEX_BOOK_KEY);
    if (b === 'sim' || b === 'real') return b;
  } catch (_) { /* ignore */ }
  return 'real';
}

function saveFlexBookPreference(book) {
  try {
    localStorage.setItem(FLEX_BOOK_KEY, book === 'sim' ? 'sim' : 'real');
  } catch (_) { /* ignore */ }
}

function isFlexSimBook() {
  return dashboardState.flexBook === 'sim';
}

function flexLedgerStorageKey(book = dashboardState.flexBook) {
  return book === 'sim' ? FLEX_LEDGER_KEY_SIM : FLEX_LEDGER_KEY_REAL;
}

const CHART_RANGE_KEYS = {
  history: 'history',
  avix: 'avix',
  hs300: 'hs300',
};

const CHART_DOM_IDS = {
  history: 'historyChart',
  avix: 'avixQvixChart',
  hs300: 'hs300Chart',
};

const CHART_RANGE_CONTROL_IDS = {
  history: 'rangeControlsHistory',
  avix: 'rangeControlsAvix',
  hs300: 'rangeControlsHs300',
};

function defaultChartRanges() {
  return { history: '1Y', avix: '1Y', hs300: '1Y' };
}

const dashboardState = {
  /** Per-chart time ranges (independent). */
  chartRanges: defaultChartRanges(),
  /** @deprecated use chartRanges.history — kept for any leftover reads */
  activeRange: '1Y',
  componentChart: null,

  timeCharts: [],
  chartInstances: { history: null, avix: null, hs300: null },
  history: [],
  nowcastHistory: {},
  strategy: {},
  refreshInFlight: false,
  cacheBust: null,
  lastUpdateTime: null,
  lastTradeDate: null,
  heavyLoaded: false,
  flexMode: 'aggressive',
  flexBook: loadFlexBookPreference(), // real = 本机点买；sim = 策略严格跟随
  flexPlaybook: null,
  flexActive: null,
  flexLedgerBound: false,
  flexModal: null,
  flexSimSyncedAsOf: null,
  /** Independent app data plane (local pipeline). Never GitHub Pages. */
  dataPlane: {
    available: false,
    status: null,
    refreshInFlight: false,
    /** Pages → GitHub Actions dispatch plane */
    actions: {
      owner: 'AmineSeRaFimmm',
      repo: 'A-Share-Risk-Thermometer',
      ref: 'main',
      workflows: {
        realtime: 'update-realtime-avix.yml',
        full: 'update-data.yml',
      },
      storageKey: 'rt.github.actions.pat',
      lastDispatchAt: 0,
    },
  },
};

function getGithubActionsPat() {
  try {
    return (localStorage.getItem(dashboardState.dataPlane.actions.storageKey) || '').trim();
  } catch (_) {
    return '';
  }
}

function setGithubActionsPat(token) {
  const key = dashboardState.dataPlane.actions.storageKey;
  try {
    if (token) localStorage.setItem(key, token);
    else localStorage.removeItem(key);
  } catch (err) {
    console.warn('localStorage PAT write failed', err);
  }
}

function hasGithubActionsPat() {
  return Boolean(getGithubActionsPat());
}

/**
 * A-share action windows (Asia/Shanghai).
 * 实时: 交易日 开盘前30分钟 → 收盘（含午休），默认 08:45–15:15
 * 日更: 上述窗口之外（盘后 / 周末 / 非交易日）
 * 节假日无完整日历时按周一至周五近似；若 history 已加载则优先用交易日集合。
 */
const ASHARE_ACTION_WINDOW = {
  openHour: 9,
  openMin: 15,
  closeHour: 15,
  closeMin: 15,
  preOpenMinutes: 30,
  timeZone: 'Asia/Shanghai',
};

function getShanghaiDateParts(date = new Date()) {
  // Avoid hourCycle (throws on some iOS Safari). Prefer formatToParts + hour12:false.
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: ASHARE_ACTION_WINDOW.timeZone,
      weekday: 'short',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(date);
    const map = {};
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i];
      if (p.type !== 'literal') map[p.type] = p.value;
    }
    let hour = Number(map.hour);
    const minute = Number(map.minute);
    // Some engines emit 24:xx for midnight — normalize.
    if (hour === 24) hour = 0;
    const year = map.year || '1970';
    const month = map.month || '01';
    const day = map.day || '01';
    return {
      weekday: map.weekday || 'Mon',
      year,
      month,
      day,
      hour: Number.isFinite(hour) ? hour : 0,
      minute: Number.isFinite(minute) ? minute : 0,
      ymd: year + '-' + month + '-' + day,
      minutes:
        (Number.isFinite(hour) ? hour : 0) * 60 +
        (Number.isFinite(minute) ? minute : 0),
    };
  } catch (err) {
    // Last resort: approximate with UTC+8 (no DST in China).
    const shifted = new Date(date.getTime() + 8 * 60 * 60 * 1000);
    const wdNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const y = shifted.getUTCFullYear();
    const m = String(shifted.getUTCMonth() + 1).padStart(2, '0');
    const d = String(shifted.getUTCDate()).padStart(2, '0');
    const hour = shifted.getUTCHours();
    const minute = shifted.getUTCMinutes();
    return {
      weekday: wdNames[shifted.getUTCDay()],
      year: String(y),
      month: m,
      day: d,
      hour,
      minute,
      ymd: y + '-' + m + '-' + d,
      minutes: hour * 60 + minute,
    };
  }
}

function isAshareTradingDayCandidate(parts) {
  if (!parts || !parts.ymd) return false;
  // Prefer known sessions from loaded history / latest (only for "was a session").
  // For *today*, history often has not caught up yet — so weekday proxy is primary.
  try {
    const wd = parts.weekday || '';
    if (wd === 'Sat' || wd === 'Sun') return false;
    return true;
  } catch (_) {
    return true;
  }
}

function getAshareActionWindow(date = new Date()) {
  try {
    const p = getShanghaiDateParts(date);
    const tradingDay = isAshareTradingDayCandidate(p);
    const openMins =
      ASHARE_ACTION_WINDOW.openHour * 60 + ASHARE_ACTION_WINDOW.openMin;
    const closeMins =
      ASHARE_ACTION_WINDOW.closeHour * 60 + ASHARE_ACTION_WINDOW.closeMin;
    const startMins = openMins - ASHARE_ACTION_WINDOW.preOpenMinutes;
    const inSession =
      tradingDay && p.minutes >= startMins && p.minutes <= closeMins;

    const hh = String(Math.floor(startMins / 60)).padStart(2, '0');
    const mm = String(startMins % 60).padStart(2, '0');
    const ch = String(ASHARE_ACTION_WINDOW.closeHour).padStart(2, '0');
    const cm = String(ASHARE_ACTION_WINDOW.closeMin).padStart(2, '0');
    const windowLabel = hh + ':' + mm + '-' + ch + ':' + cm + ' 北京时间';

    if (inSession) {
      return {
        realtime: true,
        daily: false,
        tradingDay: true,
        inSession: true,
        parts: p,
        reason: '盘中窗口 ' + windowLabel,
        windowLabel,
      };
    }
    return {
      realtime: false,
      daily: true,
      tradingDay,
      inSession: false,
      parts: p,
      reason: tradingDay
        ? '非盘中（实时仅 ' + windowLabel + '）'
        : '非交易日（周末/休市）',
      windowLabel,
    };
  } catch (err) {
    console.warn('getAshareActionWindow failed', err);
    // Fail open for daily so the app never bricks outside market hours.
    return {
      realtime: false,
      daily: true,
      tradingDay: false,
      inSession: false,
      parts: null,
      reason: '时段判断失败，默认仅日更',
      windowLabel: '08:45-15:15 北京时间',
    };
  }
}

async function loadJSON(path, { bust = true } = {}) {
  let url = path;
  if (bust) {
    const token = dashboardState.cacheBust || String(Date.now());
    url = path + (path.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(token);
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to load ' + path);
  return await res.json();
}

async function resolveCacheBust() {
  try {
    const info = await loadJSON('./data/build_info.json', { bust: true });
    if (info?.build_time) {
      dashboardState.cacheBust = info.build_time;
      return;
    }
  } catch (_) {
    /* optional */
  }
  dashboardState.cacheBust = String(Date.now());
}

function getTempClass(temp) {
  if (temp < 20) return 'calm';
  if (temp < 40) return 'normal';
  if (temp < 60) return 'caution';
  if (temp < 75) return 'high-risk';
  if (temp < 90) return 'panic';
  return 'extreme';
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '--';
}

function appendMetaItem(label, id) {
  const grid = document.querySelector('#temperaturePanel .meta-grid');
  if (!grid || document.getElementById(id)) return;
  const box = document.createElement('div');
  const dt = document.createElement('dt');
  const dd = document.createElement('dd');
  dt.textContent = label;
  dd.id = id;
  dd.textContent = '--';
  box.appendChild(dt);
  box.appendChild(dd);
  grid.appendChild(box);
}

function ensureRealtimeMeta() {
  appendMetaItem('温度口径', 'temperatureMode');
  appendMetaItem('模型置信度', 'modelConfidence');
  appendMetaItem('实时AVIX', 'realtimeAvix');
  appendMetaItem('实时质量', 'realtimeAvixQuality');
  appendMetaItem('数据新鲜度', 'freshnessStatus');
  appendMetaItem('数据检查', 'refreshStatus');
}

function formatRealtimeAvix(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : '--';
}

function renderRealtimeAvix(avix) {
  ensureRealtimeMeta();
  const realtimeMid = avix?.avix_realtime_mid;
  const realtimeQuality = avix?.avix_realtime_quality;
  const usableNowcast = avix?.avix_realtime_usable_nowcast ?? avix?.avix_realtime_usable;
  const usableGap = avix?.avix_realtime_usable_gap_fill;
  setText('realtimeAvix', formatRealtimeAvix(realtimeMid));
  let qualityLabel = realtimeQuality || '--';
  if (realtimeQuality) {
    if (usableNowcast) {
      qualityLabel += usableGap ? ' · 盘中可用/可补缺口' : ' · 盘中可用';
    } else {
      qualityLabel += ' · 不可用';
    }
  }
  setText('realtimeAvixQuality', qualityLabel);
  const qualityEl = document.getElementById('realtimeAvixQuality');
  if (qualityEl) {
    qualityEl.title = [
      avix?.avix_realtime_note,
      avix?.avix_realtime_source,
      '盘中 nowcast 允许 WARN；估算收盘补缺要求严格 OK；正式历史只用官方收盘 AVIX',
    ].filter(Boolean).join(' | ');
  }
}

function updateFreshness(latest) {
  ensureRealtimeMeta();
  const el = document.getElementById('freshnessStatus');
  if (!el) return;
  if (!latest?.update_time) {
    el.textContent = '--';
    el.dataset.freshness = 'unknown';
    el.title = 'latest.update_time missing';
    return;
  }
  const updatedAt = new Date(latest.update_time);
  if (Number.isNaN(updatedAt.getTime())) {
    el.textContent = '时间异常';
    el.dataset.freshness = 'unknown';
    el.title = latest.update_time;
    return;
  }
  const ageMs = Date.now() - updatedAt.getTime();
  const ageMinutes = Math.max(0, Math.floor(ageMs / 60000));
  const isFresh = ageMs <= STALE_THRESHOLD_MS;
  el.textContent = isFresh ? `新鲜 ${ageMinutes}分` : `延迟 ${ageMinutes}分`;
  el.dataset.freshness = isFresh ? 'fresh' : 'stale';
  el.title = `最近数据生成时间: ${updatedAt.toLocaleString('zh-CN', { hour12: false })}`;
}

function renderNowcastNote(latest) {
  const note = document.getElementById('nowcastNote');
  if (!note) return;
  const official = latest?.official_close || {};
  const nowcast = latest?.nowcast || {};
  const mode = latest?.temperature_mode || '';
  // Magazine: one short line only when not official close.
  if (mode === 'NOWCAST' || (nowcast.active && latest?.is_final === false && mode !== 'ESTIMATED_CLOSE')) {
    note.hidden = false;
    note.textContent = `盘中 · 收盘 RT ${official.risk_temperature ?? '—'}`;
    return;
  }
  if (mode === 'ESTIMATED_CLOSE') {
    note.hidden = false;
    note.textContent = '估算收盘';
    return;
  }
  note.hidden = true;
  note.textContent = '';
}

function renderBreadthMode(latest) {
  ensureRealtimeMeta();
  appendMetaItem('宽度口径', 'breadthMode');
  const market = latest?.market || {};
  const modeCn = market.breadth_mode_cn || '--';
  const mode = market.breadth_mode || '';
  const score = Number(market.breadth_pressure);
  const scoreLabel = Number.isFinite(score) ? ` · ${score.toFixed(1)}` : '';
  setText('breadthMode', `${modeCn}${scoreLabel}`);
  const el = document.getElementById('breadthMode');
  if (!el) return;
  el.dataset.breadth = (mode || 'unknown').toLowerCase();
  const asOf = market.as_of_trade_date ? ` / 日期: ${market.as_of_trade_date}` : '';
  el.title = market.breadth_quality
    ? `宽度质量: ${market.breadth_quality}${asOf}`
    : mode === 'INDEX_PROXY'
      ? '历史多数日期使用宽基指数代理宽度，不是全A个股涨跌统计'
      : `基于全A现货快照统计${asOf}`;
}

function updateRefreshStatus(status, detail) {
  ensureRealtimeMeta();
  const el = document.getElementById('refreshStatus');
  if (!el) return;
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  el.textContent = status === 'error' ? `失败 ${now}` : `已检查 ${now}`;
  el.dataset.freshness = status === 'error' ? 'stale' : 'fresh';
  el.title = detail || '页面每 60 秒自动检查最新数据';
}

function renderLatest(latest) {
  ensureRealtimeMeta();
  setText('riskTemperature', latest.risk_temperature);
  setText('regime', latest.regime_cn);
  const modeLabel = latest.temperature_mode_cn || (latest.is_final === false ? '盘中估算' : '收盘正式');
  const qualityEl = document.getElementById('quality');
  qualityEl.textContent = modeLabel;
  qualityEl.title = latest.quality || modeLabel;
  setText('temperatureMode', modeLabel);
  setText('modelConfidence', latest.model_confidence_label || '--');
  const confidenceEl = document.getElementById('modelConfidence');
  if (confidenceEl) {
    const confidence = latest.model_confidence || {};
    confidenceEl.title = confidence.missing_components ? `缺失或降级: ${confidence.missing_components}` : '主要模型输入完整';
    confidenceEl.dataset.grade = (confidence.grade || '').toLowerCase();
  }
  // Always surface official close RT next to active reading
  appendMetaItem('正式收盘RT', 'officialCloseRt');
  const official = latest.official_close || {};
  const officialLabel = official.trade_date
    ? `${official.risk_temperature ?? '--'} (${official.trade_date})`
    : '--';
  setText('officialCloseRt', officialLabel);
  const officialEl = document.getElementById('officialCloseRt');
  if (officialEl) {
    officialEl.title = '正式历史只用官方收盘 AVIX；与盘中估算分离';
  }
  renderBreadthMode(latest);
  setText('tradeDate', latest.trade_date);
  const update = latest.update_time ? new Date(latest.update_time).toLocaleString('zh-CN', { hour12: false }) : '--';
  setText('updateTime', update);
  const mastheadDate = document.getElementById('mastheadDate');
  if (mastheadDate) {
    const mode = latest.temperature_mode_cn || latest.temperature_mode || '';
    mastheadDate.textContent = latest.trade_date
      ? `${latest.trade_date}${mode ? ' · ' + mode : ''}`
      : '—';
  }
  renderRealtimeAvix(latest.avix || {});
  updateFreshness(latest);
  renderNowcastNote(latest);
  // Cover: headline + posture only — no long essay on the page.
  setText('headline', latest.interpretation?.headline || '—');
  const summaryEl = document.getElementById('summary');
  if (summaryEl) {
    summaryEl.textContent = '';
    summaryEl.hidden = true;
  }
  setText('posture', latest.interpretation?.posture);
  document.getElementById('temperaturePanel').dataset.zone = getTempClass(Number(latest.risk_temperature));
  paintStaticPagesPlaneMeta(latest);
}

function renderAudit(audit) {
  // Temperature UI no longer shows health panel; keep function for API compatibility.
  const grid = document.getElementById('healthGrid');
  if (!grid) return;
  setText('lastSuccessfulUpdate', audit.last_successful_update);
  const labels = {
    options_history: '期权数据',
    options_realtime: '实时期权',
    qvix: 'QVIX',
    indices: '指数行情',
    breadth: '市场宽度',
    shibor: 'Shibor'
  };
  grid.innerHTML = Object.entries(audit.data_health || {}).map(([key, value]) => (
    `<div class="health-item"><span>${labels[key] || key}</span><strong>${value}</strong></div>`
  )).join('');
  const confidence = audit.model_confidence || {};
  if (confidence.score !== undefined && confidence.score !== null) {
    const gradeLabel = { HIGH: '高', MEDIUM: '中', LOW: '低' }[confidence.grade] || confidence.grade || '--';
    grid.insertAdjacentHTML('beforeend',
      `<div class="health-item"><span>模型置信度</span><strong>${Number(confidence.score).toFixed(1)} / ${gradeLabel}</strong></div>`
    );
  }
}

function formatPct(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? (numeric * 100).toFixed(2) + '%' : '--';
}

function renderStrategy(strategy) {
  // Temperature UI no longer shows S3/S4 panel; strategy data still used for charts.
  const signalBox = document.getElementById('strategySignals');
  if (!signalBox) return;
  const payload = strategy || {};
  const latest = payload.latest || {};
  const position = payload.position || {};
  const rules = payload.rules || {};
  setText('strategyMode', rules.mode || payload.status || '--');
  setText('strategyPosition', position.s3_s4 || '--');
  setText('strategyTradeDate', latest.trade_date || '--');
  const execution = latest.execution_trade_date
    ? `${latest.execution_trade_date} / ${latest.execution_sse_open ?? '--'}`
    : '--';
  setText('strategyExecution', execution);
  const items = [
    ['S3', latest.s3_signal, latest.s3_buy, latest.s3_sell, latest.s3_sell_reason],
    ['S4', latest.s4_signal, latest.s4_buy, latest.s4_sell, latest.s4_sell_reason],
    ['S3+S4', latest.s3_s4_signal, latest.s3_s4_buy, latest.s3_s4_sell, latest.s3_s4_sell_reason]
  ];
  signalBox.innerHTML = items.map(([name, signal, buy, sell, reason]) => {
    const action = buy ? 'BUY' : sell ? 'SELL' : signal ? 'WATCH' : 'NONE';
    const detail = sell && reason ? reason : `AVIX ${latest.avix ?? '--'} / 10日 ${formatPct(latest.sse_ret10)}`;
    return `<div class="strategy-card" data-action="${action.toLowerCase()}"><span>${name}</span><strong>${action}</strong><em>${detail}</em></div>`;
  }).join('');
}

function formatSignedPct(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  const pct = numeric * 100;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function mergeNowcastHistory(history, nowcastHistory, latest) {
  const byDate = new Map((history || []).map(row => [row.date, {
    ...row,
    risk_temperature_official: row.risk_temperature,
    risk_temperature_estimated: null,
    estimate_reason: null,
  }]));
  (nowcastHistory?.rows || []).forEach(row => {
    const date = row.date || row.trade_date;
    if (!date) return;
    const existing = byDate.get(date) || {
      date,
      risk_temperature: null,
      risk_temperature_official: null,
      regime: row.regime,
      avix_clean: null,
      qvix: null,
      qvix_replica: null,
      hs300_close: row.hs300_close ?? null,
      drawdown_pressure: row.drawdown_pressure ?? null,
      breadth_pressure: row.breadth_pressure ?? null,
    };
    existing.risk_temperature_estimated = row.risk_temperature_estimated;
    existing.estimate_reason = row.gap_reason || row.quality || '估算收盘';
    existing.estimate_quality = row.quality;
    existing.avix_realtime_mid = row.avix_realtime_mid;
    existing.is_estimated_close = true;
    byDate.set(date, existing);
  });
  if (latest?.trade_date && latest.is_final === false && !byDate.get(latest.trade_date)?.risk_temperature_estimated) {
    const existing = byDate.get(latest.trade_date) || { date: latest.trade_date, risk_temperature: null, risk_temperature_official: null };
    existing.risk_temperature_estimated = latest.risk_temperature;
    existing.estimate_reason = '页面当前盘中估算；正式收盘温度尚未生成';
    existing.is_estimated_close = true;
    byDate.set(latest.trade_date, existing);
  }
  return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

function renderNowcastGapSummary(nowcastHistory) {
  const note = document.getElementById('nowcastGapNote');
  if (!note) return;
  // Magazine: hide gap essay; keep element for API compatibility.
  note.hidden = true;
  note.textContent = '';
}

function filterHistoryByRange(history, range) {
  if (!history?.length || range === 'ALL') return history || [];
  const months = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36 }[range] || 12;
  const last = new Date(history[history.length - 1].date + 'T00:00:00');
  const cutoff = new Date(last);
  cutoff.setMonth(cutoff.getMonth() - months);
  return history.filter(row => new Date(row.date + 'T00:00:00') >= cutoff);
}

function setChartRangeActive(chartKey, range) {
  const controlId = CHART_RANGE_CONTROL_IDS[chartKey];
  const root = controlId ? document.getElementById(controlId) : null;
  if (!root) return;
  root.querySelectorAll('button[data-range]').forEach(button => {
    button.classList.toggle('active', button.dataset.range === range);
  });
}

function disposeChartDom(domId) {
  const el = document.getElementById(domId);
  if (!el) return;
  const instance = echarts.getInstanceByDom(el);
  if (instance) instance.dispose();
}

/** Render one history-page chart with its own time range. */
function renderOneTimeChart(chartKey, history, strategy, range) {
  const key = CHART_RANGE_KEYS[chartKey] || chartKey;
  const domId = CHART_DOM_IDS[key];
  if (!domId) return null;
  const filtered = filterHistoryByRange(history, range || dashboardState.chartRanges[key] || '1Y');
  disposeChartDom(domId);
  let chart = null;
  if (key === 'history') chart = renderHistoryChart(filtered, strategy);
  else if (key === 'avix') chart = renderAvixQvixChart(filtered, strategy);
  else if (key === 'hs300') chart = renderHs300Chart(filtered);
  dashboardState.chartInstances[key] = chart;
  return chart;
}

/** Render all three charts, each with its independent range. No echarts.connect. */
function renderTimeCharts(history, strategy, ranges) {
  const r = ranges || dashboardState.chartRanges || defaultChartRanges();
  const charts = [
    renderOneTimeChart('history', history, strategy, r.history),
    renderOneTimeChart('avix', history, strategy, r.avix),
    renderOneTimeChart('hs300', history, strategy, r.hs300),
  ].filter(Boolean);
  dashboardState.timeCharts = charts;
  return charts;
}

function bindRangeControls() {
  Object.entries(CHART_RANGE_CONTROL_IDS).forEach(([chartKey, controlId]) => {
    const root = document.getElementById(controlId);
    if (!root || root.dataset.boundRange === '1') return;
    root.dataset.boundRange = '1';
    root.querySelectorAll('button[data-range]').forEach(button => {
      button.addEventListener('click', () => {
        const range = button.dataset.range || '1Y';
        dashboardState.chartRanges[chartKey] = range;
        if (chartKey === 'history') dashboardState.activeRange = range;
        setChartRangeActive(chartKey, range);
        renderOneTimeChart(chartKey, dashboardState.history, dashboardState.strategy, range);
        dashboardState.timeCharts = Object.values(dashboardState.chartInstances).filter(Boolean);
        requestAnimationFrame(() => resizeVisibleCharts());
      });
    });
    // Sync button UI to current state
    setChartRangeActive(chartKey, dashboardState.chartRanges[chartKey] || '1Y');
  });
}

const FLEX_GUIDE_KEY = 'ashare_flex_guide_dismissed_v1';

function flexToast(message, kind = 'ok', ms = 2200) {
  const el = document.getElementById('flexToast');
  if (!el || !message) return;
  el.hidden = false;
  el.className = `flex-toast ${kind || 'ok'}`;
  el.textContent = message;
  clearTimeout(flexToast._timer);
  flexToast._timer = setTimeout(() => {
    el.hidden = true;
  }, Math.max(1200, Number(ms) || 2200));
}

function paintFlexBookChrome() {
  const sim = isFlexSimBook();
  const panel = document.getElementById('flexTradePanel');
  panel?.classList.toggle('is-sim-book', sim);
  panel?.classList.toggle('is-real-book', !sim);
  const realBtn = document.getElementById('flexBookRealBtn');
  const simBtn = document.getElementById('flexBookSimBtn');
  realBtn?.classList.toggle('active', !sim);
  simBtn?.classList.toggle('active', sim);
  realBtn?.setAttribute('aria-pressed', sim ? 'false' : 'true');
  simBtn?.setAttribute('aria-pressed', sim ? 'true' : 'false');
  const sub = document.getElementById('flexTitleSub');
  if (sub) {
    sub.textContent = sim ? '模拟' : '真实';
  }
  const pill = document.getElementById('flexBookPill');
  if (pill) {
    pill.textContent = sim ? '模拟仓' : '真实仓';
    pill.title = sim ? '当前：模拟账本' : '当前：真实账本';
    pill.classList.toggle('book-sim', sim);
    pill.classList.toggle('book-real', !sim);
  }
}

function bindFlexGuide() {
  const guide = document.getElementById('flexGuide');
  if (guide) guide.hidden = true; // magazine: no onboarding essay
}

function bindFlexModeControls() {
  // Flex sizing locked to aggressive only — no 进取/保守 UI.
  dashboardState.flexMode = 'aggressive';
  saveFlexModePreference('aggressive');
  bindFlexBookToggle();
  bindFlexGuide();
  paintFlexBookChrome();
}

function bindFlexBookToggle() {
  const setBook = (book) => {
    const next = book === 'sim' ? 'sim' : 'real';
    if (dashboardState.flexBook === next) return;
    dashboardState.flexBook = next;
    saveFlexBookPreference(next);
    // Do NOT clear flexSimSyncedAsOf — switching books must not rewrite sim journal.
    paintFlexBookChrome();
    flexToast(next === 'sim' ? '已切换到模拟仓' : '已切换到真实仓', 'ok', 1400);
    if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
    else renderFlexExecUi();
  };
  const realBtn = document.getElementById('flexBookRealBtn');
  const simBtn = document.getElementById('flexBookSimBtn');
  if (realBtn && realBtn.dataset.bound !== '1') {
    realBtn.dataset.bound = '1';
    realBtn.addEventListener('click', () => setBook('real'));
  }
  if (simBtn && simBtn.dataset.bound !== '1') {
    simBtn.dataset.bound = '1';
    simBtn.addEventListener('click', () => setBook('sim'));
  }
  paintFlexBookChrome();
}

function flexSwitchTab(tabId) {
  const panel = document.getElementById('flexTradePanel');
  if (!panel) return;
  panel.querySelectorAll('.flex-tab').forEach(t => {
    const on = t.dataset.flexTab === tabId;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  panel.querySelectorAll('.flex-tab-panel').forEach(p => {
    p.classList.toggle('active', p.dataset.flexPanel === tabId);
  });
}

async function loadCriticalDashboardData() {
  if (!dashboardState.cacheBust) {
    await resolveCacheBust();
  }
  const [latest, history, nowcastHistory, components, audit] = await Promise.all([
    loadJSON('./data/latest.json'),
    loadJSON('./data/history.json'),
    loadJSON('./data/nowcast_history.json').catch(() => ({ status: 'missing', rows: [], gaps: [] })),
    loadJSON('./data/components.json'),
    loadJSON('./data/audit.json'),
  ]);
  return { latest, history, nowcastHistory, components, audit };
}

async function loadHeavyDashboardData() {
  const [strategy, rtTactical, stagePlaybook, etfMarks] = await Promise.all([
    loadJSON('./data/strategy.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/rt_tactical.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/stage_playbook.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/etf_daily_marks.json').catch(() => ({ status: 'missing', by_code: {} })),
  ]);
  dashboardState.heavyLoaded = true;
  dashboardState.etfMarks = etfMarks && etfMarks.status !== 'missing'
    ? etfMarks
    : { status: 'missing', by_code: {}, policy: 'SIM_ENTRY_OPEN_MARK_CLOSE' };
  return { strategy, rtTactical, stagePlaybook, etfMarks: dashboardState.etfMarks };
}

function renderCriticalDashboard({ latest, history, nowcastHistory, components, audit }) {
  document.body.classList.remove('error');
  hideLoadError();
  dashboardState.latest = latest || null;
  renderLatest(latest);
  renderAudit(audit);
  renderNowcastGapSummary(nowcastHistory);
  setText('componentsMode', `${components.temperature_mode || '--'} / ${components.trade_date || '--'}`);
  const componentDom = document.getElementById('componentsChart');
  if (componentDom) {
    const oldComponentChart = echarts.getInstanceByDom(componentDom);
    if (oldComponentChart) oldComponentChart.dispose();
    dashboardState.componentChart = renderComponentsChart(components);
  }
  const activeHistory = mergeNowcastHistory(history, nowcastHistory, latest);
  dashboardState.history = activeHistory;
  dashboardState.nowcastHistory = nowcastHistory;
  dashboardState.lastUpdateTime = latest?.update_time || null;
  dashboardState.lastTradeDate = latest?.trade_date || null;
  dashboardState.timeCharts = renderTimeCharts(
    activeHistory,
    dashboardState.strategy || {},
    dashboardState.chartRanges
  );
  // History may refine weekday/holiday estimate for action buttons.
  applyDataPlaneButtonSchedule({
    baseEnabled: !dashboardState.dataPlane.refreshInFlight,
  });
}

function renderHeavyDashboard({ strategy, rtTactical, stagePlaybook }) {
  dashboardState.strategy = strategy || {};
  renderFlexTradePanel(stagePlaybook);
  renderStrategy(strategy);
  renderRtTactical(rtTactical);
  if (dashboardState.history?.length) {
    dashboardState.timeCharts = renderTimeCharts(
      dashboardState.history,
      dashboardState.strategy,
      dashboardState.chartRanges
    );
  }
}

function pctLabel(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : '--';
}

const FLEX_ACTION_BADGE = {
  OPEN: { text: '明天买入', cls: 'buy' },
  OVERWEIGHT: { text: '明天买入', cls: 'buy' },
  BUY: { text: '明天买入', cls: 'buy' },
  HOLD: { text: '—', cls: 'wait' }, // desk never promotes paper HOLD as a buy
  CLOSE: { text: '平仓', cls: 'sell' },
  STRATEGY_CLOSE: { text: '策略平仓', cls: 'sell' },
  AVOID: { text: '回避', cls: 'avoid' },
  FLAT: { text: '观望', cls: 'wait' },
  SELL: { text: '卖出', cls: 'sell' },
  OVERWEIGHT_RELATIVE: { text: '明天买入', cls: 'buy' },
  UNDERWEIGHT_RELATIVE: { text: '低配', cls: 'avoid' },
};

/** @deprecated use flexLedgerStorageKey() — kept only for migration notes */
const FLEX_LEDGER_KEY = FLEX_LEDGER_KEY_REAL;
/** Cache of strategy OPEN rows so T+1 can still confirm after daily rebuild clears buy_list. v2 invalidates bad seeds. */
const FLEX_OPEN_SIGNAL_CACHE_KEY = 'ashare_flex_open_signal_cache_v2';
const FLEX_BUY_ACTIONS = new Set(['OPEN', 'OVERWEIGHT', 'BUY', 'OVERWEIGHT_RELATIVE']);
const FLEX_CLOSE_ACTIONS = new Set(['CLOSE', 'SELL']);
/** Open signal lives on real signal day T and next trading day T+1; gone from T+2 trade sessions. */
const FLEX_OPEN_SIGNAL_MAX_LAG_DAYS = 1;
const FLEX_SAT_MIN_HOLD_DAYS = 3;
const FLEX_SAT_STOP_LOSS_DEFAULT = -0.03;
const FLEX_SAT_TAKE_PROFIT_DEFAULT = 0.04;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatMoney(value, digits = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 4 });
}

function formatShares(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '--';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function flexUid(prefix = 'fx') {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function defaultFlexLedger(book = dashboardState.flexBook) {
  return {
    version: 2,
    book: book === 'sim' ? 'sim' : 'real',
    capital: 0,
    cash: 0,
    positions: {},
    journal: [],
    updated_at: null,
    strategy_as_of: null,
  };
}

function flexOpenPositions(ledger) {
  // Sim missing-price sleeves may keep cost_basis with qty=0; still show the row.
  return Object.values(ledger?.positions || {}).filter(p => {
    if (Number(p.qty) > 1e-9) return true;
    return Number(p.cost_basis) > 0 && String(p.mark_quality || '') === 'MISSING';
  });
}

function flexDeployedCost(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => sum + (Number(p.cost_basis) || 0), 0);
}

function flexMarkValue(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => {
    // Never invent P&L from unit-price fallback when EOD marks are missing.
    if (String(p.mark_quality || '') === 'MISSING') {
      return sum + (Number(p.cost_basis) || 0);
    }
    const mark = Number(p.last_price);
    const px = Number.isFinite(mark) && mark > 0 ? mark : Number(p.avg_price) || 0;
    return sum + Number(p.qty) * px;
  }, 0);
}

/** Migrate v1 ledgers that derived cash as capital−cost (dropped realized PnL). */
function normalizeFlexLedger(raw, book = dashboardState.flexBook) {
  const ledger = {
    version: 2,
    book: raw?.book === 'sim' || book === 'sim' ? 'sim' : 'real',
    capital: Number(raw?.capital) || 0,
    cash: raw?.cash,
    positions: raw?.positions && typeof raw.positions === 'object' ? { ...raw.positions } : {},
    journal: Array.isArray(raw?.journal) ? raw.journal : [],
    updated_at: raw?.updated_at || null,
    strategy_as_of: raw?.strategy_as_of || null,
  };
  if (ledger.cash == null || !Number.isFinite(Number(ledger.cash))) {
    // Best-effort migration for pre-v2 books.
    ledger.cash = Math.max(0, ledger.capital - flexDeployedCost(ledger));
  } else {
    ledger.cash = Number(ledger.cash);
  }
  // Recompute every open position's exit_date on trading-day axis (migrate old calendar exits).
  for (const key of Object.keys(ledger.positions || {})) {
    const p = ledger.positions[key];
    if (!p || !(Number(p.qty) > 1e-9)) continue;
    if (p.buy_date && p.hold_days != null && Number.isFinite(Number(p.hold_days))) {
      p.exit_date = flexAddTradingDays(p.buy_date, Number(p.hold_days));
    }
  }
  return ledger;
}

function loadFlexLedgerForBook(book) {
  try {
    const raw = localStorage.getItem(flexLedgerStorageKey(book));
    if (!raw) return defaultFlexLedger(book);
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return defaultFlexLedger(book);
    return normalizeFlexLedger(parsed, book);
  } catch (_) {
    return defaultFlexLedger(book);
  }
}

function loadFlexLedger() {
  return loadFlexLedgerForBook(dashboardState.flexBook || 'real');
}

function saveFlexLedger(ledger) {
  const book = ledger?.book === 'sim' || isFlexSimBook() ? 'sim' : 'real';
  const normalized = normalizeFlexLedger(ledger, book);
  normalized.book = book;
  normalized.updated_at = new Date().toISOString();
  localStorage.setItem(flexLedgerStorageKey(book), JSON.stringify(normalized));
  return normalized;
}

/** Map sector/name → etf fields from flex panel lists. */
function flexLookupInstrument(flex, name) {
  const n = String(name || '').trim();
  const pools = [
    ...(flex?.hold_list || []),
    ...(flex?.buy_list || []),
    ...(flex?.close_list || []),
    ...(flex?.satellite?.buy || []),
    ...(flex?.avoid_list || []),
  ];
  for (const row of pools) {
    if (String(row?.name || row?.sector || '').trim() === n) return row;
  }
  if (n === '沪深300' || n.includes('沪深300')) {
    return {
      name: '沪深300',
      etf_code: flex?.core?.etf_code || '510300',
      etf_name: flex?.core?.etf_name || '沪深300ETF华泰柏瑞',
      sleeve: 'core',
    };
  }
  return { name: n, etf_code: '', etf_name: '', sleeve: 'satellite' };
}

/**
 * AVOID cut-to-zero matcher.
 * IMPORTANT: match by sector/name first. Never cut a different sector that merely
 * shares a weak-proxy ETF code (e.g. 美容护理→159928 vs 商贸零售→159928).
 */
function flexIsAvoidCutItem(item) {
  const action = String(item?.action || item?.side || '').toUpperCase();
  if (action !== 'AVOID' && action !== 'UNDERWEIGHT_RELATIVE' && action !== 'FLAT') return false;
  const wt = item?.weight_target;
  if (wt != null && Number(wt) > 1e-9) return false;
  return true;
}

function flexTargetIsAvoided(t, flex) {
  const name = String(t?.name || t?.sector || '').trim();
  if (!name) return false;
  for (const item of flex?.avoid_list || []) {
    if (!flexIsAvoidCutItem(item)) continue;
    const aName = String(item.name || item.sector || '').trim();
    if (aName && aName === name) return true;
  }
  return false;
}

/** @deprecated kept for call sites that still pass a set — redirects to name-based check */
function flexAvoidZeroSet(flex) {
  return flex;
}

/**
 * Strategy paper targets while sleeves are open (strict sim book).
 * Uses live allocation weights; if sleeve open but alloc weight 0 (e.g. close day),
 * fall back to mode sizing so sim keeps the open sleeve until state goes flat.
 * AVOID names with target 0 are excluded (sim auto-cuts; real book tips only).
 */
function collectStrategyPaperTargets(flex) {
  const f = flex || {};
  const pos = f.position_state || {};
  const alloc = f.allocation || {};
  const mode = f.mode || 'aggressive';
  const cfg = (f.modes || {})[mode] || (f.modes || {}).aggressive || {};
  const targets = [];

  const core = pos.core || {};
  if (String(core.status || '') === 'open') {
    let w = Number(alloc.w_core);
    if (!(w > 0)) w = Number(cfg.core_when_signal != null ? cfg.core_when_signal : 0.6);
    const meta = flexLookupInstrument(f, '沪深300');
    const row = {
      sleeve: 'core',
      name: '沪深300',
      etf_code: core.etf_code || meta.etf_code || '510300',
      etf_name: meta.etf_name || f.core?.etf_name || '',
      weight: w,
      buy_date: core.entry_date || '',
      signal_as_of: core.entry_signal_date || '',
      hold_days: Number(f.hold_days) || 5,
      exit_date: core.exit_due_date || '',
    };
    if (!flexTargetIsAvoided(row, f)) targets.push(row);
  }

  const sat = pos.satellite || {};
  if (String(sat.status || '') === 'open') {
    let wSleeve = Number(alloc.w_sat);
    if (!(wSleeve > 0)) wSleeve = Number(cfg.sat_when_signal != null ? cfg.sat_when_signal : 0.4);
    const names = Array.isArray(sat.names) ? sat.names : [];
    const weights = sat.weights || {};
    // Drop avoided names before weight renorm so remaining names keep sleeve budget
    const kept = names.filter(name => !flexTargetIsAvoided({ name }, f));
    const wSum = kept.reduce((s, n) => s + (Number(weights[n]) || 0), 0) || kept.length || 1;
    for (const name of kept) {
      const win = (Number(weights[name]) || (1 / (kept.length || 1))) / wSum;
      const meta = flexLookupInstrument(f, name);
      targets.push({
        sleeve: 'satellite',
        name,
        etf_code: meta.etf_code || '',
        etf_name: meta.etf_name || '',
        weight: wSleeve * win,
        buy_date: sat.entry_date || '',
        signal_as_of: sat.entry_signal_date || '',
        hold_days: Number(f.hold_days_sat) || Number(f.satellite?.hold_days) || 8,
        exit_date: sat.exit_due_date || (f.exit_plan?.satellite?.paths?.max_signal_date) || '',
        stop_loss: flexSatelliteRiskRule(f).stopLoss,
        take_profit: flexSatelliteRiskRule(f).takeProfit,
      });
    }
  }

  // Renorm if total weight > 1 (aggressive dual sleeve)
  const tw = targets.reduce((s, t) => s + (Number(t.weight) || 0), 0);
  if (tw > 1.0001) {
    for (const t of targets) t.weight = (Number(t.weight) || 0) / tw;
  }
  return targets;
}

/** Look up EOD bar for ETF on trade date (exact, else on/before for mark, on/after for entry). */
function flexEtfBarLookup(code, dateStr, { prefer = 'exact' } = {}) {
  const c = String(code || '').replace(/\D/g, '').padStart(6, '0');
  const day = String(dateStr || '').slice(0, 10);
  const bars = dashboardState.etfMarks?.by_code?.[c]?.bars
    || dashboardState.etfMarks?.by_code?.[code]?.bars
    || null;
  if (!bars || !day) return null;
  if (bars[day]) return { ...bars[day], trade_date: day, match: 'exact' };
  const keys = Object.keys(bars).sort();
  if (!keys.length) return null;
  if (prefer === 'on_or_before') {
    for (let i = keys.length - 1; i >= 0; i -= 1) {
      if (keys[i] <= day) return { ...bars[keys[i]], trade_date: keys[i], match: 'on_or_before' };
    }
    return { ...bars[keys[0]], trade_date: keys[0], match: 'first' };
  }
  // on_or_after (entry)
  for (let i = 0; i < keys.length; i += 1) {
    if (keys[i] >= day) return { ...bars[keys[i]], trade_date: keys[i], match: 'on_or_after' };
  }
  return { ...bars[keys[keys.length - 1]], trade_date: keys[keys.length - 1], match: 'last' };
}

/**
 * Latest ETF EOD session we can actually mark to (max bar date in etf_daily_marks).
 * This is "上一交易日收盘" when market is closed / before today' s EOD lands.
 */
function flexEtfMarksCoverage() {
  const marks = dashboardState.etfMarks || {};
  let lastBar = null;
  const by = marks.by_code || {};
  Object.keys(by).forEach(code => {
    const last = by[code] && by[code].last ? String(by[code].last).slice(0, 10) : null;
    if (last && (!lastBar || last > lastBar)) lastBar = last;
  });
  const fileAsOf = marks.as_of ? String(marks.as_of).slice(0, 10) : null;
  // Truth is bars, not the file as_of claim (as_of can run ahead of incomplete bars).
  const session = lastBar || fileAsOf || null;
  return {
    file_as_of: fileAsOf,
    last_bar: lastBar,
    session,
    policy: marks.policy || null,
  };
}

/**
 * Effective mark date for holdings P&L.
 * Always prefer last available EOD close — never invent "today" nowcast without bars.
 * Strategy as_of only controls which paper positions exist. It must never delay
 * valuation or a local stop/target check once a newer EOD ETF bar is available.
 */
function flexEffectiveMarkDate() {
  const cov = flexEtfMarksCoverage();
  if (cov.session) return cov.session;
  try {
    const td = dashboardState.latest?.trade_date || dashboardState.lastTradeDate;
    if (td) return String(td).slice(0, 10);
  } catch (_) { /* ignore */ }
  return flexDateCn(0);
}

/**
 * Professional sim prices:
 *   entry = open on entry_date (T+1 open)
 *   mark  = close on markDate (last available EOD when closed)
 */
function flexSimEodPrices(etfCode, entryDate, markDate) {
  const entryBar = flexEtfBarLookup(etfCode, entryDate, { prefer: 'on_or_after' });
  const markBar = flexEtfBarLookup(etfCode, markDate, { prefer: 'on_or_before' });
  const entryOpen = entryBar && Number(entryBar.open) > 0 ? Number(entryBar.open) : null;
  const markClose = markBar && Number(markBar.close) > 0 ? Number(markBar.close) : null;
  let quality = 'OK';
  if (!entryOpen || !markClose) quality = 'MISSING';
  else if (entryBar.match !== 'exact' || markBar.match !== 'exact') quality = 'SNAP';
  return {
    entry_open: entryOpen,
    mark_close: markClose,
    entry_bar_date: entryBar?.trade_date || null,
    mark_bar_date: markBar?.trade_date || null,
    quality,
  };
}

/**
 * Remount open positions to last EOD close for display / totals.
 * Does not rewrite cost/avg (real fills stay). Safe for both real + sim books.
 */
function flexApplyEodMarksToLedger(ledger) {
  const L = normalizeFlexLedger(JSON.parse(JSON.stringify(ledger || {})));
  const markDate = flexEffectiveMarkDate();
  let marked = 0;
  let missing = 0;
  Object.keys(L.positions || {}).forEach(key => {
    const pos = L.positions[key];
    if (!pos || !(Number(pos.qty) > 0)) return;
    const code = pos.etf_code || '';
    if (!code) {
      if (!(Number(pos.last_price) > 0)) {
        pos.mark_quality = 'MISSING';
        missing += 1;
      }
      return;
    }
    const bar = flexEtfBarLookup(code, markDate, { prefer: 'on_or_before' });
    if (bar && Number(bar.close) > 0) {
      pos.last_price = Number(bar.close);
      pos.mark_bar_date = bar.trade_date;
      pos.mark_price_type = 'close';
      pos.mark_quality = bar.match === 'exact' ? 'OK' : 'SNAP';
      marked += 1;
    } else {
      pos.mark_quality = 'MISSING';
      missing += 1;
    }
  });
  L.mark_as_of = markDate;
  L.mark_policy = 'EOD_LAST_SESSION_CLOSE';
  L.mark_policy_cn = '盯市=最近可得交易日收盘（休市=上一交易日截止）';
  L._eod_mark_stats = { marked, missing, mark_date: markDate };
  return L;
}

/** Stable sim key: prefer sleeve+name so code lookup jitter never rewrites journal. */
function flexSimPositionKey(item) {
  const sleeve = String(item?.sleeve || 'na').trim() || 'na';
  const name = String(item?.name || item?.sector || '').trim();
  const code = String(item?.etf_code || item?.code || '').replace(/\D/g, '');
  if (name) return `sim:${sleeve}:${name}`;
  if (code) return `sim:etf:${code.padStart(6, '0')}`;
  return flexPositionKey(item);
}

/** Structural fingerprint for sim holdings (ignore mark prices / timestamps). */
function flexSimStructureSignature(positions, capital, asOf) {
  const keys = Object.keys(positions || {}).sort();
  const parts = keys.map(k => {
    const p = positions[k] || {};
    return [
      k,
      Math.round((Number(p.cost_basis) || 0) * 100),
      String(p.buy_date || ''),
      String(p.etf_code || ''),
      Math.round((Number(p.weight) || 0) * 1e6),
    ].join('|');
  });
  return `${asOf || ''}#${Math.round((Number(capital) || 0) * 100)}#${parts.join(';')}`;
}

/**
 * Rebuild simulation ledger to strictly mirror strategy paper open sleeves.
 * EOD marks only: cost @ entry open, last_price @ as_of close. Never touches real book.
 * Journal is append-only on *material* structure changes — book switch / re-render must not spam 流水.
 */
function rebuildSimLedgerFromStrategy(flex) {
  const f = flex || {};
  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);
  // Mark to last available EOD close (休市 → 上一交易日截止), not nowcast calendar date.
  const markAsOf = flexEffectiveMarkDate();
  const prev = flexApplyEodMarksToLedger(loadFlexLedgerForBook('sim'));
  let capital = Number(prev.capital) || 0;
  if (!(capital > 0)) {
    capital = Number(loadFlexLedgerForBook('real').capital) || 0;
  }
  if (!(capital > 0)) {
    const empty = defaultFlexLedger('sim');
    empty.strategy_as_of = asOf;
    empty.mark_as_of = markAsOf;
    empty.journal = Array.isArray(prev.journal) ? prev.journal.slice(-100) : [];
    empty.mark_policy = 'SIM_ENTRY_OPEN_MARK_CLOSE';
    empty.structure_sig = prev.structure_sig || '';
    return saveFlexLedger(empty);
  }

  const targets = collectStrategyPaperTargets(f);
  const positions = {};
  let deployed = 0;
  let marked = 0;
  let missingPx = 0;
  const uPnlParts = [];

  for (const t of targets) {
    const w = Number(t.weight) || 0;
    if (w <= 0) continue;
    const notional = Math.round(capital * w * 100) / 100;
    if (notional <= 0) continue;

    const buyDate = String(t.buy_date || asOf || flexDateCn(0)).slice(0, 10);
    const holdDays = t.hold_days != null ? Number(t.hold_days) : null;
    const exitDate = t.exit_date
      ? String(t.exit_date).slice(0, 10)
      : holdDays != null
        ? flexAddTradingDays(buyDate, holdDays)
        : null;

    const px = flexSimEodPrices(t.etf_code, buyDate, markAsOf);
    let entryPx = px.entry_open;
    let markPx = px.mark_close;
    let note = '模拟·策略纸面·入场开盘/盯市收盘';
    let quality = px.quality;
    if (!(entryPx > 0) || !(markPx > 0)) {
      missingPx += 1;
      quality = 'MISSING';
      note = '模拟·缺行情·涨跌幅不可用';
    } else {
      marked += 1;
      if (quality === 'SNAP') note += '·日期已吸附';
    }

    const costBasis = notional;
    const hasEntry = entryPx > 0;
    const hasMark = markPx > 0;
    const qty = hasEntry ? notional / entryPx : 0;
    const key = flexSimPositionKey(t);
    const mtmRet = hasEntry && hasMark ? (markPx / entryPx - 1) : null;
    const stopLoss = Number.isFinite(Number(t.stop_loss)) ? Number(t.stop_loss) : FLEX_SAT_STOP_LOSS_DEFAULT;
    const takeProfit = Number.isFinite(Number(t.take_profit)) ? Number(t.take_profit) : FLEX_SAT_TAKE_PROFIT_DEFAULT;
    const daysHeld = flexTradingDaysBetween(buyDate, px.mark_bar_date || markAsOf);
    const riskExit = String(t.sleeve || '').toLowerCase() === 'satellite'
        && mtmRet != null
        && daysHeld >= FLEX_SAT_MIN_HOLD_DAYS
      ? (mtmRet <= stopLoss ? 'STOP_LOSS' : (mtmRet >= takeProfit ? 'TAKE_PROFIT' : null))
      : null;
    if (riskExit) continue;
    // Also resolve legacy keys so re-key does not look like CLOSE+OPEN
    const old = prev.positions?.[key]
      || prev.positions?.[flexPositionKey(t)]
      || null;

    if (quality !== 'MISSING' && hasEntry && hasMark) {
      uPnlParts.push(qty * markPx - costBasis);
    }

    positions[key] = {
      id: old?.id || flexUid('sim'),
      key,
      name: t.name,
      etf_code: t.etf_code || '',
      etf_name: t.etf_name || '',
      sleeve: t.sleeve || '',
      weight: w,
      qty,
      avg_price: hasEntry ? entryPx : 0,
      cost_basis: costBasis,
      last_price: hasMark ? markPx : 0,
      opened_at: old?.opened_at || new Date().toISOString(),
      updated_at: new Date().toISOString(),
      signal_as_of: t.signal_as_of || '',
      buy_date: buyDate,
      hold_days: holdDays,
      exit_date: exitDate,
      entry_price_type: 'open',
      mark_price_type: 'close',
      entry_bar_date: px.entry_bar_date,
      mark_bar_date: px.mark_bar_date,
      mark_quality: quality,
      note,
      sim: true,
    };
    deployed += costBasis;
  }

  const nextSig = flexSimStructureSignature(positions, capital, asOf);
  const prevSig = prev.structure_sig || flexSimStructureSignature(prev.positions, prev.capital, prev.strategy_as_of);
  const structureChanged = nextSig !== prevSig;
  const asOfChanged = String(prev.strategy_as_of || '') !== String(asOf || '');

  // Quiet path: same paper structure → only refresh marks, keep journal untouched.
  if (!structureChanged && !asOfChanged && Object.keys(prev.positions || {}).length === Object.keys(positions).length) {
    const quiet = {
      ...prev,
      version: 2,
      book: 'sim',
      capital,
      cash: Math.max(0, Math.round((capital - deployed) * 100) / 100),
      positions,
      journal: Array.isArray(prev.journal) ? prev.journal : [],
      updated_at: new Date().toISOString(),
      strategy_as_of: asOf,
      mark_as_of: markAsOf,
      structure_sig: nextSig,
      mark_policy: 'SIM_ENTRY_OPEN_MARK_CLOSE',
      mark_policy_cn: '入场=开盘价 · 盯市=最近可得收盘（休市=上一交易日）',
    };
    dashboardState.flexSimSyncedAsOf = asOf;
    return saveFlexLedger(quiet);
  }

  const journal = Array.isArray(prev.journal) ? prev.journal.slice() : [];
  // Map prev positions by sim key + legacy key for stable diff
  const prevByKey = { ...(prev.positions || {}) };
  for (const [k, p] of Object.entries(prev.positions || {})) {
    const alt = flexSimPositionKey(p);
    if (alt && !prevByKey[alt]) prevByKey[alt] = p;
  }

  if (structureChanged) {
    for (const [key, pos] of Object.entries(positions)) {
      const old = prevByKey[key] || prev.positions?.[key];
      const costBasis = Number(pos.cost_basis) || 0;
      if (!old) {
        journal.push({
          id: flexUid('jn'),
          type: 'OPEN',
          type_cn: '模拟开仓',
          name: pos.name,
          etf_code: pos.etf_code || '',
          amount: costBasis,
          price: Number(pos.avg_price) || 0,
          qty: Number(pos.qty) || 0,
          pnl: 0,
          at: new Date().toISOString(),
          note: `策略纸面开仓 · 权重 ${((Number(pos.weight) || 0) * 100).toFixed(1)}% · as_of=${asOf || '—'}`,
        });
        continue;
      }
      const prevCost = Math.round((Number(old.cost_basis) || 0) * 100) / 100;
      const nextCost = Math.round(costBasis * 100) / 100;
      const delta = nextCost - prevCost;
      // Ignore sub-yuan float noise when re-entering the same paper book
      if (Math.abs(delta) >= Math.max(1, capital * 0.001)) {
        journal.push({
          id: flexUid('jn'),
          type: delta > 0 ? 'ADD' : 'REDUCE',
          type_cn: delta > 0 ? '模拟加仓' : '模拟减仓',
          name: pos.name,
          etf_code: pos.etf_code || '',
          amount: Math.abs(delta),
          price: Number(pos.last_price) || Number(pos.avg_price) || 0,
          qty: Number(pos.avg_price) > 0 ? Math.abs(delta) / Number(pos.avg_price) : 0,
          pnl: 0,
          at: new Date().toISOString(),
          note: `目标权重 ${((Number(pos.weight) || 0) * 100).toFixed(1)}% · as_of=${asOf || '—'}`,
        });
      }
    }

    for (const [key, old] of Object.entries(prev.positions || {})) {
      const still = positions[key] || positions[flexSimPositionKey(old)];
      if (still) continue;
      if (!(Number(old.qty) > 0 || Number(old.cost_basis) > 0)) continue;
      const cost = Number(old.cost_basis) || 0;
      const mark = Number(old.last_price) > 0 && Number(old.qty) > 0
        ? Number(old.qty) * Number(old.last_price)
        : cost;
      const pnl = Math.round((mark - cost) * 100) / 100;
      const ret = cost > 0 ? pnl / cost : 0;
      const avoided = flexTargetIsAvoided(old, f);
      const riskStatus = flexSatelliteRiskStatus(old, f);
      journal.push({
        id: flexUid('jn'),
        type: 'CLOSE',
        type_cn: riskStatus?.triggered
          ? (riskStatus.close_code === 'LOCAL_STOP_LOSS' ? '模拟止损平仓' : '模拟止盈平仓')
          : (avoided ? '模拟回避清仓' : '模拟平仓'),
        name: old.name || '—',
        etf_code: old.etf_code || '',
        amount: Math.round(mark * 100) / 100,
        price: Number(old.last_price) || 0,
        qty: Number(old.qty) || 0,
        pnl,
        return_pct: Math.round(ret * 1e6) / 1e6,
        at: new Date().toISOString(),
        note: riskStatus?.triggered
          ? `${riskStatus.label} · as_of=${asOf || '—'}`
          : (avoided
            ? `AVOID 归零 · as_of=${asOf || '—'}`
            : `策略纸面退出 · as_of=${asOf || '—'}`),
      });
    }
  }

  const uPnlSum = uPnlParts.reduce((s, x) => s + x, 0);
  const uRet = deployed > 0 ? uPnlSum / deployed : 0;
  // One SYNC per new strategy as_of only (not every tab switch)
  if (asOfChanged) {
    journal.push({
      id: flexUid('jn'),
      type: 'SYNC',
      type_cn: '模拟EOD同步',
      name: '策略纸面',
      etf_code: '',
      amount: deployed,
      price: 0,
      qty: 0,
      pnl: Math.round(uPnlSum * 100) / 100,
      return_pct: Math.round(uRet * 1e6) / 1e6,
      at: new Date().toISOString(),
      note: [
        `as_of=${asOf || '—'}`,
        `持仓 ${Object.keys(positions).length}`,
        `EOD盯市 ${marked}`,
        missingPx ? `缺价 ${missingPx}` : null,
        `涨跌幅 ${flexFormatSignedPct(uRet)}`,
      ].filter(Boolean).join(' · '),
    });
  }

  const ledger = {
    version: 2,
    book: 'sim',
    capital,
    cash: Math.max(0, Math.round((capital - deployed) * 100) / 100),
    positions,
    journal: journal.slice(-100),
    updated_at: new Date().toISOString(),
    strategy_as_of: asOf,
    mark_as_of: markAsOf,
    structure_sig: nextSig,
    mark_policy: 'SIM_ENTRY_OPEN_MARK_CLOSE',
    mark_policy_cn: '入场=开盘价 · 盯市=最近可得收盘（休市=上一交易日）',
  };
  dashboardState.flexSimSyncedAsOf = asOf;
  return saveFlexLedger(ledger);
}

function flexPositionKey(item) {
  const code = String(item?.etf_code || item?.code || '').trim();
  if (code) return `etf:${code}`;
  const name = String(item?.name || item?.sector || 'unknown').trim();
  const sleeve = String(item?.sleeve || 'na').trim();
  return `name:${sleeve}:${name}`;
}

function flexAvailableCash(ledger) {
  const cash = Number(ledger?.cash);
  if (Number.isFinite(cash)) return Math.max(0, cash);
  return Math.max(0, (Number(ledger?.capital) || 0) - flexDeployedCost(ledger));
}

function flexEquity(ledger) {
  return flexAvailableCash(ledger) + flexMarkValue(ledger);
}

function flexUnrealizedPnl(ledger) {
  return flexMarkValue(ledger) - flexDeployedCost(ledger);
}

/** Sum realized PnL from journal (CLOSE / REDUCE with pnl). */
function flexRealizedPnl(ledger) {
  return (ledger?.journal || []).reduce((sum, row) => {
    const t = String(row.type || '').toUpperCase();
    if (t !== 'CLOSE' && t !== 'REDUCE') return sum;
    const p = Number(row.pnl);
    return sum + (Number.isFinite(p) ? p : 0);
  }, 0);
}

function flexFormatSignedMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const abs = formatMoney(Math.abs(n));
  if (n > 0) return `+${abs}`;
  if (n < 0) return `-${abs}`;
  return abs;
}

/** ratio 0.025 → +2.50% (涨跌幅，非金额) */
function flexFormatSignedPct(ratio, digits = 2) {
  const n = Number(ratio);
  if (!Number.isFinite(n)) return '—';
  const pct = n * 100;
  if (Math.abs(pct) < 1e-12) return `${(0).toFixed(digits)}%`;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(digits)}%`;
}

/** Unrealized return: (mark - cost) / cost. null when marks missing (never fake 0%). */
function flexPositionReturnPct(pos) {
  if (String(pos?.mark_quality || '') === 'MISSING') return null;
  const cost = Number(pos?.cost_basis);
  if (!(cost > 0)) {
    const avg = Number(pos?.avg_price);
    const mark = Number(pos?.last_price);
    if (avg > 0 && mark > 0) return mark / avg - 1;
    return null;
  }
  const markPx = Number(pos?.last_price);
  const qty = Number(pos?.qty);
  if (!(qty > 0) || !(markPx > 0)) return null;
  const mtm = qty * markPx;
  return mtm / cost - 1;
}

function flexSatelliteRiskRule(flex) {
  const rule = flex?.satellite_risk_rule || {};
  const stopLoss = Number(rule.stop_loss);
  const takeProfit = Number(rule.take_profit);
  return {
    stopLoss: Number.isFinite(stopLoss) ? stopLoss : FLEX_SAT_STOP_LOSS_DEFAULT,
    takeProfit: Number.isFinite(takeProfit) ? takeProfit : FLEX_SAT_TAKE_PROFIT_DEFAULT,
    ruleCn: rule.rule_cn || `卫星持有满${FLEX_SAT_MIN_HOLD_DAYS}日后，收益≤${flexFormatSignedPct(FLEX_SAT_STOP_LOSS_DEFAULT, 0)}止损；≥${flexFormatSignedPct(FLEX_SAT_TAKE_PROFIT_DEFAULT, 0)}止盈`,
    priceBasisCn: rule.price_basis_cn || '按成交均价/入场开盘价与最近可得收盘价计算',
  };
}

function flexSatelliteRiskStatus(pos, flex) {
  if (String(pos?.sleeve || '').toLowerCase() !== 'satellite') return null;
  const ret = flexPositionReturnPct(pos);
  const rule = flexSatelliteRiskRule(flex);
  const markDay = pos?.mark_bar_date || flex?.as_of || flex?.market_state?.trade_date || flexSessionTradeDate();
  const daysHeld = flexPositionDaysHeld(pos, markDay);
  if (ret == null) {
    return {
      triggered: false,
      label: `满${FLEX_SAT_MIN_HOLD_DAYS}日后查止损${flexFormatSignedPct(rule.stopLoss, 0)} / 止盈${flexFormatSignedPct(rule.takeProfit, 0)} · 缺价`,
      rule,
    };
  }
  if (daysHeld < FLEX_SAT_MIN_HOLD_DAYS) {
    return {
      triggered: false,
      label: `已持有${daysHeld}日 · 满${FLEX_SAT_MIN_HOLD_DAYS}日后检查止损/止盈`,
      rule,
      ret,
      daysHeld,
    };
  }
  if (ret <= rule.stopLoss) {
    return {
      triggered: true,
      close_code: 'LOCAL_STOP_LOSS',
      action_cn: '卫星止损卖出',
      badge: '止损平仓',
      label: `已触发止损 ${flexFormatSignedPct(ret)} ≤ ${flexFormatSignedPct(rule.stopLoss, 0)}`,
      why: `卫星持仓收益 ${flexFormatSignedPct(ret)} 已低于止损线 ${flexFormatSignedPct(rule.stopLoss, 0)}；按规则下一交易日开盘平仓`,
      rule,
      ret,
    };
  }
  if (ret >= rule.takeProfit) {
    return {
      triggered: true,
      close_code: 'LOCAL_TAKE_PROFIT',
      action_cn: '卫星止盈卖出',
      badge: '止盈平仓',
      label: `已触发止盈 ${flexFormatSignedPct(ret)} ≥ ${flexFormatSignedPct(rule.takeProfit, 0)}`,
      why: `卫星持仓收益 ${flexFormatSignedPct(ret)} 已高于止盈线 ${flexFormatSignedPct(rule.takeProfit, 0)}；按规则下一交易日开盘平仓`,
      rule,
      ret,
    };
  }
  const toStop = ret - rule.stopLoss;
  const toTake = rule.takeProfit - ret;
  return {
    triggered: false,
    label: `距止损${(toStop * 100).toFixed(1)}个百分点 · 距止盈${(toTake * 100).toFixed(1)}个百分点`,
    rule,
    ret,
  };
}

function flexSuggestedAmount(item, capital) {
  const cap = Number(capital) || 0;
  if (!(cap > 0)) return null;
  const w = Number(item?.weight_target);
  if (Number.isFinite(w) && w > 0) return Math.round(cap * w);
  const hint = String(item?.weight_hint || '');
  const m = hint.match(/(\d+(?:\.\d+)?)\s*%/);
  if (m) return Math.round(cap * (Number(m[1]) / 100));
  return null;
}

function setFlexTabBadge(id, count) {
  const el = document.getElementById(id);
  if (!el) return;
  const n = Number(count) || 0;
  if (n > 0) {
    el.hidden = false;
    el.textContent = n > 99 ? '99+' : String(n);
  } else {
    el.hidden = true;
    el.textContent = '0';
  }
}

function appendFlexJournal(ledger, entry) {
  ledger.journal = [
    {
      id: flexUid('jn'),
      ts: new Date().toISOString(),
      ...entry,
    },
    ...(ledger.journal || []),
  ].slice(0, 200);
}

/** Natural-day helpers (ONLY for desk buy-window T/T+1 calendar, not hold length). */
function flexAddCalendarDays(dateStr, days) {
  const day = String(dateStr || flexDateCn(0)).slice(0, 10);
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + Number(days || 0));
  return dt.toISOString().slice(0, 10);
}

function flexCalendarDaysBetween(fromStr, toStr) {
  const a = String(fromStr || '').slice(0, 10);
  const b = String(toStr || flexDateCn(0)).slice(0, 10);
  if (!a || !b) return 0;
  const [y1, m1, d1] = a.split('-').map(Number);
  const [y2, m2, d2] = b.split('-').map(Number);
  const t1 = Date.UTC(y1, m1 - 1, d1);
  const t2 = Date.UTC(y2, m2 - 1, d2);
  return Math.max(0, Math.round((t2 - t1) / 86400000));
}

// ---------------------------------------------------------------------------
// Trading-day calendar — MUST match flex_engine / backtest:
//   days_held = index(as_of) - index(entry_date) on trade_date list
//   close when days_held >= hold_days  → exit signal date = entry + hold_days trade steps
// ---------------------------------------------------------------------------

function flexExtendTradeDatesForward(sorted, extraN) {
  const out = (sorted || []).slice();
  if (!out.length) return out;
  let d = new Date(`${out[out.length - 1]}T00:00:00Z`);
  let added = 0;
  const need = Math.max(0, Number(extraN) || 0);
  while (added < need) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay(); // 0=Sun … 6=Sat
    if (wd !== 0 && wd !== 6) {
      out.push(d.toISOString().slice(0, 10));
      added += 1;
    }
  }
  return out;
}

function flexGenerateWeekdayRange(fromStr, toStr) {
  const out = [];
  let d = new Date(`${String(fromStr).slice(0, 10)}T00:00:00Z`);
  const end = new Date(`${String(toStr).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || Number.isNaN(end.getTime())) return out;
  while (d <= end) {
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

/** Build sorted trade_date list from site history + nowcast + flex as_of; extend weekdays forward. */
function flexEnsureTradeCalendar() {
  const set = new Set();
  const add = (v) => {
    const s = String(v || '').slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) set.add(s);
  };
  for (const row of dashboardState.history || []) add(row.date || row.trade_date);
  const nh = dashboardState.nowcastHistory;
  const nhRows = Array.isArray(nh) ? nh : (nh?.rows || []);
  for (const row of nhRows) add(row.date || row.trade_date);
  add(dashboardState.lastTradeDate);
  const flex = dashboardState.flexActive || dashboardState.flexPlaybook?.flex_panel;
  add(flex?.as_of);
  add(flex?.market_state?.trade_date);
  const satPaths = flex?.exit_plan?.satellite?.paths || {};
  Object.values(satPaths).forEach(add);
  const coreEp = flex?.exit_plan?.core || {};
  add(coreEp.max_signal_date);
  add(coreEp.max_exec_next_open);
  add(coreEp.exit_due_date);
  add(flex?.position_state?.core?.entry_date);
  add(flex?.position_state?.satellite?.entry_date);

  let list = Array.from(set).sort();
  // Fallback if history not loaded yet: Mon–Fri skeleton (A-share proxy; no holiday table).
  if (list.length < 30) {
    const today = flexDateCn(0);
    const skeleton = flexGenerateWeekdayRange('2020-01-01', flexAddCalendarDays(today, 200));
    list = Array.from(new Set([...skeleton, ...list])).sort();
  } else {
    list = flexExtendTradeDatesForward(list, 100);
  }
  dashboardState.flexTradeDates = list;
  return list;
}

/** Index of trade date on or after day (entry snap). */
function flexTradeDateIndexOnOrAfter(dateStr, dates = flexEnsureTradeCalendar()) {
  const day = String(dateStr || '').slice(0, 10);
  if (!day || !dates.length) return 0;
  const exact = dates.indexOf(day);
  if (exact >= 0) return exact;
  for (let k = 0; k < dates.length; k += 1) {
    if (dates[k] >= day) return k;
  }
  return dates.length - 1;
}

/** Index of trade date on or before day (as_of / "today" snap). */
function flexTradeDateIndexOnOrBefore(dateStr, dates = flexEnsureTradeCalendar()) {
  const day = String(dateStr || '').slice(0, 10);
  if (!day || !dates.length) return 0;
  const exact = dates.indexOf(day);
  if (exact >= 0) return exact;
  for (let k = dates.length - 1; k >= 0; k -= 1) {
    if (dates[k] <= day) return k;
  }
  return 0;
}

/**
 * Advance N trading sessions from dateStr (N can be 0).
 * Aligns with engine: exit_i = entry_i + HOLD_DAYS on trade_date array.
 */
function flexAddTradingDays(dateStr, n) {
  const steps = Math.trunc(Number(n) || 0);
  let dates = flexEnsureTradeCalendar();
  let i = flexTradeDateIndexOnOrAfter(dateStr, dates);
  let j = i + steps;
  // Ensure enough future sessions (weekends skipped; holidays only if present in history).
  while (j >= dates.length) {
    dates = flexExtendTradeDatesForward(dates, 40);
    dashboardState.flexTradeDates = dates;
  }
  j = Math.max(0, Math.min(j, dates.length - 1));
  return dates[j];
}

/**
 * Trading-day distance index(to) - index(from), matching flex_engine days_held
 * when from=entry_date and to=as_of (as_of snapped on-or-before).
 */
function flexTradingDaysBetween(fromStr, toStr) {
  const dates = flexEnsureTradeCalendar();
  if (!fromStr || !toStr || !dates.length) return 0;
  const i = flexTradeDateIndexOnOrAfter(fromStr, dates);
  const j = flexTradeDateIndexOnOrBefore(toStr, dates);
  return j - i;
}

/** Engine-equivalent days_held for a local position. */
function flexPositionDaysHeld(pos, asOf = flexDateCn(0)) {
  if (!pos?.buy_date) return 0;
  return Math.max(0, flexTradingDaysBetween(pos.buy_date, asOf));
}

/**
 * Exit signal date = buy_date + hold_days trading steps (days_held >= hold_days).
 * Same formula as backtest exit_i = entry_i + HOLD_DAYS.
 */
function flexPositionExitSignalDate(pos) {
  if (!pos) return null;
  if (pos.buy_date && pos.hold_days != null && Number.isFinite(Number(pos.hold_days))) {
    return flexAddTradingDays(pos.buy_date, Number(pos.hold_days));
  }
  // Strategy-provided due date (already on trade calendar when from exit_plan).
  if (pos.exit_date) return String(pos.exit_date).slice(0, 10);
  return null;
}

/** Remaining hold days / exit date — trading days only (real + sim books). */
function flexPositionExitInfo(pos) {
  if (!pos || !(Number(pos.qty) > 0)) return { left: null, exitDate: null, label: '—', daysHeld: null };
  const today = flexDateCn(0);
  const holdDays = pos.hold_days != null && Number.isFinite(Number(pos.hold_days))
    ? Number(pos.hold_days)
    : null;
  const daysHeld = pos.buy_date ? flexPositionDaysHeld(pos, today) : null;
  const exitDate = flexPositionExitSignalDate(pos);
  let left = null;
  if (holdDays != null && daysHeld != null) {
    left = Math.max(0, holdDays - daysHeld);
  } else if (exitDate) {
    // Trading sessions from today (on/before) to exit signal date.
    left = Math.max(0, flexTradingDaysBetween(today, exitDate));
    if (exitDate < String(today).slice(0, 10) && left > 0) {
      // today after exit on calendar but snap can still be positive; force 0 if exit passed
      const dates = flexEnsureTradeCalendar();
      const jToday = flexTradeDateIndexOnOrBefore(today, dates);
      const jExit = flexTradeDateIndexOnOrAfter(exitDate, dates);
      if (jToday >= jExit) left = 0;
    }
  }
  const exitMd = exitDate ? flexFormatMdBuy(exitDate)?.replace(/买$/, '清') : null;
  const label = left != null
    ? (exitMd ? `剩${left}交易日 · ${exitMd}` : `剩${left}交易日`)
    : '—';
  return { left, exitDate, label, daysHeld };
}

function flexApplyBuy(ledger, draft) {
  ledger = normalizeFlexLedger(ledger);
  const amount = Number(draft.amount);
  const price = Number(draft.price);
  if (!(amount > 0) || !(price > 0)) {
    throw new Error('请输入有效的买入金额和成交价');
  }
  const cash = flexAvailableCash(ledger);
  if (amount > cash + 1e-6) {
    throw new Error(`可用现金不足（约 ${formatMoney(cash)} 元）`);
  }
  const qty = amount / price;
  const key = draft.key;
  const existing = ledger.positions[key];
  const buyDate = String(draft.buy_date || flexDateCn(0)).slice(0, 10);
  const holdDays = draft.hold_days != null && Number(draft.hold_days) >= 0
    ? Number(draft.hold_days)
    : null;
  // Exit signal date on trade calendar (engine: entry_i + HOLD_DAYS). Prefer recompute from hold_days.
  const exitDate = holdDays != null
    ? flexAddTradingDays(buyDate, holdDays)
    : (draft.exit_date ? String(draft.exit_date).slice(0, 10) : null);

  if (existing && Number(existing.qty) > 0) {
    const newQty = Number(existing.qty) + qty;
    const newCost = Number(existing.cost_basis) + amount;
    existing.qty = newQty;
    existing.cost_basis = newCost;
    existing.avg_price = newCost / newQty;
    existing.last_price = price;
    existing.updated_at = new Date().toISOString();
    if (draft.signal_as_of) existing.signal_as_of = draft.signal_as_of;
    // Keep original buy_date / exit plan on add; only refresh mark.
  } else {
    ledger.positions[key] = {
      id: flexUid('pos'),
      key,
      name: draft.name,
      etf_code: draft.etf_code || '',
      etf_name: draft.etf_name || '',
      sleeve: draft.sleeve || '',
      qty,
      avg_price: price,
      cost_basis: amount,
      last_price: price,
      opened_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      signal_as_of: draft.signal_as_of || '',
      buy_date: buyDate,
      hold_days: holdDays,
      exit_date: exitDate,
      note: draft.note || '',
    };
  }
  ledger.cash = cash - amount;
  appendFlexJournal(ledger, {
    type: existing ? 'ADD' : 'BUY',
    type_cn: existing ? '加仓' : '买入',
    key,
    name: draft.name,
    etf_code: draft.etf_code || '',
    amount,
    price,
    qty,
    signal_as_of: draft.signal_as_of || '',
  });
  return saveFlexLedger(ledger);
}

function flexApplyReduce(ledger, key, { amount, price, pct }) {
  ledger = normalizeFlexLedger(ledger);
  const pos = ledger.positions[key];
  if (!pos || !(Number(pos.qty) > 0)) throw new Error('持仓不存在或已平仓');
  const px = Number(price);
  if (!(px > 0)) throw new Error('请输入有效成交价');

  let sellQty = 0;
  let sellAmount = 0;
  // Prefer explicit amount when provided; otherwise use percent.
  if (amount != null && Number(amount) > 0) {
    sellAmount = Number(amount);
    sellQty = sellAmount / px;
  } else if (pct != null && Number(pct) > 0) {
    const ratio = Math.min(100, Math.max(0, Number(pct))) / 100;
    sellQty = Number(pos.qty) * ratio;
    sellAmount = sellQty * px;
  } else {
    throw new Error('请输入减仓金额或比例');
  }
  if (sellQty > Number(pos.qty) + 1e-9) {
    throw new Error('减仓数量超过持仓');
  }

  const costRemoved = (Number(pos.cost_basis) / Number(pos.qty)) * sellQty;
  const remainQty = Number(pos.qty) - sellQty;
  const pnl = sellAmount - costRemoved;
  // Proceeds return to cash (realized PnL included).
  ledger.cash = Number(ledger.cash) + sellAmount;

  if (remainQty <= 1e-8) {
    delete ledger.positions[key];
    appendFlexJournal(ledger, {
      type: 'CLOSE',
      type_cn: '平仓',
      key,
      name: pos.name,
      etf_code: pos.etf_code || '',
      amount: sellAmount,
      price: px,
      qty: sellQty,
      pnl,
    });
  } else {
    pos.qty = remainQty;
    pos.cost_basis = Math.max(0, Number(pos.cost_basis) - costRemoved);
    pos.avg_price = pos.cost_basis / remainQty;
    pos.last_price = px;
    pos.updated_at = new Date().toISOString();
    appendFlexJournal(ledger, {
      type: 'REDUCE',
      type_cn: '减仓',
      key,
      name: pos.name,
      etf_code: pos.etf_code || '',
      amount: sellAmount,
      price: px,
      qty: sellQty,
      pnl,
    });
  }
  return saveFlexLedger(ledger);
}

function flexApplyClose(ledger, key, price) {
  const pos = ledger.positions[key];
  if (!pos || !(Number(pos.qty) > 0)) throw new Error('持仓不存在或已平仓');
  return flexApplyReduce(ledger, key, {
    amount: Number(pos.qty) * Number(price),
    price,
  });
}

function renderFlexAccountBar() {
  const raw = loadFlexLedger();
  // Always remount to last available EOD close for totals (休市=上一交易日截止).
  const ledger = flexApplyEodMarksToLedger(raw);
  const capitalInput = document.getElementById('flexCapitalInput');
  if (capitalInput && document.activeElement !== capitalInput) {
    capitalInput.value = ledger.capital > 0 ? String(ledger.capital) : '';
  }
  const deployed = flexDeployedCost(ledger);
  const cash = flexAvailableCash(ledger);
  const mtm = flexMarkValue(ledger);
  const equity = flexEquity(ledger);
  const uPnl = flexUnrealizedPnl(ledger);
  const rPnl = flexRealizedPnl(ledger);
  const capital = Number(ledger.capital) || 0;
  const hasBook = capital > 0 || cash > 0 || deployed > 0 || mtm > 0;
  const exposureBase = equity > 0 ? equity : capital;
  const exposure = exposureBase > 0 && mtm > 0 ? mtm / exposureBase : (hasBook ? 0 : null);

  setText('flexExecEquity', hasBook ? formatMoney(equity) : '—');
  setText('flexExecCash', hasBook ? formatMoney(cash) : '—');
  setText('flexExecMtm', hasBook ? formatMoney(mtm) : '—');
  setText('flexExecDeployed', hasBook ? formatMoney(deployed) : '—');
  setText('flexExecExposure', exposure != null ? pctLabel(exposure) : '—');
  setText('flexExecCount', String(flexOpenPositions(ledger).length));

  // 收益只展示涨跌幅（%），不展示金额
  const uRet = deployed > 0 ? uPnl / deployed : null;
  const rRet = capital > 0 ? rPnl / capital : null;

  const uEl = document.getElementById('flexExecUPnl');
  if (uEl) {
    uEl.textContent = hasBook && uRet != null ? flexFormatSignedPct(uRet) : (hasBook ? '0.00%' : '—');
    uEl.classList.remove('up', 'down');
    // Never classList.add('') — DOMTokenList rejects empty tokens.
    if (hasBook && uRet != null) {
      if (uRet > 0) uEl.classList.add('up');
      else if (uRet < 0) uEl.classList.add('down');
    }
  }
  const rEl = document.getElementById('flexExecRPnl');
  if (rEl) {
    rEl.textContent = hasBook && rRet != null ? flexFormatSignedPct(rRet) : (hasBook ? '0.00%' : '—');
    rEl.classList.remove('up', 'down');
    if (hasBook && rRet != null) {
      if (rRet > 0) rEl.classList.add('up');
      else if (rRet < 0) rEl.classList.add('down');
    }
  }

  const note = document.getElementById('flexMarkNote');
  if (note) {
    const md = ledger.mark_as_of || flexEffectiveMarkDate();
    const win = typeof getAshareActionWindow === 'function' ? getAshareActionWindow() : { inSession: false };
    if (hasBook && md) {
      note.hidden = false;
      note.textContent = win.inSession
        ? `盯市：最近收盘 ${md}（盘中无实时 ETF 报价，仍用日终）`
        : `盯市：上一交易日收盘 ${md}（休市总涨跌幅）`;
    } else {
      note.hidden = true;
      note.textContent = '';
    }
  }

  const capitalHint = document.getElementById('flexCapitalHint');
  if (capitalHint) {
    // Shown only on signal tab when capital unset — toggled in renderFlexSignalList too
    capitalHint.hidden = capital > 0;
  }

  setFlexTabBadge('flexTabBadgeBook', flexOpenPositions(ledger).length);
  setFlexTabBadge('flexTabBadgeLog', (ledger.journal || []).length);
}

function renderFlexHoldings() {
  const el = document.getElementById('flexHoldingsList');
  if (!el) return;
  const ledger = flexApplyEodMarksToLedger(loadFlexLedger());
  const positions = flexOpenPositions(ledger);
  const capital = Number(ledger.capital) || 0;
  if (!positions.length) {
    el.innerHTML = `<div class="flex-empty-state soft">
      <strong>${isFlexSimBook() ? '模拟仓暂无持仓' : '真实仓暂无持仓'}</strong>
      <p>${isFlexSimBook()
        ? '请先保存全仓金额；策略纸面 open 时将自动同步持仓（EOD 开盘入场/收盘盯市）。'
        : '在「信号」里点「记买入」并录入成交价后入账。数据只存在当前浏览器，可导出备份。'}</p>
    </div>`;
    return;
  }
  positions.sort((a, b) => (Number(b.cost_basis) || 0) - (Number(a.cost_basis) || 0));
  el.innerHTML = positions.map(pos => {
    const weight = capital > 0 ? (Number(pos.cost_basis) / capital) : null;
    const missingPx = String(pos.mark_quality || '') === 'MISSING';
    const ret = flexPositionReturnPct(pos);
    const pnlCls = ret == null ? '' : ret > 0 ? 'up' : ret < 0 ? 'down' : '';
    const pnlTxt = missingPx ? '缺价' : (ret == null ? '—' : flexFormatSignedPct(ret));
    const exitInfo = flexPositionExitInfo(pos);
    const riskStatus = flexSatelliteRiskStatus(pos, dashboardState.flexPlaybook?.flex);
    const exitLabel = riskStatus?.triggered
      ? riskStatus.label
      : [exitInfo.label, riskStatus?.label].filter(Boolean).join(' · ');
    const markDay = pos.mark_bar_date || ledger.mark_as_of || '';
    const titleBits = [
      exitLabel,
      riskStatus?.rule?.ruleCn,
      missingPx
        ? '缺 EOD 行情，涨跌幅不可用'
        : (ret != null ? `涨跌幅 ${flexFormatSignedPct(ret)} · 收盘 ${markDay || '—'}` : ''),
      Number(pos.avg_price) > 0 ? `入场 ${formatPrice(pos.avg_price)}` : (missingPx ? '入场价缺失' : ''),
      Number(pos.last_price) > 0 ? `盯市 ${formatPrice(pos.last_price)}` : (missingPx ? '盯市价缺失' : ''),
      pos.note || '',
    ].filter(Boolean).join(' · ');
    return `<div class="flex-row flex-row-book" data-pos-key="${escapeHtml(pos.key)}" title="${escapeHtml(titleBits)}">
      <span class="flex-row-code" data-label="代码">${escapeHtml(pos.etf_code || '—')}</span>
      <span class="flex-row-name" data-label="名称">${escapeHtml(pos.name || '—')}</span>
      <span class="flex-row-num" data-label="成本">${formatMoney(pos.cost_basis)}</span>
      <span class="flex-row-num" data-label="均价">${Number(pos.avg_price) > 0 ? formatPrice(pos.avg_price) : (missingPx ? '缺价' : '—')}</span>
      <span class="flex-row-num" data-label="仓位">${weight != null ? pctLabel(weight) : '—'}</span>
      <span class="flex-row-num ${pnlCls}" data-label="涨跌幅">${pnlTxt}</span>
      <span class="flex-row-num flex-row-exit" data-label="清仓">${escapeHtml(exitLabel)}</span>
      <span class="flex-row-acts" data-label="操作">
        <button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(pos.key)}">加</button>
        <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(pos.key)}">减</button>
        <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(pos.key)}">平</button>
      </span>
    </div>`;
  }).join('');
}

function renderFlexJournal() {
  const el = document.getElementById('flexJournalList');
  if (!el) return;
  const ledger = loadFlexLedger();
  const rows = (ledger.journal || []).slice(0, 50);
  if (!rows.length) {
    el.innerHTML = `<div class="flex-empty-state soft">
      <strong>暂无流水</strong>
      <p>买卖、调仓、调整全仓金额会记录在此。可导出 JSON 备份。</p>
    </div>`;
    return;
  }
  el.innerHTML = rows.map(row => {
    const when = row.ts
      ? new Date(row.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      : '—';
    const label = row.type_cn || row.type || '—';
    const code = row.etf_code || row.name || '—';
    // Journal: prefer return % (pnl / cost). CLOSE/REDUCE: cost ≈ amount - pnl when amount is proceeds.
    let retTxt = '—';
    let retCls = '';
    const pnlN = Number(row.pnl);
    const amtN = Number(row.amount);
    if (Number.isFinite(pnlN)) {
      const t = String(row.type || '').toUpperCase();
      let cost = null;
      if ((t === 'CLOSE' || t === 'REDUCE') && Number.isFinite(amtN)) {
        cost = amtN - pnlN; // sell proceeds - pnl = cost removed
      } else if (Number.isFinite(amtN) && amtN > 0 && t === 'SYNC') {
        cost = amtN; // sim sync stores deployed notional in amount
      }
      if (cost != null && cost > 1e-9) {
        const ret = pnlN / cost;
        retTxt = flexFormatSignedPct(ret);
        retCls = ret > 0 ? 'up' : ret < 0 ? 'down' : '';
      } else if (row.return_pct != null && Number.isFinite(Number(row.return_pct))) {
        retTxt = flexFormatSignedPct(Number(row.return_pct));
        retCls = Number(row.return_pct) > 0 ? 'up' : Number(row.return_pct) < 0 ? 'down' : '';
      }
    }
    return `<div class="flex-row flex-row-log">
      <span class="flex-row-tag" data-label="类型">${escapeHtml(label)}</span>
      <span class="flex-row-code" data-label="代码">${escapeHtml(code)}</span>
      <span class="flex-row-num" data-label="金额">${formatMoney(row.amount)}</span>
      <span class="flex-row-num" data-label="价格">${Number(row.price) > 0 ? formatPrice(row.price) : '—'}</span>
      <span class="flex-row-num ${retCls}" data-label="涨跌幅">${retTxt}</span>
      <span class="flex-row-time" data-label="时间">${when}</span>
    </div>`;
  }).join('');
}

function renderFlexExecUi() {
  renderFlexAccountBar();
  renderFlexHoldings();
  renderFlexJournal();
}

function closeFlexTradeModal() {
  const modal = document.getElementById('flexTradeModal');
  if (modal) modal.hidden = true;
  dashboardState.flexModal = null;
  const err = document.getElementById('flexModalError');
  if (err) {
    err.hidden = true;
    err.textContent = '';
  }
}

function updateFlexModalPreview() {
  const state = dashboardState.flexModal;
  if (!state) return;
  const preview = document.getElementById('flexModalPreview');
  const amountEl = document.getElementById('flexModalAmount');
  const priceEl = document.getElementById('flexModalPrice');
  const pctEl = document.getElementById('flexModalPct');
  if (!preview) return;

  const price = Number(priceEl?.value);
  const amount = Number(amountEl?.value);
  const pct = Number(pctEl?.value);
  const capital = Number(loadFlexLedger().capital) || 0;

  const ledger = loadFlexLedger();
  const cash = flexAvailableCash(ledger);

  if (state.mode === 'buy' || state.mode === 'add') {
    if (amount > 0 && price > 0) {
      const qty = amount / price;
      const w = capital > 0 ? pctLabel(amount / capital) : '—';
      const afterCash = cash - amount;
      preview.textContent = `约 ${formatShares(qty)} 份 · 占全仓 ${w} · 成交后现金约 ${formatMoney(Math.max(0, afterCash))}`;
    } else if (state.defaultAmount) {
      preview.textContent = `建议金额 ${formatMoney(state.defaultAmount)}（目标权重 × 全仓）；请填写成交价`;
    } else {
      preview.textContent = cash > 0 ? `可用现金 ${formatMoney(cash)}` : '请填写金额与成交价';
    }
    return;
  }
  if (state.mode === 'reduce') {
    const pos = ledger.positions[state.key];
    if (!pos) {
      preview.textContent = '—';
      return;
    }
    let sellQty = 0;
    let sellAmt = 0;
    if (amount > 0 && price > 0) {
      sellAmt = amount;
      sellQty = amount / price;
    } else if (pct > 0 && price > 0) {
      sellQty = Number(pos.qty) * (Math.min(100, pct) / 100);
      sellAmt = sellQty * price;
    }
    if (sellQty > 0) {
      const costRemoved = (Number(pos.cost_basis) / Number(pos.qty)) * sellQty;
      const pnl = sellAmt - costRemoved;
      const ret = costRemoved > 0 ? pnl / costRemoved : null;
      preview.textContent = `卖出 ${formatShares(sellQty)} · 金额 ${formatMoney(sellAmt)} · 预计涨跌幅 ${ret != null ? flexFormatSignedPct(ret) : '—'} · 剩余 ${formatShares(Math.max(0, Number(pos.qty) - sellQty))}`;
    } else {
      preview.textContent = '填写金额或比例，以及成交价';
    }
    return;
  }
  if (state.mode === 'close') {
    const pos = ledger.positions[state.key];
    if (!pos) {
      preview.textContent = '—';
      return;
    }
    if (price > 0) {
      const amt = Number(pos.qty) * price;
      const cost = Number(pos.cost_basis) || 0;
      const pnl = amt - cost;
      const ret = cost > 0 ? pnl / cost : null;
      preview.textContent = `全平约 ${formatMoney(amt)} · 预计涨跌幅 ${ret != null ? flexFormatSignedPct(ret) : '—'} · 回现金`;
    } else {
      preview.textContent = '请填写成交价（按本机持仓全平）';
    }
  }
}

function openFlexTradeModal(spec) {
  const modal = document.getElementById('flexTradeModal');
  if (!modal) return;
  const ledger = loadFlexLedger();
  if ((spec.mode === 'buy' || spec.mode === 'add') && !(Number(ledger.capital) > 0)) {
    document.getElementById('flexCapitalInput')?.focus();
    return;
  }

  dashboardState.flexModal = { ...spec };
  setText('flexModalTitle', spec.title || '确认');
  setText('flexModalSub', spec.subtitle || '');

  const amountField = document.getElementById('flexModalAmountField');
  const priceField = document.getElementById('flexModalPriceField');
  const pctField = document.getElementById('flexModalPctField');
  const amountEl = document.getElementById('flexModalAmount');
  const priceEl = document.getElementById('flexModalPrice');
  const pctEl = document.getElementById('flexModalPct');
  const amountLabel = document.getElementById('flexModalAmountLabel');
  const err = document.getElementById('flexModalError');
  if (err) {
    err.hidden = true;
    err.textContent = '';
  }

  const chips = document.getElementById('flexModalAmountChips');
  if (spec.mode === 'buy' || spec.mode === 'add') {
    if (amountField) amountField.hidden = false;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = true;
    if (chips) chips.hidden = false;
    if (amountLabel) amountLabel.textContent = '金额（元）';
    if (amountEl) amountEl.value = spec.defaultAmount != null ? String(spec.defaultAmount) : '';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  } else if (spec.mode === 'reduce') {
    if (amountField) amountField.hidden = false;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = false;
    if (chips) chips.hidden = true;
    if (amountLabel) amountLabel.textContent = '金额（元）';
    if (amountEl) amountEl.value = '';
    if (pctEl) pctEl.value = '50';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  } else if (spec.mode === 'close') {
    if (amountField) amountField.hidden = true;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = true;
    if (chips) chips.hidden = true;
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  }

  modal.hidden = false;
  updateFlexModalPreview();
  (priceEl || amountEl)?.focus();
}

function confirmFlexTradeModal() {
  const state = dashboardState.flexModal;
  if (!state) return;
  const err = document.getElementById('flexModalError');
  const amount = Number(document.getElementById('flexModalAmount')?.value);
  const price = Number(document.getElementById('flexModalPrice')?.value);
  const pct = Number(document.getElementById('flexModalPct')?.value);
  try {
    let ledger = loadFlexLedger();
    if (state.mode === 'buy' || state.mode === 'add') {
      ledger = flexApplyBuy(ledger, {
        key: state.key,
        name: state.name,
        etf_code: state.etf_code,
        etf_name: state.etf_name,
        sleeve: state.sleeve,
        amount,
        price,
        signal_as_of: state.signal_as_of || '',
        buy_date: flexDateCn(0),
        hold_days: state.hold_days != null && Number.isFinite(Number(state.hold_days))
          ? Number(state.hold_days)
          : (dashboardState.flexActive?.hold_days ?? 5),
      });
    } else if (state.mode === 'reduce') {
      ledger = flexApplyReduce(ledger, state.key, {
        amount: amount > 0 ? amount : null,
        price,
        pct: pct > 0 ? pct : null,
      });
    } else if (state.mode === 'close') {
      ledger = flexApplyClose(ledger, state.key, price);
    }
    closeFlexTradeModal();
    const labels = { buy: '买入已记账', add: '加仓已记账', reduce: '减仓已记账', close: '平仓已记账' };
    flexToast(labels[state.mode] || '已记账', 'ok', 1800);
    if (dashboardState.flexPlaybook) {
      renderFlexTradePanel(dashboardState.flexPlaybook);
    } else {
      renderFlexExecUi();
    }
    // After fill, take user to holdings to see the result.
    if (state.mode === 'buy' || state.mode === 'add' || state.mode === 'close') {
      flexSwitchTab('book');
    }
  } catch (e) {
    if (err) {
      err.hidden = false;
      err.textContent = e.message || String(e);
    }
    flexToast(e.message || '记账失败', 'err', 2800);
  }
}

function flexShortAction(action, actionCn) {
  const a = String(action || '').toUpperCase();
  if (FLEX_ACTION_BADGE[a]) return FLEX_ACTION_BADGE[a].text;
  const cn = String(actionCn || '');
  if (/持有|持仓/.test(cn)) return '持有信号';
  if (/新开|开仓/.test(cn)) return '新开';
  if (/买|超配/.test(cn)) return '买入';
  if (/卖|平/.test(cn)) return '平仓';
  if (/回避|低配/.test(cn)) return '回避';
  return cn.slice(0, 4) || '—';
}

function flexFormatMdBuy(dateStr) {
  if (!dateStr || dateStr === '—' || dateStr === '-') return null;
  const m = String(dateStr).match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (m) return `${Number(m[2])}月${Number(m[3])}日买`;
  const m2 = String(dateStr).match(/(\d{1,2})月(\d{1,2})日/);
  if (m2) return `${Number(m2[1])}月${Number(m2[2])}日买`;
  return null;
}

/** Resolve execution entry date for HOLD badge: sleeve state → item fields. */
function flexResolveEntryDate(item, flex) {
  const direct = item?.entry_date || item?.entry_signal_date || null;
  if (direct && String(direct).includes('-')) return String(direct).slice(0, 10);
  const entryTxt = flexFormatMdBuy(item?.entry);
  if (entryTxt) {
    // entry was display text only; fall through to state
  } else if (item?.entry && String(item.entry).match(/\d{4}-\d{2}-\d{2}/)) {
    return String(item.entry).match(/\d{4}-\d{2}-\d{2}/)[0];
  }
  const state = flex?.position_state || {};
  const sleeve = String(item?.sleeve || '').toLowerCase();
  if (sleeve === 'core' && state.core?.entry_date) return String(state.core.entry_date).slice(0, 10);
  if (sleeve === 'satellite' && state.satellite?.entry_date) return String(state.satellite.entry_date).slice(0, 10);
  // Match by etf/name against core sleeve
  const code = String(item?.etf_code || '');
  const name = String(item?.name || '');
  if (state.core?.etf_code && code && state.core.etf_code === code && state.core.entry_date) {
    return String(state.core.entry_date).slice(0, 10);
  }
  if (Array.isArray(state.core?.names) && state.core.names.includes(name) && state.core.entry_date) {
    return String(state.core.entry_date).slice(0, 10);
  }
  if (Array.isArray(state.satellite?.names) && state.satellite.names.includes(name) && state.satellite.entry_date) {
    return String(state.satellite.entry_date).slice(0, 10);
  }
  return null;
}

/**
 * Desk badge for open signals:
 * - signal day T → 可买(至T+1)
 * - T+1 → T+1可确认
 * Paper HOLD is never a buy badge.
 */
function flexActionBadge(item, flex, options = {}) {
  const action = String(item?.action || item?.side || '').toUpperCase();
  if (action === 'HOLD') {
    return options.localHeld
      ? { text: '持有中', cls: 'hold' }
      : { text: '—', cls: 'wait' };
  }
  if (FLEX_BUY_ACTIONS.has(action) || action === 'OPEN') {
    const asOf = options.signalAsOf || item?.signal_as_of || flex?.as_of || '';
    const lag = flexBookLagDays(asOf);
    if (lag === 1) return { text: 'T+1可确认', cls: 'buy' };
    return { text: '可买·至T+1', cls: 'buy' };
  }
  if (FLEX_CLOSE_ACTIONS.has(action) || action === 'CLOSE') {
    if (item?._strategyPaper && !options.localHeld) {
      return { text: '策略平仓', cls: 'sell' };
    }
    const code = item?.close_code || '';
    if (code === 'MAX_HOLD' || code === 'CORE_MAX_HOLD') return { text: '到期平仓', cls: 'sell' };
    if (code === 'EVENT_FLIP') return { text: '事件平仓', cls: 'sell' };
    if (code === 'DEFAULT_NO_STAGE') return { text: '默认平仓', cls: 'sell' };
    if (code === 'LOCAL_STOP_LOSS') return { text: '止损平仓', cls: 'sell' };
    if (code === 'LOCAL_TAKE_PROFIT') return { text: '止盈平仓', cls: 'sell' };
    return { text: '平仓', cls: 'sell' };
  }
  return FLEX_ACTION_BADGE[action] || { text: flexShortAction(action, item?.action_cn), cls: 'wait' };
}

/** Shanghai calendar YYYY-MM-DD. */
function flexDateCn(offsetDays = 0) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(Date.now() + offsetDays * 86400000));
  const y = parts.find(p => p.type === 'year')?.value;
  const m = parts.find(p => p.type === 'month')?.value;
  const d = parts.find(p => p.type === 'day')?.value;
  return `${y}-${m}-${d}`;
}

/**
 * Current A-share session date for the desk:
 * last trade_date on or before Shanghai calendar today
 * (weekend/holiday → previous session — never count non-trading natural days).
 */
function flexSessionTradeDate() {
  const calToday = flexDateCn(0);
  const dates = flexEnsureTradeCalendar();
  if (!dates.length) return calToday;
  return dates[flexTradeDateIndexOnOrBefore(calToday, dates)] || calToday;
}

/**
 * Actionable window: current session + next trade session (T, T+1).
 * Trading calendar only.
 */
function flexActionableDateSet() {
  const t = flexSessionTradeDate();
  const t1 = flexAddTradingDays(t, 1);
  return new Set([t, t1].filter(Boolean));
}

function flexDateInActionWindow(dateStr, windowSet = flexActionableDateSet()) {
  if (!dateStr) return false;
  const day = String(dateStr).slice(0, 10);
  return windowSet.has(day);
}

/**
 * Trading-day lag from as_of → current session trade date.
 * 0 = as_of is the latest session (aligned), even on weekend/holiday.
 * Must NOT use natural-day difference (e.g. Fri→Sun is still lag 0).
 */
function flexBookLagDays(asOf) {
  const a = String(asOf || '').slice(0, 10);
  if (!a || !/^\d{4}-\d{2}-\d{2}$/.test(a)) return null;
  const session = flexSessionTradeDate();
  const lag = flexTradingDaysBetween(a, session);
  if (!Number.isFinite(lag)) return null;
  return Math.max(0, lag);
}

/**
 * Desk rule (personal execution — strict):
 * 1) T = real strategy signal day (entry_signal_date / day engine emits OPEN), NOT playbook as_of alone.
 * 2) Buy/confirm allowed only on T and T+1 trading sessions (lag 0..1 on trade calendar).
 * 3) From T+2 trading sessions the open signal is gone.
 * 4) Holding only after user clicks 买. Paper HOLD ≠ user hold.
 * 5) Hold clock uses trading days from confirm buy date.
 */
function flexBookIsToday(asOf) {
  const a = String(asOf || '').slice(0, 10);
  return !!a && a === flexSessionTradeDate();
}

/** True when signalDay is T or next trade day T+1 vs current session. lag≥2 trade days → false. */
function deskSignalWindowOpen(signalDay) {
  const lag = flexBookLagDays(signalDay);
  return lag != null && lag >= 0 && lag <= FLEX_OPEN_SIGNAL_MAX_LAG_DAYS;
}

function loadOpenSignalCache() {
  try {
    // Drop legacy bad caches (v1 seeded from as_of-1 / days_held).
    try { localStorage.removeItem('ashare_flex_open_signal_cache_v1'); } catch { /* ignore */ }
    const raw = localStorage.getItem(FLEX_OPEN_SIGNAL_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const day = String(parsed.as_of || '').slice(0, 10);
    if (!deskSignalWindowOpen(day)) {
      clearOpenSignalCache();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function saveOpenSignalCache(signalDay, items) {
  const day = String(signalDay || '').slice(0, 10);
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return;
  // Never cache opens outside the live T..T+1 window.
  if (!deskSignalWindowOpen(day)) return;
  const rows = (items || []).map(item => ({
    ...item,
    signal_as_of: day,
    action: item.action || 'OPEN',
  }));
  localStorage.setItem(FLEX_OPEN_SIGNAL_CACHE_KEY, JSON.stringify({
    as_of: day,
    items: rows,
    saved_at: new Date().toISOString(),
  }));
}

function clearOpenSignalCache() {
  try { localStorage.removeItem(FLEX_OPEN_SIGNAL_CACHE_KEY); } catch { /* ignore */ }
  try { localStorage.removeItem('ashare_flex_open_signal_cache_v1'); } catch { /* ignore */ }
}

/**
 * Resolve a strategy row to the actual local position it represents.
 *
 * Signal keys can change when an ETF mapping is corrected, while old browser
 * ledgers retain their original key. All rendering and mutations must use this
 * same resolved key; a boolean-only match can otherwise offer a duplicate buy.
 */
function flexFindLocalPosition(item, ledger = loadFlexLedger()) {
  if (!item) return null;
  const openPos = flexOpenPositions(ledger);
  if (!openPos.length) return null;
  const key = flexPositionKey(item);
  if (ledger.positions?.[key] && Number(ledger.positions[key].qty) > 1e-9) {
    return { key, position: ledger.positions[key] };
  }
  const code = String(item.etf_code || item.code || '').trim();
  const name = String(item.name || item.sector || '').trim();
  const position = openPos.find(p => {
    const pc = String(p.etf_code || '').trim();
    const pn = String(p.name || '').trim();
    if (code && pc && code === pc) return true;
    if (name && pn && name === pn) return true;
    return false;
  });
  if (!position) return null;
  return { key: position.key || flexPositionKey(position), position };
}

/** True when local ledger has an open position matching this signal row. */
function flexIsLocallyHeld(item, ledger = loadFlexLedger()) {
  return !!flexFindLocalPosition(item, ledger);
}

/**
 * Engine true OPEN rows only.
 * signal day T = playbook as_of on the day the engine still emits OPEN (not HOLD).
 */
function deskFreshOpenSignals(flex) {
  const f = flex || {};
  const openKeys = new Set(['OPEN', 'BUY', 'OVERWEIGHT', 'OVERWEIGHT_RELATIVE']);
  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);
  // If as_of itself is already past T+1, these opens are stale — ignore.
  if (!deskSignalWindowOpen(asOf)) return [];

  const byKey = new Map();
  const put = (item) => {
    if (!item) return;
    const action = String(item.action || item.side || '').toUpperCase();
    if (!openKeys.has(action)) return;
    const key = flexPositionKey(item);
    if (!key || byKey.has(key)) return;
    byKey.set(key, {
      ...item,
      action: action === 'BUY' ? 'OPEN' : item.action || 'OPEN',
      signal_as_of: asOf,
    });
  };
  for (const item of f.buy_list || []) put(item);
  for (const item of f.minimal_actions || []) put(item);
  return [...byKey.values()];
}

/**
 * T+1 recovery when buy_list is already empty: use REAL entry_signal_date only.
 * Never invent signal day from days_held or as_of-1 (that wrongly extended 07-13 to 07-15).
 */
function deskRecoverOpensFromSignalDate(flex) {
  const f = flex || {};
  const pos = f.position_state || {};
  const rows = [];
  const seen = new Set();

  const pushRow = (item, signalDay) => {
    if (!item || !signalDay || !deskSignalWindowOpen(signalDay)) return;
    const key = flexPositionKey(item);
    if (!key || seen.has(key)) return;
    seen.add(key);
    rows.push({
      ...item,
      action: 'OPEN',
      action_cn: '新开确认（T～T+1）',
      side: 'OPEN',
      side_cn: '买入',
      entry: '可确认买入',
      signal_as_of: signalDay,
      why: item.why || `策略信号日 ${signalDay}：T 与 T+1 可点买确认`,
      _deskRecovered: true,
    });
  };

  // Satellite: T = entry_signal_date from state machine (authoritative).
  const satState = pos.satellite || f.satellite?.position || {};
  const satSignal = String(satState.entry_signal_date || '').slice(0, 10);
  if (satSignal && deskSignalWindowOpen(satSignal)) {
    const names = satState.names || [];
    const weights = satState.weights || {};
    const holdByName = new Map((f.hold_list || [])
      .filter(h => String(h.sleeve || '') === 'satellite')
      .map(h => [String(h.name || ''), h]));
    const buyByName = new Map((f.satellite?.buy || []).map(b => [String(b.name || ''), b]));
    for (const name of names) {
      const base = holdByName.get(name) || buyByName.get(name) || { name, sleeve: 'satellite' };
      const w = weights[name];
      pushRow({
        ...base,
        sleeve: 'satellite',
        name,
        weight_target: base.weight_target != null ? base.weight_target : w,
        weight_hint: base.weight_hint || (w != null ? `${Math.round(Number(w) * 100)}%` : '—'),
      }, satSignal);
    }
  }

  // Core: only if still true OPEN, or entry_signal_date still inside T..T+1.
  const core = f.core || {};
  const coreState = pos.core || core.position || {};
  const coreSignal = String(coreState.entry_signal_date || '').slice(0, 10);
  const coreIsOpen = String(core.action || '').toUpperCase() === 'OPEN';
  if (coreIsOpen && deskSignalWindowOpen(String(f.as_of || '').slice(0, 10))) {
    pushRow({
      sleeve: 'core',
      name: '沪深300',
      etf_code: core.etf_code || '510300',
      etf_name: core.etf_name,
      weight_target: core.weight_target,
      weight_hint: core.weight_hint,
      why: core.rule || core.detail,
    }, String(f.as_of || '').slice(0, 10));
  } else if (coreSignal && deskSignalWindowOpen(coreSignal)) {
    pushRow({
      sleeve: 'core',
      name: '沪深300',
      etf_code: coreState.etf_code || core.etf_code || '510300',
      etf_name: core.etf_name,
      why: core.rule || core.detail,
    }, coreSignal);
  }

  return rows;
}

/**
 * Collect desk OPEN rows strictly inside T..T+1 of the real signal day.
 */
function deskCollectOpenSignals(flex) {
  const f = flex || {};
  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);

  // 1) Live engine OPEN on as_of (as_of is T that day).
  const fresh = deskFreshOpenSignals(f);
  if (fresh.length) {
    saveOpenSignalCache(asOf, fresh);
    return fresh;
  }

  // 2) Browser cache only if its stored signal day is still T or T+1.
  const cache = loadOpenSignalCache();
  if (cache?.as_of && deskSignalWindowOpen(cache.as_of) && Array.isArray(cache.items) && cache.items.length) {
    return cache.items.map(item => ({
      ...item,
      signal_as_of: cache.as_of,
      action: item.action || 'OPEN',
    }));
  }

  // 3) Recover using authoritative entry_signal_date only (no days_held invention).
  const recovered = deskRecoverOpensFromSignalDate(f);
  if (recovered.length) {
    const sig = recovered[0].signal_as_of;
    saveOpenSignalCache(sig, recovered);
    return recovered;
  }

  clearOpenSignalCache();
  return [];
}

/** Personal positions whose hold window has ended (user's buy_date clock). */
function deskLocalDueCloses(ledger = loadFlexLedger()) {
  const today = flexDateCn(0);
  const rows = [];
  for (const pos of flexOpenPositions(ledger)) {
    const info = flexPositionExitInfo(pos);
    const daysHeld = info.daysHeld != null ? info.daysHeld : flexPositionDaysHeld(pos, today);
    const holdDays = pos.hold_days != null ? Number(pos.hold_days) : null;
    // Same rule as engine: days_held >= hold_days → CLOSE signal (execute next trade open).
    const due = (holdDays != null && daysHeld >= holdDays)
      || (info.left != null && info.left <= 0);
    if (!due) continue;
    rows.push({
      action: 'CLOSE',
      action_cn: '持有期满卖出',
      side: 'CLOSE',
      side_cn: '卖出',
      sleeve: pos.sleeve || '',
      name: pos.name || '—',
      etf_code: pos.etf_code || '',
      etf_name: pos.etf_name || '',
      priority: 'P0',
      entry: '下一交易日开盘',
      exit: '平仓',
      why: `本机买入日起持有期满（买 ${pos.buy_date || '—'} · 计划 ${holdDays ?? '—'} 个交易日 · 已持有 ${daysHeld} 交易日）`,
      days_held: daysHeld,
      close_code: 'LOCAL_MAX_HOLD',
      guaranteed: true,
      weight_target: 0,
      weight_hint: '0%',
      _key: pos.key || flexPositionKey(pos),
      _deskLocalDue: true,
    });
  }
  return rows;
}

/** Satellite stop-loss / take-profit closes from local EOD marks. */
function deskLocalRiskCloses(flex, ledger = loadFlexLedger()) {
  const marked = flexApplyEodMarksToLedger(ledger);
  const rows = [];
  for (const pos of flexOpenPositions(marked)) {
    const st = flexSatelliteRiskStatus(pos, flex);
    if (!st?.triggered) continue;
    rows.push({
      action: 'CLOSE',
      action_cn: st.action_cn,
      side: 'CLOSE',
      side_cn: '卖出',
      sleeve: pos.sleeve || 'satellite',
      name: pos.name || '—',
      etf_code: pos.etf_code || '',
      etf_name: pos.etf_name || '',
      priority: 'P0',
      entry: '下一交易日开盘',
      exit: '平仓',
      why: st.why,
      close_code: st.close_code,
      guaranteed: true,
      weight_target: 0,
      weight_hint: '0%',
      return_pct: st.ret,
      _key: pos.key || flexPositionKey(pos),
      _deskLocalRisk: true,
    });
  }
  return rows;
}

/** Split engine lists into desk buckets — personal book only. */
function splitFlexSignalBuckets(flex) {
  const f = flex || {};
  const closeKeys = new Set(['CLOSE', 'SELL']);
  const avoidKeys = new Set(['AVOID', 'UNDERWEIGHT_RELATIVE']);

  const buckets = { open: [], hold: [], close: [], avoid: [] };
  const seen = { open: new Set(), hold: new Set(), close: new Set(), avoid: new Set() };
  const ledger = loadFlexLedger();

  const pushUnique = (kind, item) => {
    const key = item._key || flexPositionKey(item);
    if (seen[kind].has(key)) return;
    seen[kind].add(key);
    buckets[kind].push({ ...item, _key: key });
  };

  // OPEN: only real signal day T..T+1 (entry_signal_date / engine OPEN), never paper multi-day HOLD.
  for (const item of deskCollectOpenSignals(f)) {
    const sig = String(item.signal_as_of || '').slice(0, 10);
    if (sig && !deskSignalWindowOpen(sig)) continue;
    if (flexIsLocallyHeld(item, ledger)) continue;
    pushUnique('open', item);
  }

  // Local satellite risk exits have the clearest user-facing reason; show before paper exits.
  for (const item of deskLocalRiskCloses(f, ledger)) pushUnique('close', item);

  // CLOSE: ALWAYS surface engine close_list when as_of is today (guaranteed tip path).
  // - You hold it → actionable 平
  // - You don't → still show as 策略平仓 (paper book), never silent-drop
  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);
  const bookIsLive = flexBookIsToday(asOf) || flexBookLagDays(asOf) === 0;
  if (bookIsLive || deskSignalWindowOpen(asOf)) {
    for (const item of [...(f.close_list || []), ...(f.sell_list || []), ...(f.minimal_actions || [])]) {
      const action = String(item.action || item.side || '').toUpperCase();
      if (!closeKeys.has(action)) continue;
      const held = flexIsLocallyHeld(item, ledger);
      pushUnique('close', {
        ...item,
        _strategyPaper: !held,
        _deskForceShow: true,
        action_cn: held
          ? (item.action_cn || '平仓')
          : `策略纸面·${item.action_cn || item.close_code || '平仓'}`,
        why: item.why || item.close_code || '策略退出',
      });
    }
  }
  // Personal hold-days expired (from the day user clicked 买) — always.
  for (const item of deskLocalDueCloses(ledger)) pushUnique('close', item);

  // AVOID: only tip names the user actually holds (real: manual cut; sim: auto-zero on rebuild).
  for (const item of f.avoid_list || []) {
    const action = String(item.action || item.side || '').toUpperCase();
    if (!avoidKeys.has(action) && action !== 'FLAT') continue;
    if (!flexIsLocallyHeld(item, ledger)) continue;
    pushUnique('avoid', {
      ...item,
      _simAuto: isFlexSimBook(),
      action_cn: isFlexSimBook()
        ? '回避·模拟自动归零'
        : (item.action_cn || '回避/条件减配'),
    });
  }

  buckets.hold = []; // never list paper HOLD; real holds live under 持仓 tab

  for (const kind of Object.keys(buckets)) {
    buckets[kind].sort((a, b) =>
      String(a.etf_code || a.name || '').localeCompare(String(b.etf_code || b.name || ''), 'zh')
    );
  }

  return buckets;
}

function renderFlexSignalRows(items, flex, options = {}) {
  const ledger = loadFlexLedger();
  const capital = Number(ledger.capital) || 0;
  const signalAsOf = options.signalAsOf || '';
  const forceKind = options.forceKind || null;

  return items.map(item => {
    const action = String(item.action || item.side || '').toUpperCase();
    const signalKey = item._key || flexPositionKey(item);
    const localMatch = flexFindLocalPosition(item, ledger);
    const held = !!localMatch;
    const key = localMatch?.key || signalKey;
    const isHoldRow = forceKind === 'hold' || action === 'HOLD';
    const badgeInfo = isHoldRow
      ? flexActionBadge({ ...item, action: 'HOLD' }, flex, { localHeld: held, signalAsOf })
      : flexActionBadge(item, flex, {
        localHeld: held,
        signalAsOf: item.signal_as_of || signalAsOf,
      });
    const suggested = flexSuggestedAmount(item, capital);
    const etfCode = item.etf_code || '';
    const name = item.name || '—';
    const w = item.weight_hint || (item.weight_target != null ? pctLabel(item.weight_target) : '—');
    const amt = suggested != null ? formatMoney(suggested) : '—';
    // 清仓列：本机持仓用个人 exit 计划；策略平仓提示用「下一交易日开盘」
    const localPos = localMatch?.position || null;
    let left = '—';
    if (held) left = flexPositionExitInfo(localPos).label;
    else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
      left = item.entry && item.entry !== '—' ? String(item.entry) : '下一交易日开盘';
    } else if (String(item.sleeve || '').toLowerCase() === 'satellite' && (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action))) {
      left = item.risk_rule_cn || item.exit || flexSatelliteRiskRule(flex).ruleCn;
    }
    const isAvoid = forceKind === 'avoid' || action === 'AVOID' || action === 'UNDERWEIGHT_RELATIVE' || action === 'FLAT';
    // Avoid rows only appear when held; strategy CLOSE always listed (tip if not held).
    const interactive = !isAvoid || held;

    // Buy plan starts when user confirms (today's bookkeeping); full default hold window.
    const planDays = item.hold_days != null
      ? Number(item.hold_days)
      : (String(item.sleeve || '').toLowerCase() === 'satellite' ? 8 : (options.defaultHoldDays != null ? Number(options.defaultHoldDays) : null));

    let acts = '';
    if (interactive) {
      if (isFlexSimBook()) {
        // Sim book is fully automatic: open / size / avoid-cut / close via rebuildSimLedgerFromStrategy.
        if (isAvoid) {
          acts = held
            ? '<span class="flex-chip ghost" title="模拟仓：回避名单自动归零">自动回避</span>'
            : '<span class="flex-row-muted">—</span>';
        } else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
          acts = held
            ? '<span class="flex-chip ghost" title="模拟仓由策略纸面自动平仓">自动平</span>'
            : '<span class="flex-row-muted" title="策略提示">提示</span>';
        } else if (held) {
          acts = '<span class="flex-chip ghost" title="模拟仓自动跟随策略权重">已同步</span>';
        } else if (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action)) {
          acts = '<span class="flex-chip ghost" title="模拟仓在策略 open 时自动铺仓">自动开</span>';
        }
      } else if (isAvoid) {
        // Real book: only listed when user holds it — tip + act
        acts = held
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`
          : '<span class="flex-row-muted">—</span>';
      } else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
        acts = held
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`
          : '<span class="flex-row-muted" title="未点买，仅策略提示">仅提示</span>';
      } else if (held) {
        acts = `<button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(key)}">加</button>
          <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
          <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`;
      } else if (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action)) {
        acts = `<button type="button" class="flex-chip primary"
          data-flex-act="buy"
          data-pos-key="${escapeHtml(key)}"
          data-name="${escapeHtml(item.name || '')}"
          data-etf-code="${escapeHtml(etfCode)}"
          data-etf-name="${escapeHtml(item.etf_name || '')}"
          data-sleeve="${escapeHtml(item.sleeve || '')}"
          data-suggested="${suggested != null ? suggested : ''}"
          data-signal-as-of="${escapeHtml(signalAsOf)}"
          data-hold-days="${planDays != null ? planDays : ''}"
        >记买入</button>`;
      }
    }

    const whyTip = item.why ? ` title="${escapeHtml(String(item.why))}"` : '';
    return `<div class="flex-row ${badgeInfo.cls}${held ? ' is-held' : ''}"${whyTip}>
      <span class="badge badge-wide" data-label="动作">${escapeHtml(badgeInfo.text)}</span>
      <span class="flex-row-code" data-label="代码">${escapeHtml(etfCode || '—')}</span>
      <span class="flex-row-name" data-label="名称">${escapeHtml(name)}</span>
      <span class="flex-row-num" data-label="权重">${escapeHtml(String(w))}</span>
      <span class="flex-row-num" data-label="建议">${amt}</span>
      <span class="flex-row-num flex-row-muted" data-label="说明">${escapeHtml(left)}</span>
      <span class="flex-row-acts" data-label="操作">${acts}</span>
    </div>`;
  }).join('');
}

function renderFlexSignalList(flex, options = {}) {
  const buckets = splitFlexSignalBuckets(flex || {});
  const defaultHoldDays = flex?.hold_days != null ? Number(flex.hold_days) : 5;
  // hold bucket intentionally omitted (paper HOLD is not a desk action)
  const map = [
    { kind: 'open', id: 'flexOpenList', forceKind: 'open', countId: 'flexOpenCount' },
    { kind: 'close', id: 'flexCloseList', forceKind: 'close', countId: 'flexCloseCount' },
    { kind: 'avoid', id: 'flexAvoidList', forceKind: 'avoid', countId: 'flexAvoidCount' },
  ];

  // Dynamic open title: T vs T+1
  const asOfForTitle = String(options.signalAsOf || flex?.as_of || '').slice(0, 10);
  const lagOpen = flexBookLagDays(asOfForTitle);
  const openTitle = document.getElementById('flexOpenTitle');
  if (openTitle) {
    const countSpan = document.getElementById('flexOpenCount');
    const countHtml = countSpan ? countSpan.outerHTML : '';
    openTitle.innerHTML = lagOpen === 1
      ? `T+1 可确认 ${countHtml}`
      : `可买信号 ${countHtml}`;
  }
  const openHint = document.getElementById('flexOpenHint');
  if (openHint) {
    openHint.innerHTML = isFlexSimBook()
      ? '模拟仓：新开由策略纸面自动同步，无需点买。下列为窗口内策略新开提示。'
      : '真实仓：信号日 <strong>T</strong> 与 <strong>T+1</strong> 可点「记买入」；未点则 <strong>T+2</strong> 消失。';
  }

  let any = false;
  let actionable = 0;
  for (const { kind, id, forceKind, countId } of map) {
    const block = document.querySelector(`[data-signal-kind="${kind}"]`);
    const el = document.getElementById(id);
    const items = buckets[kind] || [];
    const countEl = document.getElementById(countId);
    if (countEl) countEl.textContent = items.length ? `(${items.length})` : '';
    if (block) block.hidden = items.length === 0;
    if (kind !== 'avoid') actionable += items.length;
    if (!el) continue;
    if (!items.length) {
      el.innerHTML = '';
      continue;
    }
    any = true;
    el.innerHTML = renderFlexSignalRows(items, flex, {
      ...options,
      forceKind,
      defaultHoldDays,
    });
  }

  const empty = document.getElementById('flexSignalEmpty');
  if (empty) {
    empty.hidden = any;
    if (!any) {
      const title = document.getElementById('flexSignalEmptyTitle');
      const body = document.getElementById('flexSignalEmptyBody');
      const asOf = String(flex?.as_of || '').slice(0, 10);
      const today = flexDateCn(0);
      const lag = flexBookLagDays(asOf);
      if (title && body) {
        if (isFlexSimBook()) {
          title.textContent = '模拟仓：暂无新开/平仓动作';
          body.textContent = asOf
            ? `策略 as_of=${asOf}。若袖标已 open，请到「持仓」查看自动同步仓位；日更约 15:40 后刷新。`
            : '设置全仓后，模拟仓将按策略纸面自动铺仓。';
        } else if (lag != null && lag > FLEX_OPEN_SIGNAL_MAX_LAG_DAYS) {
          title.textContent = '买入窗口已过';
          body.textContent = `策略 as_of=${asOf || '—'}，当前交易日=${flexSessionTradeDate()}（差 ${lag} 个交易日）。仅 T～T+1 可买。`;
        } else {
          title.textContent = '今日无行动信号';
          body.textContent = asOf
            ? `策略 as_of=${asOf}：当前没有可买/须平提示。点买才进真实仓；T+2 起未确认信号消失。`
            : '等待策略日更，或检查网络后刷新页面。';
        }
      }
    }
  }

  const capital = Number(loadFlexLedger().capital) || 0;
  const capitalHint = document.getElementById('flexCapitalHint');
  if (capitalHint) {
    capitalHint.hidden = !(capital <= 0 && any);
  }

  setFlexTabBadge('flexTabBadgeSignal', actionable);
}

function bindFlexTabs() {
  const panel = document.getElementById('flexTradePanel');
  if (!panel || panel.dataset.tabsBound === '1') return;
  panel.dataset.tabsBound = '1';
  panel.querySelectorAll('.flex-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const id = tab.dataset.flexTab;
      panel.querySelectorAll('.flex-tab').forEach(t => {
        const on = t === tab;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      panel.querySelectorAll('.flex-tab-panel').forEach(p => {
        p.classList.toggle('active', p.dataset.flexPanel === id);
      });
    });
  });
}

function bindFlexExecControls() {
  if (dashboardState.flexLedgerBound) return;
  dashboardState.flexLedgerBound = true;
  bindFlexTabs();

  document.getElementById('flexCapitalSaveBtn')?.addEventListener('click', () => {
    const input = document.getElementById('flexCapitalInput');
    const capital = Number(input?.value);
    if (!(capital > 0)) {
      flexToast('请输入大于 0 的全仓金额', 'err', 2600);
      input?.focus();
      return;
    }
    const ledger = loadFlexLedger();
    const prev = Number(ledger.capital) || 0;
    const next = Math.round(capital * 100) / 100;
    const delta = next - prev;
    const cash = flexAvailableCash(ledger);
    // A capital withdrawal can only come from cash. Silently zeroing cash would
    // make the book's funding base disagree with its recorded positions.
    if (delta < 0 && -delta > cash + 1e-6) {
      flexToast(`下调全仓需先减仓或平仓；当前可用现金约 ${formatMoney(cash)} 元`, 'err', 3600);
      input?.focus();
      return;
    }
    // Funding changes affect spendable cash one-for-one after the guard above.
    ledger.cash = cash + delta;
    ledger.capital = next;
    if (prev !== next) {
      appendFlexJournal(ledger, {
        type: 'CAPITAL',
        type_cn: '调整全仓',
        name: '账户',
        amount: next,
        price: 0,
        qty: 0,
        note: `从 ${formatMoney(prev)} 调整为 ${formatMoney(next)}（现金同步 ${delta >= 0 ? '+' : ''}${formatMoney(delta)}）`,
      });
    }
    saveFlexLedger(ledger);
    flexToast(`${isFlexSimBook() ? '模拟' : '真实'}仓全仓已保存：${formatMoney(next)} 元`, 'ok');
    if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
    else renderFlexExecUi();
  });

  document.getElementById('flexCapitalInput')?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      document.getElementById('flexCapitalSaveBtn')?.click();
    }
  });

  document.getElementById('flexResetLedgerBtn')?.addEventListener('click', () => {
    const bookName = isFlexSimBook() ? '模拟仓' : '真实仓';
    if (!confirm(`确认清空【${bookName}】持仓与流水？\n全仓金额保留，现金重置为全仓。\n（另一账本不受影响）`)) return;
    const ledger = loadFlexLedger();
    const capital = Number(ledger.capital) || 0;
    const next = defaultFlexLedger();
    next.capital = capital;
    next.cash = capital;
    saveFlexLedger(next);
    flexToast(`${bookName}已清空（本金 ${formatMoney(capital)} 保留）`, 'warn');
    if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
    else renderFlexExecUi();
  });

  document.getElementById('flexExportLedgerBtn')?.addEventListener('click', () => {
    const ledger = loadFlexLedger();
    const blob = new Blob([JSON.stringify(ledger, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const tag = isFlexSimBook() ? 'sim' : 'real';
    a.download = `flex-ledger-${tag}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    flexToast(`已导出${isFlexSimBook() ? '模拟' : '真实'}账本`, 'ok', 1500);
  });

  document.getElementById('flexImportLedgerBtn')?.addEventListener('click', () => {
    document.getElementById('flexImportLedgerFile')?.click();
  });
  document.getElementById('flexImportLedgerFile')?.addEventListener('change', async (ev) => {
    const file = ev.target?.files?.[0];
    ev.target.value = '';
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object') throw new Error('无效账本文件');
      const ledger = normalizeFlexLedger(parsed);
      const bookName = isFlexSimBook() ? '模拟仓' : '真实仓';
      if (!confirm(`导入到【${bookName}】？将覆盖该账本持仓与流水。\n全仓 ${formatMoney(ledger.capital)} · 持仓 ${flexOpenPositions(ledger).length} · 流水 ${(ledger.journal || []).length}`)) {
        return;
      }
      saveFlexLedger(ledger);
      flexToast(`已导入到${bookName}`, 'ok');
      if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
      else renderFlexExecUi();
    } catch (e) {
      flexToast(e.message || '导入失败', 'err', 3200);
    }
  });

  document.getElementById('flexModalCloseBtn')?.addEventListener('click', closeFlexTradeModal);
  document.getElementById('flexModalCancelBtn')?.addEventListener('click', closeFlexTradeModal);
  document.getElementById('flexModalConfirmBtn')?.addEventListener('click', confirmFlexTradeModal);
  document.getElementById('flexTradeModal')?.addEventListener('click', (ev) => {
    if (ev.target?.id === 'flexTradeModal') closeFlexTradeModal();
  });
  ['flexModalAmount', 'flexModalPrice', 'flexModalPct'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', updateFlexModalPreview);
    document.getElementById(id)?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        confirmFlexTradeModal();
      }
    });
  });
  document.getElementById('flexModalAmountChips')?.addEventListener('click', (ev) => {
    const chip = ev.target.closest('[data-flex-amt-chip]');
    if (!chip) return;
    const state = dashboardState.flexModal;
    if (!state || (state.mode !== 'buy' && state.mode !== 'add')) return;
    const amountEl = document.getElementById('flexModalAmount');
    if (!amountEl) return;
    const ledger = loadFlexLedger();
    const cash = flexAvailableCash(ledger);
    const kind = chip.dataset.flexAmtChip;
    let next = null;
    if (kind === 'suggest' && state.defaultAmount != null) next = Number(state.defaultAmount);
    else if (kind === '25') next = Math.floor(cash * 0.25);
    else if (kind === '50') next = Math.floor(cash * 0.5);
    else if (kind === '100') next = Math.floor(cash);
    if (next != null && next > 0) {
      amountEl.value = String(next);
      updateFlexModalPreview();
    }
  });
  document.addEventListener('keydown', (ev) => {
    const modal = document.getElementById('flexTradeModal');
    if (ev.key === 'Escape' && modal && !modal.hidden) {
      ev.preventDefault();
      closeFlexTradeModal();
    }
  });

  document.getElementById('flexTradePanel')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-flex-act]');
    if (!btn) return;
    const act = btn.dataset.flexAct;
    const key = btn.dataset.posKey;
    const ledger = loadFlexLedger();

    if (act === 'buy') {
      const suggested = btn.dataset.suggested ? Number(btn.dataset.suggested) : null;
      const isAdd = ledger.positions[key] && Number(ledger.positions[key].qty) > 0;
      const codeName = `${btn.dataset.etfCode || ''} ${btn.dataset.name || ''}`.trim();
      openFlexTradeModal({
        mode: isAdd ? 'add' : 'buy',
        title: isAdd ? '加仓记账' : '买入记账',
        subtitle: `${codeName}${suggested ? ` · 建议 ${formatMoney(suggested)}` : ''} · 请填实际成交价`,
        key,
        name: btn.dataset.name || '',
        etf_code: btn.dataset.etfCode || '',
        etf_name: btn.dataset.etfName || '',
        sleeve: btn.dataset.sleeve || '',
        signal_as_of: btn.dataset.signalAsOf || '',
        hold_days: btn.dataset.holdDays !== '' && btn.dataset.holdDays != null
          ? Number(btn.dataset.holdDays)
          : null,
        defaultAmount: suggested,
        defaultPrice: ledger.positions[key]?.last_price || ledger.positions[key]?.avg_price || null,
      });
      return;
    }

    if (act === 'add' || act === 'reduce' || act === 'close') {
      const pos = ledger.positions[key];
      if (!pos || !(Number(pos.qty) > 0)) return;
      const sub = `${pos.etf_code || pos.name || ''} · 成本 ${formatMoney(pos.cost_basis)}`;
      if (act === 'add') {
        openFlexTradeModal({
          mode: 'add',
          title: '加仓记账',
          subtitle: sub,
          key,
          name: pos.name,
          etf_code: pos.etf_code,
          etf_name: pos.etf_name,
          sleeve: pos.sleeve,
          defaultAmount: null,
          defaultPrice: pos.last_price || pos.avg_price,
        });
      } else if (act === 'reduce') {
        openFlexTradeModal({
          mode: 'reduce',
          title: '减仓记账',
          subtitle: sub,
          key,
          defaultPrice: pos.last_price || pos.avg_price,
        });
      } else {
        openFlexTradeModal({
          mode: 'close',
          title: '平仓记账',
          subtitle: sub,
          key,
          defaultPrice: pos.last_price || pos.avg_price,
        });
      }
    }
  });
}

function flexModeStats(flex, mode) {
  const bt = flex.backtest || {};
  const block = (bt[mode] || bt.aggressive || {});
  const full = block.full_sample || bt.full_sample || {};
  const oos = block.oos || bt.oos || {};
  const core = bt.core_only || {};
  return { full, oos, core, stress: bt.cost_stress || {} };
}

function applyFlexModeOverlay(flex, mode) {
  // Client-side re-label of weights for aggressive vs conservative without rebuild
  if (!flex || !flex.allocation) return flex;
  const modes = flex.modes || {};
  const cfg = modes[mode] || modes.aggressive;
  if (!cfg) return flex;
  const copy = { ...flex, mode };
  // Allocation strip: open signals in T..T+1 window, paper open sleeves, or personal holdings.
  const freshOpen = deskCollectOpenSignals(flex);
  const hasCoreOpen = freshOpen.some(x => String(x.sleeve || '') === 'core'
    || String(x.name || '').includes('沪深300')
    || String(x.etf_code || '') === '510300');
  const hasSatOpen = freshOpen.some(x => String(x.sleeve || '') === 'satellite'
    || (String(x.name || '') && !String(x.name || '').includes('沪深300') && String(x.etf_code || '') !== '510300'));
  const ledgerForAlloc = loadFlexLedger();
  const coreHeld = flexIsLocallyHeld({
    sleeve: 'core',
    name: '沪深300',
    etf_code: (flex.core && flex.core.etf_code) || '510300',
  }, ledgerForAlloc);
  const satHeld = flexOpenPositions(ledgerForAlloc).some(p => String(p.sleeve || '') === 'satellite');
  // Critical for sim: paper position_state "open" must drive sleeve weights even when
  // action lists show HOLD (not OPEN) and local ledger is empty before rebuild.
  const corePaperOpen = String(flex.position_state?.core?.status || '') === 'open';
  const satPaperOpen = String(flex.position_state?.satellite?.status || '') === 'open';
  const coreOn = hasCoreOpen || coreHeld || corePaperOpen
    || !!(flex.core && flex.core.active && String(flex.core.action || '').toUpperCase() === 'OPEN');
  const satOn = hasSatOpen || satHeld || satPaperOpen
    || !!(flex.satellite && flex.satellite.active && String(flex.satellite.action || flex.satellite.status_cn || '').includes('新开'));
  let wCore = coreOn ? Number(cfg.core_when_signal || 0.5) : 0;
  let wSat = satOn ? Number(cfg.sat_when_signal || 0.3) : 0;
  if (cfg.flex_single_full) {
    if (coreOn && !satOn) { wCore = 1; wSat = 0; }
    else if (satOn && !coreOn) { wCore = 0; wSat = 1; }
  }
  let total = wCore + wSat;
  const cap = Number(cfg.total_cap || 1);
  if (total > cap && total > 0) {
    wCore *= cap / total;
    wSat *= cap / total;
    total = cap;
  }
  if (flex.satellite && flex.satellite.observe_only && wSat > 0) {
    wSat *= 0.25;
    total = wCore + wSat;
  }
  const allocCn = coreOn && satOn
    ? `双仓：核心 ${(wCore * 100).toFixed(0)}% + 卫星 ${(wSat * 100).toFixed(0)}%（${cfg.label_cn || mode}）`
    : coreOn
      ? `仅核心：${(wCore * 100).toFixed(0)}%（${cfg.label_cn || mode}）`
      : satOn
        ? `仅卫星：${(wSat * 100).toFixed(0)}%（${cfg.label_cn || mode}）`
        : (flex.allocation_cn || '空仓观望');
  copy.allocation_cn = allocCn;
  copy.allocation = {
    ...(flex.allocation || {}),
    mode,
    w_core: Math.round(wCore * 10000) / 10000,
    w_sat: Math.round(wSat * 10000) / 10000,
    w_cash: Math.round((1 - wCore - wSat) * 10000) / 10000,
    total_exposure: Math.round((wCore + wSat) * 10000) / 10000,
    allocation_cn: allocCn,
  };
  return copy;
}

function renderFlexTradePanel(playbook) {
  const panel = document.getElementById('flexTradePanel');
  if (!panel) return;
  let flex = playbook?.flex_panel;
  if (!flex || playbook?.status === 'missing') {
    setText('flexStatus', '—');
    setText('flexCoreAction', '—');
    setText('flexSatStage', '—');
    setText('flexCoreWeight', '—');
    setText('flexSatWeight', '—');
    setText('flexAllocShort', '—');
    setText('flexStatsShort', '—');
    setText('flexExposure', '—');
    setText('flexBeta', '—');
    setText('flexHold', '—');
    setText('flexModeHint', '—');
    renderFlexSignalList({}, {});
    dashboardState.flexActive = null;
    renderFlexExecUi();
    return;
  }

  dashboardState.flexPlaybook = playbook;
  // Product lock: only aggressive Flex sizing is exposed in the app; backend is the sizing source of truth.
  const mode = flex.mode || 'aggressive';
  dashboardState.flexMode = 'aggressive';
  if (flex.mode !== 'aggressive') {
    flex = applyFlexModeOverlay(flex, mode);
  }
  // Promote root playbook metadata onto flex for signal filters / empty-state copy.
  flex = {
    ...flex,
    as_of: flex.as_of || playbook?.as_of || '',
    data_quality: playbook?.data_quality || flex.data_quality || null,
    mode,
  };
  dashboardState.flexActive = flex;

  // Sim book: rebuild to strictly mirror strategy paper sleeves (never touches real ledger).
  if (isFlexSimBook()) {
    rebuildSimLedgerFromStrategy(flex);
  }
  paintFlexBookChrome();

  const stats = flexModeStats(flex, mode);
  const { full } = stats;
  const asOf = flex.as_of || playbook?.as_of || '';
  setText('flexStatus', flex.status || '—');
  const asOfEl = document.getElementById('flexAsOf');
  if (asOfEl) {
    asOfEl.hidden = !asOf;
    const lag = flexBookLagDays(asOf);
    const dq = flex.data_quality || {};
    let label = asOf ? asOf.slice(5) : '';
    if (dq.bridged) label = `${label}·桥`;
    else if (lag != null && lag > 0) label = `${label}·滞${lag}d`;
    asOfEl.textContent = label;
    const session = flexSessionTradeDate();
    asOfEl.title = [
      `策略书 as_of=${asOf}`,
      `当前交易日=${session}`,
      dq.official_as_of ? `正式 RT=${dq.official_as_of}` : '',
      dq.bridged ? `桥接日 ${(dq.bridged_dates || []).join(',')}` : '',
      lag != null && lag > 0 ? `落后 ${lag} 个交易日` : '与当前交易日对齐',
    ].filter(Boolean).join(' · ');
    // lag 1 = still T+1 trade window; only warn when past next trading session
    asOfEl.classList.toggle('warn', !!(lag != null && lag > FLEX_OPEN_SIGNAL_MAX_LAG_DAYS));
  }
  const satRule = flexSatelliteRiskRule(flex);
  setText('flexHold', `核心${flex.hold_days || 5}日 · 卫星${satRule.ruleCn}`);
  // Win rate + ann return (1bp baseline when present in stats)
  const win = full.win_rate;
  const ann = full.ann_return;
  if (win != null && Number.isFinite(Number(win)) && ann != null && Number.isFinite(Number(ann))) {
    setText('flexStatsShort', `${(Number(win) * 100).toFixed(0)}%·${(Number(ann) * 100).toFixed(0)}%`);
  } else if (win != null && Number.isFinite(Number(win))) {
    setText('flexStatsShort', pctLabel(win));
  } else {
    setText('flexStatsShort', '—');
  }

  const risk = flex.risk_dashboard || {};
  const alloc = flex.allocation || {};
  setText('flexBeta', risk.estimated_beta != null ? Number(risk.estimated_beta).toFixed(2) : '—');
  setText(
    'flexExposure',
    alloc.total_exposure != null
      ? pctLabel(alloc.total_exposure)
      : risk.total_exposure != null
        ? pctLabel(risk.total_exposure)
        : '—'
  );
  const wCore = alloc.w_core;
  const wSat = alloc.w_sat;
  setText(
    'flexAllocShort',
    wCore != null || wSat != null
      ? `${Math.round((Number(wCore) || 0) * 100)}/${Math.round((Number(wSat) || 0) * 100)}`
      : '—'
  );

  const core = flex.core || {};
  const sat = flex.satellite || {};
  const ledgerNow = loadFlexLedger();
  const openNow = deskCollectOpenSignals(flex);
  const coreOpenNow = openNow.some(x =>
    String(x.sleeve || '') === 'core'
    || String(x.name || '').includes('沪深300')
    || String(x.etf_code || '') === (core.etf_code || '510300'));
  const satOpenNow = openNow.some(x =>
    String(x.sleeve || '') === 'satellite'
    || (String(x.etf_code || '') && String(x.etf_code || '') !== (core.etf_code || '510300')
      && !String(x.name || '').includes('沪深300')));
  const coreHeld = flexIsLocallyHeld({
    sleeve: 'core',
    name: '沪深300',
    etf_code: core.etf_code || '510300',
  }, ledgerNow);
  const satHeld = flexOpenPositions(ledgerNow).some(p => String(p.sleeve || '') === 'satellite');
  const coreEl = document.getElementById('flexCoreSleeve');
  const satEl = document.getElementById('flexSatSleeve');
  if (coreEl) {
    coreEl.dataset.tone = coreHeld ? 'buy' : (coreOpenNow ? 'buy' : 'wait');
  }
  if (satEl) {
    satEl.dataset.tone = satHeld ? 'buy' : (satOpenNow ? 'buy' : 'wait');
  }
  // Sleeve cards: strategy status + book holdings
  const paperCoreOpen = String(flex.position_state?.core?.status || '') === 'open'
    || String(core.action || '').toUpperCase() === 'HOLD'
    || String(core.action || '').toUpperCase() === 'CLOSE';
  const paperSatOpen = String(flex.position_state?.satellite?.status || '') === 'open'
    || !!sat.active;
  let coreActionLabel = '观望';
  if (isFlexSimBook()) {
    if (coreHeld || paperCoreOpen) coreActionLabel = paperCoreOpen && String(core.action || '').toUpperCase() === 'CLOSE' ? '策略待平' : '策略持有';
    else if (coreOpenNow) coreActionLabel = '策略可开';
  } else {
    if (coreHeld) coreActionLabel = '已持有';
    else if (coreOpenNow) coreActionLabel = '可买·T～T+1';
  }
  setText('flexCoreAction', coreActionLabel);
  setText('flexCoreWeight', wCore != null ? pctLabel(wCore) : (core.etf_code || '—'));
  let satActionLabel = '空仓';
  if (isFlexSimBook()) {
    if (satHeld || paperSatOpen) satActionLabel = '策略持有';
    else if (satOpenNow) satActionLabel = '策略可开';
  } else {
    if (satHeld) satActionLabel = '已持有';
    else if (satOpenNow) satActionLabel = '可买·T～T+1';
  }
  setText('flexSatStage', satActionLabel);
  setText('flexSatWeight', wSat != null ? pctLabel(wSat) : '—');
  if (coreEl) {
    coreEl.title = [
      isFlexSimBook() ? (coreHeld ? '模拟已同步' : '模拟未持有') : (coreHeld ? '真实已点买' : '真实未点买'),
      coreOpenNow ? '窗口内有新开' : '窗口内无新开',
      core.etf_code,
    ].filter(Boolean).join(' · ');
  }
  if (satEl) {
    satEl.title = [
      isFlexSimBook() ? (satHeld ? '模拟已同步' : '模拟未持有') : (satHeld ? '真实已点买' : '真实未点买'),
      satOpenNow ? '窗口内有新开' : '窗口外无新开',
      sat.stage_cn,
    ].filter(Boolean).join(' · ');
  }

  // Always aggressive; UI only toggles real/sim book.
  dashboardState.flexMode = 'aggressive';
  const modeCfg = (flex.modes || {}).aggressive || (flex.modes || {})[mode] || {};
  const bookLabel = isFlexSimBook() ? '模拟' : '真实';
  setText('flexModeHint', bookLabel);
  const modeHintEl = document.getElementById('flexModeHint');
  if (modeHintEl) {
    modeHintEl.title = isFlexSimBook()
      ? `${modeCfg.label_cn || '进取'} · 模拟仓`
      : `${modeCfg.label_cn || '进取'} · 真实仓`;
  }

  const trust = document.getElementById('flexTrustLine');
  if (trust) {
    trust.hidden = true;
    trust.textContent = '';
  }

  renderFlexSignalList(flex, { signalAsOf: asOf });
  renderFlexExecUi();
}

function hideLoadError() {
  const banner = document.getElementById('loadErrorBanner');
  if (banner) banner.hidden = true;
}

function showLoadError(message) {
  let banner = document.getElementById('loadErrorBanner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'loadErrorBanner';
    banner.className = 'load-error-banner';
    banner.innerHTML = '<span></span><button type="button">重试</button>';
    banner.querySelector('button').addEventListener('click', () => {
      refreshDashboard({ forceFull: true });
    });
    const shell = document.querySelector('.page-shell');
    if (shell) shell.prepend(banner);
  }
  banner.querySelector('span').textContent = message || '数据加载失败，请稍后重试。';
  banner.hidden = false;
}

function renderRtTactical(payload) {
  const panel = document.getElementById('rtTacticalPanel');
  if (!panel) return;
  if (!payload || payload.status === 'missing' || !payload.latest) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const latest = payload.latest || {};
  setText('rtTacticalStatus', latest.status_cn || latest.status || '--');
  setText('rtTacticalRule', payload.rule_summary || '--');
  setText('rtTacticalNote', payload.disclaimer || '研究观察信号，不构成投资建议。');
  const detail = [
    `RT ${latest.risk_temperature ?? '--'}`,
    `60日回撤 ${formatSignedPct(latest.drawdown_60d)}`,
    latest.in_band ? '落在 60-75 研究带' : '未在研究带',
  ].join(' / ');
  setText('rtTacticalDetail', detail);
}

async function refreshDashboard({ forceFull = false } = {}) {
  if (dashboardState.refreshInFlight) return;
  dashboardState.refreshInFlight = true;
  try {
    if (!forceFull && dashboardState.lastUpdateTime) {
      const latest = await loadJSON('./data/latest.json');
      if (
        latest?.update_time === dashboardState.lastUpdateTime
        && latest?.trade_date === dashboardState.lastTradeDate
      ) {
        updateFreshness(latest);
        updateRefreshStatus('ok', '数据未变化，跳过全量刷新');
        return;
      }
      // New data: refresh cache bust and reload critical + heavy.
      dashboardState.cacheBust = latest?.update_time || String(Date.now());
    } else {
      await resolveCacheBust();
    }

    const critical = await loadCriticalDashboardData();
    renderCriticalDashboard(critical);
    const heavy = await loadHeavyDashboardData();
    renderHeavyDashboard(heavy);
    updateRefreshStatus('ok');
  } catch (err) {
    console.error(err);
    updateRefreshStatus('error', err.message || String(err));
    showLoadError(err.message || String(err));
  } finally {
    dashboardState.refreshInFlight = false;
  }
}

const APP_VIEW_KEY = 'ashare_app_view_v1';

function resizeVisibleCharts() {
  try {
    dashboardState.componentChart?.resize();
    (dashboardState.timeCharts || []).forEach(chart => chart?.resize?.());
    Object.values(dashboardState.chartInstances || {}).forEach(chart => chart?.resize?.());
  } catch (_) { /* ignore */ }
}

function setAppView(viewId, { persist = true } = {}) {
  const allowed = new Set(['temp', 'history', 'flex']);
  const view = allowed.has(viewId) ? viewId : 'temp';

  document.querySelectorAll('.app-view').forEach(el => {
    const on = el.dataset.view === view;
    el.classList.toggle('is-active', on);
    if (on) el.removeAttribute('hidden');
    else el.setAttribute('hidden', '');
  });

  document.querySelectorAll('#appDock [data-view], .app-dock-item[data-view]').forEach(btn => {
    const on = btn.getAttribute('data-view') === view;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });

  document.body.dataset.appView = view;
  if (persist) {
    try { localStorage.setItem(APP_VIEW_KEY, view); } catch (_) { /* ignore */ }
  }
  // Charts in hidden views need a resize when shown.
  requestAnimationFrame(() => {
    resizeVisibleCharts();
    setTimeout(resizeVisibleCharts, 60);
  });
  try {
    if (location.hash !== `#${view}`) {
      history.replaceState(null, '', `#${view}`);
    }
  } catch (_) { /* ignore */ }
}

function bindAppTabs() {
  document.querySelectorAll('#appDock [data-view], .app-dock-item[data-view]').forEach(btn => {
    if (btn.dataset.boundTabs === '1') return;
    btn.dataset.boundTabs = '1';
    btn.addEventListener('click', e => {
      e.preventDefault();
      setAppView(btn.getAttribute('data-view'));
    });
  });
  window.addEventListener('hashchange', () => {
    const h = (location.hash || '').replace(/^#/, '');
    if (h === 'temp' || h === 'history' || h === 'flex') setAppView(h, { persist: true });
  });
}

function initialAppView() {
  const hash = (location.hash || '').replace(/^#/, '');
  if (hash === 'temp' || hash === 'history' || hash === 'flex') return hash;
  try {
    const saved = localStorage.getItem(APP_VIEW_KEY);
    if (saved === 'temp' || saved === 'history' || saved === 'flex') return saved;
  } catch (_) { /* ignore */ }
  return 'temp';
}

function bindMagazineChrome() {
  bindAppTabs();
  setAppView(initialAppView(), { persist: false });
  // Detect Expo / standalone WebView for slight chrome tweaks
  try {
    const ua = navigator.userAgent || '';
    if (/Expo|ReactNative|wv\)/i.test(ua) || window.ReactNativeWebView) {
      document.body.classList.add('in-app-webview');
    }
  } catch (_) { /* ignore */ }
  bindDataPlaneControls();
}

async function fetchDataPlaneStatus() {
  try {
    const res = await fetch('./api/status', { cache: 'no-store' });
    if (!res.ok) throw new Error('status ' + res.status);
    const status = await res.json();
    dashboardState.dataPlane.available = true;
    dashboardState.dataPlane.status = status;
    renderDataPlaneBar(status);
    return status;
  } catch (_) {
    dashboardState.dataPlane.available = false;
    dashboardState.dataPlane.status = null;
    renderDataPlaneBar(null);
    return null;
  }
}

function setDataPlaneActionsVisible(on) {
  const actions = document.querySelector('#dataPlaneBar .data-plane-actions');
  if (actions) actions.hidden = !on;
}

function paintStaticPagesPlaneMeta(latest, { busy = false, note = null } = {}) {
  if (dashboardState.dataPlane.available) return;
  const sourceEl = document.getElementById('dataPlaneSource');
  const metaEl = document.getElementById('dataPlaneMeta');
  const bar = document.getElementById('dataPlaneBar');
  const patOk = hasGithubActionsPat();
  if (sourceEl) sourceEl.textContent = patOk ? 'Actions' : 'Pages';
  if (bar) {
    bar.dataset.state = busy ? 'stale' : (patOk ? 'fresh' : 'offline');
    bar.dataset.plane = 'actions';
  }
  // Keep 实时/日更 visible on pure Pages — they dispatch GitHub Actions when PAT is set.
  setDataPlaneActionsVisible(true);
  setDataPlaneButtonsEnabled(!busy && !dashboardState.dataPlane.refreshInFlight);
  if (!metaEl) return;
  if (note) {
    metaEl.textContent = note;
    return;
  }
  if (!latest) {
    metaEl.textContent = patOk
      ? 'GitHub Actions 自动更新'
      : '点「令牌」配置后可在 App 内触发更新';
    return;
  }
  metaEl.textContent = [
    latest.risk_temperature != null ? `RT ${latest.risk_temperature}` : null,
    latest.temperature_mode_cn || latest.temperature_mode || null,
    latest.trade_date || null,
    patOk ? null : '未配令牌',
  ].filter(Boolean).join(' · ');
}

function renderDataPlaneBar(status) {
  const bar = document.getElementById('dataPlaneBar');
  const sourceEl = document.getElementById('dataPlaneSource');
  const metaEl = document.getElementById('dataPlaneMeta');
  if (!bar || !sourceEl || !metaEl) return;

  // Pure GitHub Pages / static host: no /api — use Actions dispatch buttons.
  if (!status) {
    paintStaticPagesPlaneMeta(null);
    return;
  }

  setDataPlaneActionsVisible(true);
  bar.dataset.plane = 'api';

  const latest = status.latest || {};
  const fresh = status.freshness || {};
  const age = fresh.age_minutes;
  const ageLabel = age == null ? '' : (age < 60 ? `${age}m` : `${Math.round(age / 60)}h`);
  const mode = latest.temperature_mode_cn || latest.temperature_mode || '';
  const rt = latest.risk_temperature ?? '—';

  sourceEl.textContent = status.independent_of_github === false ? 'API' : '本机';
  metaEl.textContent = [rt !== '—' ? `RT ${rt}` : null, mode, ageLabel, status.refresh_running ? '…' : null]
    .filter(Boolean)
    .join(' · ');

  if (status.refresh_running) bar.dataset.state = 'stale';
  else if (fresh.stale) bar.dataset.state = 'stale';
  else if (status.last_error) bar.dataset.state = 'error';
  else bar.dataset.state = 'fresh';

  setDataPlaneButtonsEnabled(!status.refresh_running && !dashboardState.dataPlane.refreshInFlight);
}

function setDataPlaneButtonsEnabled(on) {
  // `on` = not busy / not in-flight. Per-button A-share schedule still applies.
  applyDataPlaneButtonSchedule({ baseEnabled: on });
}

function applyDataPlaneButtonSchedule({ baseEnabled = true } = {}) {
  try {
    const win = getAshareActionWindow();
    const rt = document.getElementById('dataPlaneRefreshRealtime');
    const full = document.getElementById('dataPlaneRefreshFull');
    const free = Boolean(baseEnabled);

    if (rt) {
      const allow = free && win.realtime;
      rt.disabled = !allow;
      rt.title = allow
        ? '触发盘中实时 AVIX（' + win.windowLabel + '）'
        : '实时不可用：' + win.reason + '。仅交易日 ' + win.windowLabel + ' 可点';
      if (rt.dataset) rt.dataset.window = win.realtime ? 'open' : 'closed';
    }
    if (full) {
      const allow = free && win.daily;
      full.disabled = !allow;
      full.title = allow
        ? '触发日终正式更新（休市/盘后）'
        : '日更不可用：' + win.reason + '。请在盘后或非交易日使用';
      if (full.dataset) full.dataset.window = win.daily ? 'open' : 'closed';
    }
  } catch (err) {
    console.warn('applyDataPlaneButtonSchedule failed', err);
  }
}

function openGithubTokenDialog() {
  const dialog = document.getElementById('ghTokenDialog');
  const input = document.getElementById('ghTokenInput');
  const hint = document.getElementById('ghTokenHint');
  if (!dialog) {
    const token = window.prompt(
      '粘贴 GitHub PAT（仅存本机；需 Actions: Read and write）\n留空并确定可清除：',
      getGithubActionsPat() ? '•••• 已保存，重贴可覆盖' : '',
    );
    if (token == null) return;
    const cleaned = token.trim();
    if (!cleaned || cleaned.startsWith('••')) {
      if (!cleaned) setGithubActionsPat('');
    } else {
      setGithubActionsPat(cleaned);
    }
    paintStaticPagesPlaneMeta(null);
    return;
  }
  if (hint) {
    hint.textContent = hasGithubActionsPat()
      ? '已保存令牌（本机）。可粘贴新令牌覆盖，或点清除。'
      : '尚未配置。配置后「实时/日更」将触发仓库 Actions。';
  }
  if (input) input.value = '';
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', '');
}

function bindGithubTokenDialog() {
  const form = document.getElementById('ghTokenForm');
  const dialog = document.getElementById('ghTokenDialog');
  if (!form) return;
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const submitter = ev.submitter;
    const value = submitter && submitter.value ? submitter.value : 'cancel';
    const input = document.getElementById('ghTokenInput');
    if (value === 'save') {
      const token = (input?.value || '').trim();
      if (!token) {
        const hint = document.getElementById('ghTokenHint');
        if (hint) hint.textContent = '请粘贴 token 后再保存。';
        return;
      }
      setGithubActionsPat(token);
    } else if (value === 'clear') {
      setGithubActionsPat('');
    }
    if (dialog) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
    }
    paintStaticPagesPlaneMeta(null, {
      note: hasGithubActionsPat() ? '令牌已保存 · 可点实时/日更' : '已清除令牌',
    });
  });
}

async function dispatchGithubActionsWorkflow(mode) {
  const pat = getGithubActionsPat();
  if (!pat) {
    openGithubTokenDialog();
    throw new Error('请先配置 GitHub 令牌');
  }
  const cfg = dashboardState.dataPlane.actions;
  const workflow = mode === 'full' ? cfg.workflows.full : cfg.workflows.realtime;
  const inputs = mode === 'full'
    ? { mode: 'daily' }
    : { mode: 'single' };
  const url = `https://api.github.com/repos/${cfg.owner}/${cfg.repo}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${pat}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ref: cfg.ref, inputs }),
  });
  if (res.status === 204 || res.ok) {
    cfg.lastDispatchAt = Date.now();
    return { ok: true, workflow };
  }
  let detail = '';
  try {
    const body = await res.json();
    detail = body.message || JSON.stringify(body);
  } catch (_) {
    detail = await res.text().catch(() => '');
  }
  if (res.status === 401 || res.status === 403) {
    throw new Error('令牌无效或权限不足（需要 Actions: write）');
  }
  if (res.status === 404) {
    throw new Error('找不到 workflow 或仓库不可见：' + workflow);
  }
  throw new Error(detail || `GitHub API ${res.status}`);
}

async function waitForPagesDataRefresh({
  beforeBuildTime = null,
  beforeUpdateTime = null,
  maxWaitMs = 8 * 60 * 1000,
  intervalMs = 12000,
  onTick = null,
} = {}) {
  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < maxWaitMs) {
    attempt += 1;
    if (onTick) onTick(attempt, Math.round((Date.now() - started) / 1000));
    await new Promise(r => setTimeout(r, intervalMs));
    dashboardState.cacheBust = String(Date.now());
    try {
      const [info, latest] = await Promise.all([
        loadJSON('./data/build_info.json', { bust: true }).catch(() => null),
        loadJSON('./data/latest.json', { bust: true }).catch(() => null),
      ]);
      const buildTime = info?.build_time || null;
      const updateTime = latest?.update_time || latest?.as_of || null;
      const buildChanged = beforeBuildTime && buildTime && buildTime !== beforeBuildTime;
      const updateChanged = beforeUpdateTime && updateTime && updateTime !== beforeUpdateTime;
      // If we had no baseline, accept first successful load after grace period
      const graceOk = !beforeBuildTime && !beforeUpdateTime && attempt >= 2 && (buildTime || updateTime);
      if (buildChanged || updateChanged || graceOk) {
        return { buildTime, updateTime, latest, info };
      }
    } catch (_) {
      /* keep polling */
    }
  }
  return null;
}

async function requestDataPlaneRefresh(mode) {
  // Static Pages: dispatch GitHub Actions, then poll until published data moves.
  if (!dashboardState.dataPlane.available) {
    if (dashboardState.dataPlane.refreshInFlight) return;
    const win = getAshareActionWindow();
    if (mode === 'realtime' && !win.realtime) {
      paintStaticPagesPlaneMeta(null, {
        note: `实时仅交易日 ${win.windowLabel} 可点 · 当前：${win.reason}`,
      });
      applyDataPlaneButtonSchedule({ baseEnabled: true });
      return;
    }
    if (mode === 'full' && !win.daily) {
      paintStaticPagesPlaneMeta(null, {
        note: `日更仅盘后/休市可点 · 当前：${win.reason}`,
      });
      applyDataPlaneButtonSchedule({ baseEnabled: true });
      return;
    }
    if (!hasGithubActionsPat()) {
      openGithubTokenDialog();
      paintStaticPagesPlaneMeta(null, { note: '需先配置令牌才能触发 Actions' });
      return;
    }
    dashboardState.dataPlane.refreshInFlight = true;
    setDataPlaneButtonsEnabled(false);
    const metaEl = document.getElementById('dataPlaneMeta');
    const label = mode === 'full' ? '日更' : '实时';
    try {
      let beforeBuildTime = null;
      let beforeUpdateTime = null;
      try {
        const info = await loadJSON('./data/build_info.json', { bust: true });
        beforeBuildTime = info?.build_time || null;
      } catch (_) { /* ignore */ }
      try {
        const latest = await loadJSON('./data/latest.json', { bust: true });
        beforeUpdateTime = latest?.update_time || null;
      } catch (_) { /* ignore */ }

      paintStaticPagesPlaneMeta(null, { busy: true, note: `触发 ${label} Actions…` });
      await dispatchGithubActionsWorkflow(mode);
      paintStaticPagesPlaneMeta(null, {
        busy: true,
        note: `${label} 已排队 · 等待发布（约 2–8 分钟）…`,
      });

      const result = await waitForPagesDataRefresh({
        beforeBuildTime,
        beforeUpdateTime,
        onTick: (n, sec) => {
          paintStaticPagesPlaneMeta(null, {
            busy: true,
            note: `${label} 运行中 · ${sec}s · 轮询 #${n}`,
          });
        },
      });

      dashboardState.cacheBust = String(Date.now());
      await refreshDashboard(true);
      if (result) {
        paintStaticPagesPlaneMeta({
          risk_temperature: result.latest?.risk_temperature
            ?? document.getElementById('riskTemperature')?.textContent,
          trade_date: result.latest?.trade_date
            ?? document.getElementById('tradeDate')?.textContent,
          temperature_mode_cn: result.latest?.temperature_mode_cn
            || result.latest?.temperature_mode
            || document.getElementById('quality')?.textContent,
        }, { note: `${label} 完成 · 已重载` });
      } else {
        paintStaticPagesPlaneMeta(null, {
          note: `${label} 已触发，数据可能仍在发布 · 可稍后再点页面刷新`,
        });
      }
    } catch (err) {
      console.error(err);
      const bar = document.getElementById('dataPlaneBar');
      if (bar) bar.dataset.state = 'error';
      paintStaticPagesPlaneMeta(null, {
        note: `${label}失败：` + (err.message || String(err)),
      });
    } finally {
      dashboardState.dataPlane.refreshInFlight = false;
      setDataPlaneButtonsEnabled(true);
    }
    return;
  }

  if (dashboardState.dataPlane.refreshInFlight) return;
  dashboardState.dataPlane.refreshInFlight = true;
  setDataPlaneButtonsEnabled(false);
  const metaEl = document.getElementById('dataPlaneMeta');
  if (metaEl) metaEl.textContent = mode === 'full' ? '日更…' : '实时…';
  try {
    const res = await fetch('./api/refresh?mode=' + encodeURIComponent(mode), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    const payload = await res.json().catch(() => ({}));
    if (mode === 'full' && res.status === 202) {
      for (let i = 0; i < 90; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const st = await fetchDataPlaneStatus();
        if (st && !st.refresh_running) break;
      }
    } else if (!res.ok && !payload.ok) {
      throw new Error(payload.error || payload.detail || ('refresh failed ' + res.status));
    }
    dashboardState.cacheBust = String(Date.now());
    await refreshDashboard(true);
    await fetchDataPlaneStatus();
  } catch (err) {
    console.error(err);
    const bar = document.getElementById('dataPlaneBar');
    if (bar) bar.dataset.state = 'error';
    if (metaEl) metaEl.textContent = '刷新失败：' + (err.message || String(err));
  } finally {
    dashboardState.dataPlane.refreshInFlight = false;
    setDataPlaneButtonsEnabled(true);
  }
}

function bindDataPlaneControls() {
  const rt = document.getElementById('dataPlaneRefreshRealtime');
  const full = document.getElementById('dataPlaneRefreshFull');
  const setup = document.getElementById('dataPlaneTokenSetup');
  if (rt) rt.addEventListener('click', () => requestDataPlaneRefresh('realtime'));
  if (full) full.addEventListener('click', () => requestDataPlaneRefresh('full'));
  if (setup) setup.addEventListener('click', () => openGithubTokenDialog());
  bindGithubTokenDialog();
  applyDataPlaneButtonSchedule({ baseEnabled: true });
  // Re-evaluate window at minute boundary so buttons unlock without reload.
  if (!dashboardState.dataPlane._scheduleTimer) {
    dashboardState.dataPlane._scheduleTimer = setInterval(() => {
      if (dashboardState.dataPlane.refreshInFlight) return;
      const base = dashboardState.dataPlane.available
        ? !(dashboardState.dataPlane.status && dashboardState.dataPlane.status.refresh_running)
        : true;
      applyDataPlaneButtonSchedule({ baseEnabled: base });
    }, 30 * 1000);
  }
}

async function main() {
  document.body.classList.add('is-loading');
  bindMagazineChrome();
  // Probe independent local data plane first (never GitHub).
  await fetchDataPlaneStatus();
  try {
    const critical = await loadCriticalDashboardData();
    renderCriticalDashboard(critical);
    updateRefreshStatus('ok', '核心数据已加载；正在加载策略与 Flex…');
    document.body.classList.remove('is-loading');
    const heavy = await loadHeavyDashboardData();
    renderHeavyDashboard(heavy);
    updateRefreshStatus('ok', '初始数据加载完成；页面每 60 秒检查 latest 是否更新');
    await fetchDataPlaneStatus();
  } catch (err) {
    document.body.classList.remove('is-loading');
    throw err;
  }
  bindRangeControls();
  bindFlexModeControls();
  bindFlexExecControls();
  renderFlexExecUi();
  window.addEventListener('resize', () => {
    resizeVisibleCharts();
  });
}

main().catch(err => {
  document.body.classList.add('error');
  showLoadError(err.message || String(err));
  console.error(err);
});

setInterval(() => {
  if (!document.hidden) {
    refreshDashboard();
    fetchDataPlaneStatus();
  }
}, AUTO_REFRESH_MS);

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshDashboard();
    fetchDataPlaneStatus();
  }
});
