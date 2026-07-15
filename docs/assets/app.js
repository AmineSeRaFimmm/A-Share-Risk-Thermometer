const AUTO_REFRESH_MS = 60 * 1000;
const STALE_THRESHOLD_MS = 15 * 60 * 1000;
const FLEX_MODE_KEY = 'ashare_flex_mode_v1';

function loadFlexModePreference() {
  try {
    const m = localStorage.getItem(FLEX_MODE_KEY);
    if (m === 'conservative' || m === 'aggressive') return m;
  } catch (_) { /* ignore */ }
  return 'aggressive';
}

function saveFlexModePreference(mode) {
  try {
    localStorage.setItem(FLEX_MODE_KEY, mode === 'conservative' ? 'conservative' : 'aggressive');
  } catch (_) { /* ignore */ }
}

const dashboardState = {
  activeRange: '1Y',
  componentChart: null,

  timeCharts: [],
  history: [],
  nowcastHistory: {},
  strategy: {},
  refreshInFlight: false,
  cacheBust: null,
  lastUpdateTime: null,
  lastTradeDate: null,
  heavyLoaded: false,
  flexMode: loadFlexModePreference(),
  flexPlaybook: null,
  flexActive: null,
  flexLedgerBound: false,
  flexModal: null,
};

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
  const officialTxt = `正式收盘 ${official.trade_date || '--'} RT ${official.risk_temperature ?? '--'}（官方 AVIX）`;
  if (mode === 'NOWCAST' || (nowcast.active && latest?.is_final === false && mode !== 'ESTIMATED_CLOSE')) {
    note.textContent = `盘中估算 RT ${nowcast.risk_temperature ?? latest?.risk_temperature ?? '--'}（实时 AVIX ${formatRealtimeAvix(nowcast.realtime_avix ?? latest?.avix?.avix_realtime_mid)}）· ${officialTxt}。仅替换 AVIX 三因子；宽度/回撤等沿用最近正式收盘。官方日线就绪后自动切回正式收盘。`;
    return;
  }
  if (mode === 'ESTIMATED_CLOSE') {
    note.textContent = `估算收盘：正式期权日线缺口，用严格 OK 的实时 AVIX 估算 · ${officialTxt}。不写入正式历史序列。`;
    return;
  }
  const reason = nowcast.reason_cn || '盘中可用时将显示实时估算';
  note.textContent = `当前为收盘正式口径 · ${officialTxt}。${reason}`;
}

function renderBreadthMode(latest) {
  ensureRealtimeMeta();
  appendMetaItem('宽度口径', 'breadthMode');
  const market = latest?.market || {};
  const modeCn = market.breadth_mode_cn || '--';
  const mode = market.breadth_mode || '';
  setText('breadthMode', modeCn);
  const el = document.getElementById('breadthMode');
  if (!el) return;
  el.dataset.breadth = (mode || 'unknown').toLowerCase();
  el.title = market.breadth_quality
    ? `宽度质量: ${market.breadth_quality}`
    : mode === 'INDEX_PROXY'
      ? '历史多数日期使用宽基指数代理宽度，不是全A个股涨跌统计'
      : '基于全A现货快照统计';
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
  renderRealtimeAvix(latest.avix || {});
  updateFreshness(latest);
  renderNowcastNote(latest);
  setText('headline', latest.interpretation?.headline);
  setText('summary', latest.interpretation?.summary);
  setText('posture', latest.interpretation?.posture);
  document.getElementById('temperaturePanel').dataset.zone = getTempClass(Number(latest.risk_temperature));
}

function renderAudit(audit) {
  setText('lastSuccessfulUpdate', audit.last_successful_update);
  const grid = document.getElementById('healthGrid');
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

  const signalBox = document.getElementById('strategySignals');
  if (!signalBox) return;
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
  if (!nowcastHistory || nowcastHistory.status === 'missing') {
    note.textContent = '估算历史暂不可用。';
    return;
  }
  const rows = nowcastHistory.rows || [];
  const unavailable = (nowcastHistory.gaps || []).filter(row => row.estimate_status !== '可用').map(row => row.date);
  const estimatePart = rows.length
    ? `估算可用 ${rows[0].date || rows[0].trade_date} 至 ${rows[rows.length - 1].date || rows[rows.length - 1].trade_date}`
    : '暂无估算点';
  const missingPart = unavailable.length ? `仍缺 ${unavailable.join('、')}` : '无未解释缺口';
  note.textContent = `正式收盘最新 ${nowcastHistory.official_latest_date || '--'}；${estimatePart}；${missingPart}。`;
}

function filterHistoryByRange(history, range) {
  if (!history?.length || range === 'ALL') return history || [];
  const months = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36 }[range] || 12;
  const last = new Date(history[history.length - 1].date + 'T00:00:00');
  const cutoff = new Date(last);
  cutoff.setMonth(cutoff.getMonth() - months);
  return history.filter(row => new Date(row.date + 'T00:00:00') >= cutoff);
}

function setActiveRange(range) {
  document.querySelectorAll('#rangeControls button').forEach(button => {
    button.classList.toggle('active', button.dataset.range === range);
  });
}

function connectTimeCharts(charts) {
  charts.forEach(chart => {
    chart.group = 'risk-time-series';
  });
  echarts.connect('risk-time-series');
}

function renderTimeCharts(history, strategy, range) {
  const filtered = filterHistoryByRange(history, range);
  ['historyChart', 'avixQvixChart', 'hs300Chart'].forEach(id => {
    const instance = echarts.getInstanceByDom(document.getElementById(id));
    if (instance) instance.dispose();
  });
  const charts = [
    renderHistoryChart(filtered, strategy),
    renderAvixQvixChart(filtered, strategy),
    renderHs300Chart(filtered)
  ];
  connectTimeCharts(charts);
  return charts;
}

function bindRangeControls(onRange) {
  document.querySelectorAll('#rangeControls button').forEach(button => {
    button.addEventListener('click', () => {
      const range = button.dataset.range || '1Y';
      setActiveRange(range);
      onRange(range);
    });
  });
}

function bindFlexModeControls() {
  // Reflect persisted mode on first paint.
  document.querySelectorAll('.flex-mode-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.flexMode === dashboardState.flexMode);
  });
  document.querySelectorAll('.flex-mode-btn').forEach(button => {
    button.addEventListener('click', () => {
      const mode = button.dataset.flexMode || 'aggressive';
      dashboardState.flexMode = mode;
      saveFlexModePreference(mode);
      if (dashboardState.flexPlaybook) {
        renderFlexTradePanel(dashboardState.flexPlaybook);
      }
    });
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
  const [strategy, rtTactical, stagePlaybook] = await Promise.all([
    loadJSON('./data/strategy.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/rt_tactical.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/stage_playbook.json').catch(() => ({ status: 'missing' })),
  ]);
  dashboardState.heavyLoaded = true;
  return { strategy, rtTactical, stagePlaybook };
}

function renderCriticalDashboard({ latest, history, nowcastHistory, components, audit }) {
  document.body.classList.remove('error');
  hideLoadError();
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
    dashboardState.activeRange
  );
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
      dashboardState.activeRange
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
  AVOID: { text: '回避', cls: 'avoid' },
  FLAT: { text: '观望', cls: 'wait' },
  SELL: { text: '卖出', cls: 'sell' },
  OVERWEIGHT_RELATIVE: { text: '明天买入', cls: 'buy' },
  UNDERWEIGHT_RELATIVE: { text: '低配', cls: 'avoid' },
};

const FLEX_LEDGER_KEY = 'ashare_flex_exec_ledger_v1';
const FLEX_BUY_ACTIONS = new Set(['OPEN', 'OVERWEIGHT', 'BUY', 'OVERWEIGHT_RELATIVE']);
const FLEX_CLOSE_ACTIONS = new Set(['CLOSE', 'SELL']);

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

function defaultFlexLedger() {
  return {
    version: 2,
    capital: 0,
    cash: 0,
    positions: {},
    journal: [],
    updated_at: null,
  };
}

function flexOpenPositions(ledger) {
  return Object.values(ledger?.positions || {}).filter(p => Number(p.qty) > 1e-9);
}

function flexDeployedCost(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => sum + (Number(p.cost_basis) || 0), 0);
}

function flexMarkValue(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => {
    const mark = Number(p.last_price);
    const px = Number.isFinite(mark) && mark > 0 ? mark : Number(p.avg_price) || 0;
    return sum + Number(p.qty) * px;
  }, 0);
}

/** Migrate v1 ledgers that derived cash as capital−cost (dropped realized PnL). */
function normalizeFlexLedger(raw) {
  const ledger = {
    version: 2,
    capital: Number(raw?.capital) || 0,
    cash: raw?.cash,
    positions: raw?.positions && typeof raw.positions === 'object' ? raw.positions : {},
    journal: Array.isArray(raw?.journal) ? raw.journal : [],
    updated_at: raw?.updated_at || null,
  };
  if (ledger.cash == null || !Number.isFinite(Number(ledger.cash))) {
    // Best-effort migration for pre-v2 books.
    ledger.cash = Math.max(0, ledger.capital - flexDeployedCost(ledger));
  } else {
    ledger.cash = Number(ledger.cash);
  }
  return ledger;
}

function loadFlexLedger() {
  try {
    const raw = localStorage.getItem(FLEX_LEDGER_KEY);
    if (!raw) return defaultFlexLedger();
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return defaultFlexLedger();
    return normalizeFlexLedger(parsed);
  } catch (_) {
    return defaultFlexLedger();
  }
}

function saveFlexLedger(ledger) {
  const normalized = normalizeFlexLedger(ledger);
  normalized.updated_at = new Date().toISOString();
  localStorage.setItem(FLEX_LEDGER_KEY, JSON.stringify(normalized));
  return normalized;
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

/** Remaining hold days / exit date — only meaningful after user confirmed buy. */
function flexPositionExitInfo(pos) {
  if (!pos || !(Number(pos.qty) > 0)) return { left: null, exitDate: null, label: '—' };
  const today = flexDateCn(0);
  let exitDate = pos.exit_date ? String(pos.exit_date).slice(0, 10) : null;
  if (!exitDate && pos.buy_date && pos.hold_days != null) {
    exitDate = flexAddCalendarDays(pos.buy_date, Number(pos.hold_days));
  }
  let left = null;
  if (exitDate) {
    left = flexCalendarDaysBetween(today, exitDate);
    // if exit in the past, left = 0
    if (exitDate < today) left = 0;
  } else if (pos.hold_days != null && pos.buy_date) {
    left = Math.max(0, Number(pos.hold_days) - flexCalendarDaysBetween(pos.buy_date, today));
  }
  const exitMd = exitDate ? flexFormatMdBuy(exitDate)?.replace(/买$/, '清') : null;
  const label = left != null
    ? (exitMd ? `剩${left}日 · ${exitMd}` : `剩${left}日`)
    : '—';
  return { left, exitDate, label };
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
  const exitDate = draft.exit_date
    ? String(draft.exit_date).slice(0, 10)
    : holdDays != null
      ? flexAddCalendarDays(buyDate, holdDays)
      : null;

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
  const ledger = loadFlexLedger();
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

  const uEl = document.getElementById('flexExecUPnl');
  if (uEl) {
    uEl.textContent = hasBook && deployed > 0 ? flexFormatSignedMoney(uPnl) : (hasBook ? '0' : '—');
    uEl.classList.remove('up', 'down');
    // Never classList.add('') — DOMTokenList rejects empty tokens.
    if (hasBook && deployed > 0) {
      if (uPnl > 0) uEl.classList.add('up');
      else if (uPnl < 0) uEl.classList.add('down');
    }
  }
  const rEl = document.getElementById('flexExecRPnl');
  if (rEl) {
    rEl.textContent = hasBook ? flexFormatSignedMoney(rPnl) : '—';
    rEl.classList.remove('up', 'down');
    if (hasBook) {
      if (rPnl > 0) rEl.classList.add('up');
      else if (rPnl < 0) rEl.classList.add('down');
    }
  }

  const note = document.getElementById('flexMarkNote');
  if (note) {
    note.textContent = mtm > 0
      ? '估值说明：市值 / 浮动盈亏按最近一次录入成交价计算，非实时行情。'
      : '估值说明：录入成交价后计算市值与浮动盈亏；本机记账不会自动下单。';
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
  const ledger = loadFlexLedger();
  const positions = flexOpenPositions(ledger);
  const capital = Number(ledger.capital) || 0;
  if (!positions.length) {
    el.innerHTML = `<div class="flex-empty-state soft">
      <strong>本机暂无持仓</strong>
      <p>在「信号」里点「买」并录入成交价后，会记入本机账本。数据只存在当前浏览器。</p>
    </div>`;
    return;
  }
  positions.sort((a, b) => (Number(b.cost_basis) || 0) - (Number(a.cost_basis) || 0));
  el.innerHTML = positions.map(pos => {
    const weight = capital > 0 ? (Number(pos.cost_basis) / capital) : null;
    const mark = Number(pos.last_price);
    const mtm = Number.isFinite(mark) ? Number(pos.qty) * mark : null;
    const pnl = mtm != null ? mtm - Number(pos.cost_basis) : null;
    const pnlCls = pnl == null ? '' : pnl >= 0 ? 'up' : 'down';
    const pnlTxt = pnl == null ? '—' : flexFormatSignedMoney(pnl);
    const exitInfo = flexPositionExitInfo(pos);
    return `<div class="flex-row flex-row-book" data-pos-key="${escapeHtml(pos.key)}" title="${escapeHtml(exitInfo.label)}">
      <span class="flex-row-code" data-label="代码">${escapeHtml(pos.etf_code || '—')}</span>
      <span class="flex-row-name" data-label="名称">${escapeHtml(pos.name || '—')}</span>
      <span class="flex-row-num" data-label="成本">${formatMoney(pos.cost_basis)}</span>
      <span class="flex-row-num" data-label="均价">${formatPrice(pos.avg_price)}</span>
      <span class="flex-row-num" data-label="仓位">${weight != null ? pctLabel(weight) : '—'}</span>
      <span class="flex-row-num ${pnlCls}" data-label="浮盈亏">${pnlTxt}</span>
      <span class="flex-row-num flex-row-exit" data-label="清仓">${escapeHtml(exitInfo.label)}</span>
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
    const pnl = row.pnl != null && Number.isFinite(Number(row.pnl))
      ? flexFormatSignedMoney(row.pnl)
      : '—';
    const pnlCls = Number(row.pnl) > 0 ? 'up' : Number(row.pnl) < 0 ? 'down' : '';
    return `<div class="flex-row flex-row-log">
      <span class="flex-row-tag" data-label="类型">${escapeHtml(label)}</span>
      <span class="flex-row-code" data-label="代码">${escapeHtml(code)}</span>
      <span class="flex-row-num" data-label="金额">${formatMoney(row.amount)}</span>
      <span class="flex-row-num" data-label="价格">${Number(row.price) > 0 ? formatPrice(row.price) : '—'}</span>
      <span class="flex-row-num ${pnlCls}" data-label="盈亏">${pnl}</span>
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
      preview.textContent = `卖出 ${formatShares(sellQty)} · 金额 ${formatMoney(sellAmt)} · 预计盈亏 ${flexFormatSignedMoney(pnl)} · 剩余 ${formatShares(Math.max(0, Number(pos.qty) - sellQty))}`;
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
      const pnl = amt - Number(pos.cost_basis);
      preview.textContent = `全平约 ${formatMoney(amt)} · 预计盈亏 ${flexFormatSignedMoney(pnl)} · 回现金`;
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
    if (dashboardState.flexPlaybook) {
      renderFlexTradePanel(dashboardState.flexPlaybook);
    } else {
      renderFlexExecUi();
    }
  } catch (e) {
    if (err) {
      err.hidden = false;
      err.textContent = e.message || String(e);
    }
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
 * Desk badge: T-day signal → next open = 明天买入.
 * Paper HOLD is not a desk buy signal (no「7月8日买」, no「今日买入」).
 */
function flexActionBadge(item, flex, options = {}) {
  const action = String(item?.action || item?.side || '').toUpperCase();
  if (action === 'HOLD') {
    return options.localHeld
      ? { text: '持有中', cls: 'hold' }
      : { text: '—', cls: 'wait' };
  }
  if (FLEX_BUY_ACTIONS.has(action) || action === 'OPEN') {
    return { text: '明天买入', cls: 'buy' };
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
 * Actionable window for the desk: 今天 + 明天（上海日历）.
 * Also used as a soft "book is current" check (see flexBookLagDays).
 */
function flexActionableDateSet() {
  return new Set([flexDateCn(0), flexDateCn(1)]);
}

function flexDateInActionWindow(dateStr, windowSet = flexActionableDateSet()) {
  if (!dateStr) return false;
  const day = String(dateStr).slice(0, 10);
  return windowSet.has(day);
}

/** Calendar day lag from as_of → Shanghai today (0 = same day). */
function flexBookLagDays(asOf) {
  const a = String(asOf || '').slice(0, 10);
  const b = flexDateCn(0);
  if (!a || !/^\d{4}-\d{2}-\d{2}$/.test(a)) return null;
  const [y1, m1, d1] = a.split('-').map(Number);
  const [y2, m2, d2] = b.split('-').map(Number);
  const t1 = Date.UTC(y1, m1 - 1, d1);
  const t2 = Date.UTC(y2, m2 - 1, d2);
  return Math.round((t2 - t1) / 86400000);
}

/**
 * Desk rule (personal execution, not paper simulation):
 * 1) Strategy emits a buy signal only on the signal day (as_of == 上海今天).
 * 2) It becomes YOUR holding only after you click 买 and confirm fill.
 * 3) If you do not click 买, the open signal is gone the next day — no catch-up,
 *    no promoting multi-day paper HOLD into "still buyable".
 * 4) CLOSE / AVOID only apply to names you personally hold in the local ledger.
 * 5) Your own hold clock starts on the day you confirmed the buy.
 */
function flexBookIsToday(asOf) {
  const a = String(asOf || '').slice(0, 10);
  return !!a && a === flexDateCn(0);
}

/** True when local ledger has an open position matching this signal row. */
function flexIsLocallyHeld(item, ledger = loadFlexLedger()) {
  if (!item) return false;
  const openPos = flexOpenPositions(ledger);
  if (!openPos.length) return false;
  const key = flexPositionKey(item);
  if (ledger.positions?.[key] && Number(ledger.positions[key].qty) > 1e-9) return true;
  const code = String(item.etf_code || item.code || '').trim();
  const name = String(item.name || item.sector || '').trim();
  return openPos.some(p => {
    const pc = String(p.etf_code || '').trim();
    const pn = String(p.name || '').trim();
    if (code && pc && code === pc) return true;
    if (name && pn && name === pn) return true;
    return false;
  });
}

/** True fresh OPEN rows from engine for today only (never paper HOLD archaeology). */
function deskFreshOpenSignals(flex) {
  const f = flex || {};
  const openKeys = new Set(['OPEN', 'BUY', 'OVERWEIGHT', 'OVERWEIGHT_RELATIVE']);
  const byKey = new Map();
  const put = (item) => {
    if (!item) return;
    const action = String(item.action || item.side || '').toUpperCase();
    if (!openKeys.has(action)) return;
    const key = flexPositionKey(item);
    if (!key || byKey.has(key)) return;
    byKey.set(key, { ...item, action: action === 'BUY' ? 'OPEN' : item.action || 'OPEN' });
  };
  for (const item of f.buy_list || []) put(item);
  for (const item of f.minimal_actions || []) put(item);
  // Intentionally ignore hold_list / satellite.buy / paper CLOSE-as-reopen.
  // Missed the signal day → disappears; do not resurrect next day.
  return [...byKey.values()];
}

/** Personal positions whose hold window has ended (user's buy_date clock). */
function deskLocalDueCloses(ledger = loadFlexLedger()) {
  const today = flexDateCn(0);
  const rows = [];
  for (const pos of flexOpenPositions(ledger)) {
    const info = flexPositionExitInfo(pos);
    const due = (info.exitDate && info.exitDate <= today)
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
      why: `本机买入日起持有期满（买 ${pos.buy_date || '—'} · 计划 ${pos.hold_days ?? '—'} 日）`,
      days_held: pos.buy_date ? flexCalendarDaysBetween(pos.buy_date, today) : null,
      weight_target: 0,
      weight_hint: '0%',
      _key: pos.key || flexPositionKey(pos),
      _deskLocalDue: true,
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

  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);
  const isToday = flexBookIsToday(asOf);

  // Stale strategy book → no open/close/avoid from strategy (missed day is gone).
  // Still surface personal due closes so hold clocks keep working.
  if (!isToday) {
    for (const item of deskLocalDueCloses(ledger)) pushUnique('close', item);
    buckets.hold = [];
    return buckets;
  }

  // OPEN: only today's fresh strategy opens, and only if you have not already bought.
  for (const item of deskFreshOpenSignals(f)) {
    if (flexIsLocallyHeld(item, ledger)) continue;
    pushUnique('open', item);
  }

  // CLOSE: strategy exit tips only if you personally hold the name.
  for (const item of [...(f.close_list || []), ...(f.sell_list || []), ...(f.minimal_actions || [])]) {
    const action = String(item.action || item.side || '').toUpperCase();
    if (!closeKeys.has(action)) continue;
    if (!flexIsLocallyHeld(item, ledger)) continue;
    pushUnique('close', item);
  }
  // Plus: your own hold-days expired (independent of paper engine).
  for (const item of deskLocalDueCloses(ledger)) pushUnique('close', item);

  // AVOID: only tip names you actually hold.
  for (const item of f.avoid_list || []) {
    const action = String(item.action || item.side || '').toUpperCase();
    if (!avoidKeys.has(action) && action !== 'FLAT') continue;
    if (!flexIsLocallyHeld(item, ledger)) continue;
    pushUnique('avoid', item);
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
    const key = item._key || flexPositionKey(item);
    const held = ledger.positions[key] && Number(ledger.positions[key].qty) > 0;
    const isHoldRow = forceKind === 'hold' || action === 'HOLD';
    const badgeInfo = isHoldRow
      ? flexActionBadge({ ...item, action: 'HOLD' }, flex, { localHeld: held })
      : flexActionBadge(item, flex, { localHeld: held });
    const suggested = flexSuggestedAmount(item, capital);
    const etfCode = item.etf_code || '';
    const name = item.name || '—';
    const w = item.weight_hint || (item.weight_target != null ? pctLabel(item.weight_target) : '—');
    const amt = suggested != null ? formatMoney(suggested) : '—';
    // 剩余/清仓时间：仅本机已确认买入后才显示（用账本 exit 计划）
    const localPos = held ? ledger.positions[key] : null;
    const left = held ? flexPositionExitInfo(localPos).label : '—';
    const isAvoid = forceKind === 'avoid' || action === 'AVOID' || action === 'UNDERWEIGHT_RELATIVE' || action === 'FLAT';
    // Avoid rows only appear when held; allow reduce/close. Other non-avoid keep prior rules.
    const interactive = !isAvoid || held;

    // Buy plan starts when user confirms (today's bookkeeping); full default hold window.
    const planDays = options.defaultHoldDays != null ? Number(options.defaultHoldDays) : null;

    let acts = '';
    if (interactive) {
      if (isAvoid) {
        // Only listed when user holds it — tip + act
        acts = held
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`
          : '<span class="flex-row-muted">—</span>';
      } else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
        acts = held
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`
          : '<span class="flex-row-muted">—</span>';
      } else if (held) {
        acts = `<button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(key)}">加</button>
          <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
          <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`;
      } else if (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action)) {
        // Only true OPEN/BUY — never paper HOLD
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

    return `<div class="flex-row ${badgeInfo.cls}${held ? ' is-held' : ''}">
      <span class="badge badge-wide" data-label="信号">${escapeHtml(badgeInfo.text)}</span>
      <span class="flex-row-code" data-label="代码">${escapeHtml(etfCode || '—')}</span>
      <span class="flex-row-name" data-label="名称">${escapeHtml(name)}</span>
      <span class="flex-row-num" data-label="目标">${escapeHtml(String(w))}</span>
      <span class="flex-row-num" data-label="建议">${amt}</span>
      <span class="flex-row-num flex-row-muted" data-label="清仓">${escapeHtml(left)}</span>
      <span class="flex-row-acts" data-label="操作">${acts}</span>
    </div>`;
  }).join('');
}

function renderFlexSignalList(flex, options = {}) {
  const buckets = splitFlexSignalBuckets(flex || {});
  const defaultHoldDays = flex?.hold_days != null ? Number(flex.hold_days) : 5;
  const map = [
    { kind: 'open', id: 'flexOpenList', forceKind: 'open', countId: 'flexOpenCount' },
    { kind: 'hold', id: 'flexHoldList', forceKind: 'hold', countId: 'flexHoldCount' },
    { kind: 'close', id: 'flexCloseList', forceKind: 'close', countId: 'flexCloseCount' },
    { kind: 'avoid', id: 'flexAvoidList', forceKind: 'avoid', countId: 'flexAvoidCount' },
  ];

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
      const dq = flex?.data_quality || {};
      const officialAsOf = dq.official_as_of || asOf;
      if (title && body) {
        if (lag != null && lag > 0) {
          title.textContent = '今天没有买入信号';
          body.textContent = `策略书 as_of=${asOf || '—'}，日历今天=${today}（差 ${lag} 天）。买入信号只活「信号当天」；不点买，过一天就消失。已点买的看「持仓」页。`;
        } else {
          title.textContent = '今天没有买入信号';
          body.textContent = asOf
            ? `策略书 as_of=${asOf}：今天没有新开信号。规则：当天点「买」才进你的仓；不点，过一天信号消失，不会把纸面多日持有再当成可买。`
            : '今天没有买入信号。信号只活一天；不点买就过期。';
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
      alert('请输入大于 0 的全仓金额');
      return;
    }
    const ledger = loadFlexLedger();
    const prev = Number(ledger.capital) || 0;
    const next = Math.round(capital * 100) / 100;
    const delta = next - prev;
    // Adjust cash by the same delta so funding changes affect spendable cash.
    ledger.cash = Math.max(0, Number(ledger.cash) + delta);
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
    if (!confirm('确认清空本机持仓与成交流水？全仓金额可保留，现金将重置为全仓。')) return;
    const ledger = loadFlexLedger();
    const capital = Number(ledger.capital) || 0;
    const next = defaultFlexLedger();
    next.capital = capital;
    next.cash = capital;
    saveFlexLedger(next);
    if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
    else renderFlexExecUi();
  });

  document.getElementById('flexExportLedgerBtn')?.addEventListener('click', () => {
    const ledger = loadFlexLedger();
    const blob = new Blob([JSON.stringify(ledger, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `flex-ledger-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
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
      if (!confirm(`导入账本？将覆盖本机当前持仓与流水。\n全仓 ${formatMoney(ledger.capital)} · 持仓 ${flexOpenPositions(ledger).length} · 流水 ${(ledger.journal || []).length}`)) {
        return;
      }
      saveFlexLedger(ledger);
      if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
      else renderFlexExecUi();
    } catch (e) {
      alert(e.message || '导入失败');
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
    if (ev.key === 'Escape') closeFlexTradeModal();
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
  const cfg = modes[mode];
  if (!cfg) return flex;
  const copy = { ...flex, mode };
  // Allocation strip: true engine OPEN today, or personal holdings — not paper HOLD archaeology.
  const freshOpen = deskFreshOpenSignals(flex);
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
  const coreOn = hasCoreOpen || coreHeld
    || !!(flex.core && flex.core.active && String(flex.core.action || '').toUpperCase() === 'OPEN');
  const satOn = hasSatOpen || satHeld
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
  const mode = dashboardState.flexMode || flex.mode || 'aggressive';
  flex = applyFlexModeOverlay(flex, mode);
  // Promote root playbook metadata onto flex for signal filters / empty-state copy.
  flex = {
    ...flex,
    as_of: flex.as_of || playbook?.as_of || '',
    data_quality: playbook?.data_quality || flex.data_quality || null,
  };
  dashboardState.flexActive = flex;

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
    asOfEl.title = [
      `策略书 as_of=${asOf}`,
      dq.official_as_of ? `正式 RT=${dq.official_as_of}` : '',
      dq.bridged ? `桥接日 ${(dq.bridged_dates || []).join(',')}` : '',
      lag != null && lag > 0 ? `落后今日 ${lag} 个日历日` : '与今日对齐',
    ].filter(Boolean).join(' · ');
    asOfEl.classList.toggle('warn', !!(lag != null && lag > 0));
  }
  setText('flexHold', String(flex.hold_days || 5));
  setText('flexStatsShort', pctLabel(full.win_rate));

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
  const fresh = deskFreshOpenSignals(flex);
  const coreOpenToday = fresh.some(x =>
    String(x.sleeve || '') === 'core'
    || String(x.name || '').includes('沪深300')
    || String(x.etf_code || '') === (core.etf_code || '510300'));
  const satOpenToday = fresh.some(x =>
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
    coreEl.dataset.tone = coreHeld ? 'buy' : (coreOpenToday ? 'buy' : 'wait');
  }
  if (satEl) {
    satEl.dataset.tone = satHeld ? 'buy' : (satOpenToday ? 'buy' : 'wait');
  }
  // Sleeve cards: personal fill + today's fresh open only
  let coreActionLabel = '观望';
  if (coreHeld) coreActionLabel = '本机持有';
  else if (coreOpenToday) coreActionLabel = '今日可买';
  setText('flexCoreAction', coreActionLabel);
  setText('flexCoreWeight', wCore != null ? pctLabel(wCore) : (core.etf_code || '—'));
  let satActionLabel = '空仓';
  if (satHeld) satActionLabel = '本机持有';
  else if (satOpenToday) satActionLabel = '今日可买';
  setText('flexSatStage', satActionLabel);
  setText('flexSatWeight', wSat != null ? pctLabel(wSat) : '—');
  if (coreEl) {
    coreEl.title = [
      coreHeld ? '本机已点买' : '本机未点买',
      coreOpenToday ? '今日有新开信号' : '今日无新开（错过即消失）',
      core.etf_code,
    ].filter(Boolean).join(' · ');
  }
  if (satEl) {
    satEl.title = [
      satHeld ? '本机已点买' : '本机未点买',
      satOpenToday ? '今日有新开信号' : '今日无新开（错过即消失）',
      sat.stage_cn,
    ].filter(Boolean).join(' · ');
  }

  document.querySelectorAll('.flex-mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.flexMode === mode);
  });

  const modeCfg = (flex.modes || {})[mode] || {};
  const modeLabel = mode === 'conservative' ? '保守' : '进取';
  const modeDetail = modeCfg.label_cn
    || (mode === 'conservative' ? '50/30 · cap80%' : '60/40 · 单仓满');
  setText('flexModeHint', modeLabel);
  const modeHintEl = document.getElementById('flexModeHint');
  if (modeHintEl) modeHintEl.title = modeDetail;

  const trust = document.getElementById('flexTrustLine');
  if (trust && asOf) {
    trust.innerHTML = `<strong>执行台规则</strong>：信号 as_of <code>${escapeHtml(String(asOf).slice(0, 10))}</code>。<strong>当天点「买」才算你的仓</strong>；不点，过一天信号消失。纸面模拟仓≠你的 hold。清仓按你买入日起算的持有期。成交价自行录入。`;
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

async function main() {
  document.body.classList.add('is-loading');
  try {
    const critical = await loadCriticalDashboardData();
    renderCriticalDashboard(critical);
    updateRefreshStatus('ok', '核心数据已加载；正在加载策略与 Flex…');
    document.body.classList.remove('is-loading');
    const heavy = await loadHeavyDashboardData();
    renderHeavyDashboard(heavy);
    updateRefreshStatus('ok', '初始数据加载完成；页面每 60 秒检查 latest 是否更新');
  } catch (err) {
    document.body.classList.remove('is-loading');
    throw err;
  }
  bindRangeControls(range => {
    dashboardState.activeRange = range;
    dashboardState.timeCharts = renderTimeCharts(dashboardState.history, dashboardState.strategy, range);
  });
  bindFlexModeControls();
  bindFlexExecControls();
  renderFlexExecUi();
  window.addEventListener('resize', () => {
    dashboardState.componentChart?.resize();
    dashboardState.timeCharts.forEach(chart => chart.resize());
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
  }
}, AUTO_REFRESH_MS);

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshDashboard();
  }
});
