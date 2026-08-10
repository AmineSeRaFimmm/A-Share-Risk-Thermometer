const AUTO_REFRESH_MS = 60 * 1000;
const STALE_THRESHOLD_MS = 15 * 60 * 1000;
const FLEX_MODE_KEY = 'ashare_flex_mode_v1';
const FLEX_BOOK_KEY = 'ashare_flex_book_v1'; // real | sim
const FLEX_LEDGER_KEY_REAL = 'ashare_flex_exec_ledger_v1';
const FLEX_LEDGER_KEY_SIM = 'ashare_flex_exec_ledger_sim_v1';
const FLEX_REALTIME_QUOTE_TTL_MS = 45 * 1000;
const FLEX_REALTIME_POLL_MS = 30 * 1000;

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
  intradayTemperatureChart: null,

  timeCharts: [],
  chartInstances: { history: null, avix: null, hs300: null },
  history: [],
  tradeCalendar: {},
  nowcastHistory: {},
  intradayTemperature: {},
  strategy: {},
  refreshInFlight: false,
  refreshPromise: null,
  forceRefreshQueued: false,
  cacheBust: null,
  freshRequestSequence: 0,
  lastUpdateTime: null,
  lastTradeDate: null,
  lastBuildTime: null,
  heavyLoaded: false,
  flexMode: 'aggressive',
  flexBook: loadFlexBookPreference(), // real = 本机点买；sim = 策略严格跟随
  flexPlaybook: null,
  flexActive: null,
  flexLedgerBound: false,
  flexModal: null,
  flexSimSyncedAsOf: null,
  flexRealtimeQuotes: { quotes: {}, source: null, fetchedAt: 0 },
  flexRealtimeQuoteInFlight: false,
  flexRealtimeQuoteTimer: null,
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
  try {
    const calendar = dashboardState.tradeCalendar || {};
    const from = String(calendar.coverage_from || '').slice(0, 10);
    const through = String(calendar.coverage_through || '').slice(0, 10);
    if (calendar.authoritative && from && through && parts.ymd >= from && parts.ymd <= through) {
      const dates = calendar._dateSet || new Set(calendar.dates || []);
      calendar._dateSet = dates;
      return dates.has(parts.ymd);
    }
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

/**
 * Flex display quotes remain useful after the continuous auction ends: providers
 * publish the day's final ETF value before the daily-bars pipeline catches up.
 * This is deliberately separate from the action window used for trading actions.
 */
function getFlexQuoteWindow(date = new Date()) {
  const action = getAshareActionWindow(date);
  const p = action.parts || getShanghaiDateParts(date);
  const afterCloseMins = 15 * 60 + 16;
  if (action.inSession) return { active: true, phase: 'intraday', parts: p };
  if (action.tradingDay && p && p.minutes >= afterCloseMins) {
    return { active: true, phase: 'final', parts: p };
  }
  return { active: false, phase: 'eod', parts: p };
}

function dashboardDataRevision({ buildTime = null, updateTime = null, tradeDate = null } = {}) {
  return [updateTime, buildTime, tradeDate].filter(Boolean).join('|') || String(Date.now());
}

async function loadJSON(path, { bust = true, fresh = false } = {}) {
  let url = path;
  if (bust) {
    const token = fresh
      ? `${Date.now()}-${++dashboardState.freshRequestSequence}`
      : (dashboardState.cacheBust || String(Date.now()));
    url = path + (path.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(token);
  }
  const res = await fetch(url, fresh ? { cache: 'no-store' } : undefined);
  if (!res.ok) throw new Error('Failed to load ' + path);
  return await res.json();
}

async function resolveCacheBust() {
  try {
    const [info, latest] = await Promise.all([
      loadJSON('./data/build_info.json', { fresh: true }).catch(() => null),
      loadJSON('./data/latest.json', { fresh: true }).catch(() => null),
    ]);
    const buildTime = info?.build_time || null;
    const updateTime = latest?.update_time || latest?.as_of || null;
    if (buildTime || updateTime) {
      dashboardState.cacheBust = dashboardDataRevision({
        buildTime,
        updateTime,
        tradeDate: latest?.trade_date || null,
      });
      if (buildTime) dashboardState.lastBuildTime = buildTime;
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
      avix?.avix_realtime_quality_flags ? `数据质量: ${avix.avix_realtime_quality_flags}` : null,
      avix?.avix_realtime_source_quote_time ? `行情时间: ${avix.avix_realtime_source_quote_time}` : null,
      avix?.avix_realtime_fetch_time ? `抓取时间: ${avix.avix_realtime_fetch_time}` : null,
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
  if (mode === 'CLOSE_PENDING') {
    note.hidden = false;
    note.textContent = '收盘确认中';
    return;
  }
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

function renderQvixFallback(latest) {
  ensureRealtimeMeta();
  appendMetaItem('QVIX来源', 'qvixSource');
  const nowcast = latest?.nowcast || {};
  const source = String(nowcast.qvix_source || '');
  const delay = Number(nowcast.qvix_delay_minutes);
  let label = '--';
  if (source.includes('EASTMONEY_CFFEX_300INDEX_QVIX_DELAYED')) {
    label = `东财300股指期权复刻 · 延迟约${Number.isFinite(delay) ? delay : 15}分`;
  } else if (source) {
    label = source.includes('300ETF') ? '300ETF QVIX代理' : '300股指QVIX';
  }
  setText('qvixSource', label);
  const el = document.getElementById('qvixSource');
  if (el) {
    el.title = [
      source || null,
      nowcast.qvix_quote_time ? `盘口时间: ${nowcast.qvix_quote_time}` : null,
      nowcast.qvix_close != null ? `QVIX: ${formatRealtimeAvix(nowcast.qvix_close)}` : null,
    ].filter(Boolean).join(' | ') || '无盘中 QVIX 备用数据';
  }
}

function renderRealtimeIndexFactors(latest) {
  ensureRealtimeMeta();
  appendMetaItem('指数因子', 'realtimeIndexFactors');
  const nowcast = latest?.nowcast || {};
  const source = String(nowcast.realtime_index_source || '');
  const quoteTime = nowcast.realtime_index_quote_time;
  const fetchTime = nowcast.realtime_index_fetch_time;
  const realtimeSource = source.includes('EASTMONEY_INDEX_QUOTE_RT') || source.includes('TENCENT_INDEX_QUOTE_RT');
  const provider = source.includes('TENCENT') ? '腾讯备用' : '东财';
  const label = realtimeSource && quoteTime
    ? `沪深300/上证实时 · 因子已盘中重算 · ${provider}`
    : realtimeSource
      ? `指数因子已盘中重算 · 行情时点未核验 · ${provider}`
      : '上一正式收盘 · 因子未盘中重算';
  setText('realtimeIndexFactors', label);
  const el = document.getElementById('realtimeIndexFactors');
  if (el) {
    el.title = [
      source || null,
      nowcast.realtime_index_symbols || null,
      quoteTime ? `行情时间: ${quoteTime}` : '行情时间未验证',
      fetchTime ? `抓取时间: ${fetchTime}` : null,
    ].filter(Boolean).join(' | ') || '当前页面未使用实时指数因子';
  }
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
  const sourceDetail = [
    market.breadth_source,
    market.breadth_secondary_source ? `复核: ${market.breadth_secondary_source}` : null,
    market.breadth_source_score_delta != null ? `源差: ${Number(market.breadth_source_score_delta).toFixed(2)}` : null,
    market.advancing_ratio != null ? `上涨: ${(Number(market.advancing_ratio) * 100).toFixed(1)}%` : null,
    market.big_down_ratio != null ? `大跌: ${(Number(market.big_down_ratio) * 100).toFixed(1)}%` : null,
  ].filter(Boolean).join(' | ');
  el.title = market.breadth_quality
    ? `宽度质量: ${market.breadth_quality}${asOf}${sourceDetail ? ` | ${sourceDetail}` : ''}`
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
    confidenceEl.title = [
      confidence.coverage_score != null ? `数据覆盖: ${Number(confidence.coverage_score).toFixed(1)}%` : null,
      confidence.data_quality_score != null ? `数据质量: ${Number(confidence.data_quality_score).toFixed(1)}%` : null,
      confidence.missing_components ? `缺失或降级: ${confidence.missing_components}` : '主要模型输入完整',
    ].filter(Boolean).join(' | ');
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
  renderQvixFallback(latest);
  renderRealtimeIndexFactors(latest);
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
  const reset = document.getElementById('flexResetLedgerBtn');
  if (reset) {
    reset.disabled = sim;
    reset.hidden = sim;
    reset.title = sim ? '模拟仓由策略自动维护' : '清空真实仓';
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

async function loadCriticalDashboardData({ fresh = false } = {}) {
  if (!dashboardState.cacheBust) {
    await resolveCacheBust();
  }
  const [latest, history, tradeCalendar, nowcastHistory, intradayTemperature, components, audit] = await Promise.all([
    loadJSON('./data/latest.json', { fresh }),
    loadJSON('./data/history.json', { fresh }),
    loadJSON('./data/trade_calendar.json', { fresh }).catch(() => ({ status: 'missing', dates: [] })),
    loadJSON('./data/nowcast_history.json', { fresh }).catch(() => ({ status: 'missing', rows: [], gaps: [] })),
    loadJSON('./data/intraday_temperature.json', { fresh }).catch(() => ({ status: 'no_samples', rows: [] })),
    loadJSON('./data/components.json', { fresh }),
    loadJSON('./data/audit.json', { fresh }),
  ]);
  return { latest, history, tradeCalendar, nowcastHistory, intradayTemperature, components, audit };
}

async function loadHeavyDashboardData({ fresh = false } = {}) {
  const [strategy, rtTactical, stagePlaybook, etfMarks] = await Promise.all([
    loadJSON('./data/strategy.json', { fresh }).catch(() => ({ status: 'missing' })),
    loadJSON('./data/rt_tactical.json', { fresh }).catch(() => ({ status: 'missing' })),
    loadJSON('./data/stage_playbook.json', { fresh }).catch(() => ({ status: 'missing' })),
    loadJSON('./data/etf_daily_marks.json', { fresh }).catch(() => ({ status: 'missing', by_code: {} })),
  ]);
  dashboardState.heavyLoaded = true;
  dashboardState.etfMarks = etfMarks && etfMarks.status !== 'missing'
    ? etfMarks
    : { status: 'missing', by_code: {}, policy: 'SIM_ENTRY_OPEN_MARK_CLOSE' };
  return { strategy, rtTactical, stagePlaybook, etfMarks: dashboardState.etfMarks };
}

function renderTemperatureCoreTailSignal(payload) {
  const container = document.getElementById('temperatureCoreTailSignal');
  if (!container) return;
  const signal = payload?.core_tail_signal || {};
  const summary = payload?.core_tail_day_summary || {};
  const state = flexCoreTailState(signal);
  const count = Number(signal.consecutive_samples) || 0;
  const required = Number(signal.policy?.stable_samples) || 3;
  const executeTime = String(summary.execute_at || '').slice(11, 16);
  const latestState = String(signal.latest_sample_state || '');
  const degraded = !!signal.degraded || latestState === 'DEGRADED_PASS';
  const uncertainty = signal.conditions?.uncertainty || signal.degraded_uncertainty || {};
  const lower = Number(uncertainty.risk_temperature_lower);
  const upper = Number(uncertainty.risk_temperature_upper);
  const rangeLabel = Number.isFinite(lower) && Number.isFinite(upper)
    ? `RT边界 ${lower.toFixed(1)}-${upper.toFixed(1)}`
    : '缺失边界不可量化';
  let displayState = 'inactive';
  let status = '';
  let detail = '';

  if (flexCoreTailActionableNow(signal)) {
    displayState = degraded ? 'degraded_execute' : 'execute';
    status = degraded ? '降级稳健 · 14:50-15:00 买入 510300' : '14:50-15:00 买入 510300';
    detail = degraded
      ? `${rangeLabel} 全部落在买入区间；仅执行 CORE，卫星及其他信号仍为 T+1。`
      : '严格条件与质量门槛均通过；仅执行 CORE，卫星及其他信号仍为 T+1。';
  } else if (state === 'prepare') {
    displayState = degraded ? 'degraded_prepare' : 'prepare';
    status = degraded ? 'CORE 降级稳健条件已稳定' : 'CORE 条件已稳定';
    detail = `${degraded ? `${rangeLabel}；` : ''}连续有效采样 ${count} 次；14:50 后须用新鲜有效样本再次确认。`;
  } else if (state === 'confirming' && signal.candidate) {
    displayState = degraded ? 'degraded_confirming' : 'confirming';
    status = `CORE ${degraded ? '降级稳健' : '候选'}确认 ${count}/${required}`;
    detail = degraded
      ? `${rangeLabel} 全部通过，但稳定性尚未满足，当前不可提前买入。`
      : '本次严格条件通过，但稳定性尚未满足，当前不可提前买入。';
  } else if (state === 'data_wait' && (signal.stable || count > 0)) {
    displayState = 'data_wait';
    status = latestState === 'INDETERMINATE'
      ? 'CORE 边界不确定，暂停确认'
      : 'CORE 数据无法量化，暂停确认';
    detail = `${latestState === 'INDETERMINATE' ? `${rangeLabel} 跨过买入边界；` : ''}本次样本已跳过，不增加也不清零；保留连续有效采样 ${count} 次。`;
  } else if (summary.execute_triggered) {
    displayState = 'recorded';
    status = `当日 ${executeTime || '--:--'} 已触发${summary.execute_degraded ? '降级稳健' : ''}买入 510300`;
    detail = '这是轨迹中的已发生信号记录，不代表当前尾盘窗口仍可下单。';
  }

  container.hidden = displayState === 'inactive';
  container.dataset.state = displayState;
  setText('temperatureCoreTailStatus', status);
  setText('temperatureCoreTailDetail', detail);
}

function renderIntradayTemperaturePanel(payload) {
  const meta = document.getElementById('intradayTemperatureMeta');
  const note = document.getElementById('intradayTemperatureNote');
  const rows = payload?.rows || [];
  const eligibleCount = Number.isFinite(Number(payload?.eligible_count))
    ? Number(payload.eligible_count)
    : rows.filter(row => row.plot_eligible !== false && !String(row.quality || '').includes('WARN_BREADTH_MISSING')).length;
  const excludedCount = Math.max(0, rows.length - eligibleCount);
  renderTemperatureCoreTailSignal(payload);
  if (meta) {
    if (!rows.length) {
      meta.textContent = '暂无采样';
    } else {
      const last = rows[rows.length - 1];
      meta.textContent = [
        payload.trade_date,
        `${rows.length}点`,
        excludedCount ? `${eligibleCount}点有效` : null,
        last.time,
        payload.has_final ? '已收盘' : '盘中',
      ].filter(Boolean).join(' · ');
    }
  }
  if (note) {
    note.textContent = excludedCount
      ? `刷新采样 · 非逐笔实时 · ${excludedCount}个A股宽度缺失点已剔除 · 有效点连续绘制`
      : '刷新采样 · 非逐笔实时';
  }
  const dom = document.getElementById('intradayTemperatureChart');
  if (!dom) return;
  const previous = echarts.getInstanceByDom(dom);
  if (previous) previous.dispose();
  dashboardState.intradayTemperatureChart = renderIntradayTemperatureChart(payload || { rows: [] });
}

function renderCriticalDashboard({ latest, history, tradeCalendar, nowcastHistory, intradayTemperature, components, audit }) {
  document.body.classList.remove('error');
  hideLoadError();
  dashboardState.latest = latest || null;
  dashboardState.tradeCalendar = tradeCalendar || { status: 'missing', dates: [] };
  dashboardState.intradayTemperature = intradayTemperature || {};
  renderLatest(latest);
  renderAudit(audit);
  renderNowcastGapSummary(nowcastHistory);
  renderIntradayTemperaturePanel(intradayTemperature);
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
  if (dashboardState.flexPlaybook) renderFlexTradePanel(dashboardState.flexPlaybook);
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
const FLEX_ONE_WAY_COST_RATE = 0.0001;
const FLEX_ETF_LOT_SIZE = 100;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function flexCoreTailIsFresh(signal) {
  if (!signal?.valid_until) return false;
  const expiry = Date.parse(signal.valid_until);
  return Number.isFinite(expiry) && Date.now() <= expiry;
}

function flexCoreTailWindowOpenNow(signal) {
  if (!signal?.trade_date) return false;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date()).filter(part => part.type !== 'literal').map(part => [part.type, part.value]));
  const date = `${parts.year}-${parts.month}-${parts.day}`;
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  return date === String(signal.trade_date).slice(0, 10) && minute >= 14 * 60 + 50 && minute < 15 * 60;
}

function flexCoreTailActionableNow(signal) {
  return !!signal?.actionable
    && signal.status === 'EXECUTE'
    && flexCoreTailIsFresh(signal)
    && flexCoreTailWindowOpenNow(signal);
}

function flexCoreTailState(signal) {
  if (!signal || signal.status === 'NO_SAMPLE') return 'inactive';
  if (!flexCoreTailIsFresh(signal) && !['FINAL', 'WINDOW_CLOSED'].includes(signal.status)) return 'stale';
  if (signal.status === 'EXECUTE' && !flexCoreTailWindowOpenNow(signal)) return 'window_closed';
  return String(signal.status || 'INACTIVE').toLowerCase();
}

function renderFlexCoreTailAlert(signal) {
  const alert = document.getElementById('flexCoreTailAlert');
  if (!alert) return;
  const tail = signal || {};
  const state = flexCoreTailState(tail);
  const actionable = flexCoreTailActionableNow(tail);
  const status = state === 'stale'
    ? '盘中信号已过期，禁止尾盘执行'
    : state === 'window_closed'
      ? '15:00尾盘窗口已结束，回退T+1'
      : (tail.status_cn || '等待盘中采样');
  const values = tail.conditions?.values || {};
  const checks = tail.conditions?.checks || {};
  const rt = Number(values.risk_temperature);
  const dd = Number(values.hs300_drawdown_60d);
  const coverage = Number(values.model_coverage_score);
  const dataQuality = Number(values.model_data_quality_score);
  const qualityLabel = Number.isFinite(dataQuality) && Number.isFinite(coverage)
    ? `${dataQuality.toFixed(1)}·覆盖${coverage.toFixed(0)}`
    : '—';
  const stockBreadth = values.breadth_mode === 'STOCK_A' && String(values.breadth_quality || '').startsWith('OK');
  const sampleCount = Number(tail.consecutive_samples) || 0;
  const required = Number(tail.policy?.stable_samples) || 3;
  const latestState = String(tail.latest_sample_state || '');
  const sequenceDegraded = !!tail.degraded || latestState === 'DEGRADED_PASS';
  const boundedLatest = ['DEGRADED_PASS', 'DEGRADED_FAIL', 'INDETERMINATE'].includes(latestState);
  const degraded = ['execute', 'prepare', 'confirming'].includes(state)
    ? sequenceDegraded
    : boundedLatest;
  const uncertainty = tail.conditions?.uncertainty || (degraded ? tail.degraded_uncertainty : null) || {};
  const lower = Number(uncertainty.risk_temperature_lower);
  const upper = Number(uncertainty.risk_temperature_upper);
  const rangeLabel = Number.isFinite(lower) && Number.isFinite(upper)
    ? `${lower.toFixed(1)}-${upper.toFixed(1)}`
    : '—';
  const fullQualityPass = !!(
    checks.confidence_present
    && checks.coverage
    && checks.data_quality
    && checks.allowed_degradations
    && checks.intraday_mode
  );
  const checkRows = [
    ['RT', degraded ? `边界${rangeLabel}` : (Number.isFinite(rt) ? rt.toFixed(1) : '—'), degraded ? 'warn' : (checks.risk_temperature ? 'pass' : 'fail')],
    ['回撤', Number.isFinite(dd) ? `${(dd * 100).toFixed(1)}%` : '—', checks.hs300_drawdown_60d ? 'pass' : 'fail'],
    ['质量', degraded ? `稳健降级·覆盖${Number.isFinite(coverage) ? coverage.toFixed(0) : '—'}` : qualityLabel, degraded ? 'warn' : (fullQualityPass ? 'pass' : 'fail')],
    ['宽度', stockBreadth ? '全A' : (degraded ? '缺失·已计边界' : '无效'), degraded && !stockBreadth ? 'warn' : (checks.stock_breadth ? 'pass' : 'fail')],
    ['稳定', tail.stable ? `已稳${sampleCount}次` : `${sampleCount}/${required}`, tail.stable ? 'pass' : 'fail'],
  ];
  alert.dataset.state = state;
  setText('flexCoreTailStatus', status);
  setText('flexCoreTailTime', tail.last_sample_at ? `采样 ${String(tail.last_sample_at).slice(11, 16)}` : '');
  const detail = document.getElementById('flexCoreTailDetail');
  if (detail) {
    if (actionable) {
      detail.textContent = degraded
        ? `缺失因子按完整极端范围复算后，RT仍在${rangeLabel}；仅买入510300并在15:00前记实际成交价。其他信号仍为T+1。`
        : '仅买入沪深300核心仓（510300）；请在15:00前按实际成交价记账。卫星及其他信号仍为T+1。';
    } else if (state === 'prepare') {
      detail.textContent = '严格条件已连续稳定；14:50后须再次看到“尾盘执行窗口”才可买入。其他信号仍为T+1。';
    } else if (state === 'confirming') {
      detail.textContent = `正在连续确认，至少需要${required}次且覆盖15分钟；确认完成前不提前买入。`;
    } else if (state === 'data_wait') {
      detail.textContent = latestState === 'INDETERMINATE'
        ? `缺失因子的RT可能范围${rangeLabel}跨过买入边界；本次跳过，不增加也不清零，保留${sampleCount}次。`
        : `本次采样无法量化而跳过，不增加也不清零；当前连续有效样本${sampleCount}次。等待新鲜有效数据。`;
    } else if (latestState === 'DEGRADED_FAIL') {
      detail.textContent = `缺失因子按完整极端范围复算后仍不满足买入条件（RT边界${rangeLabel}）；连续确认已清零。`;
    } else {
      detail.textContent = tail.fallback_cn || '仅严格条件连续稳定后启用；其他信号仍按 T+1 开盘执行。';
    }
  }
  const checksEl = document.getElementById('flexCoreTailChecks');
  if (checksEl) {
    checksEl.innerHTML = checkRows.map(([label, value, tone]) =>
      `<span class="flex-tail-check ${tone}">${escapeHtml(label)} ${escapeHtml(value)}</span>`
    ).join('');
  }

  const dock = document.getElementById('dockFlex');
  const badge = document.getElementById('dockFlexTailBadge');
  const attention = flexCoreTailIsFresh(tail) && ['confirming', 'prepare', 'execute', 'data_wait'].includes(state);
  if (dock) dock.dataset.tailState = attention ? state : 'inactive';
  if (badge) badge.hidden = !attention;
  if (!document.documentElement.dataset.baseTitle) {
    document.documentElement.dataset.baseTitle = document.title;
  }
  document.title = actionable
    ? `CORE尾盘执行 | ${document.documentElement.dataset.baseTitle}`
    : document.documentElement.dataset.baseTitle;
}

function flexWithCoreTailSignal(flex, signal) {
  const base = flex || {};
  const tail = signal || {};
  const copy = { ...base, core_tail_signal: tail };
  if (!flexCoreTailActionableNow(tail)) return copy;

  const satOpen = String(base.position_state?.satellite?.status || '') === 'open'
    || !!base.satellite?.active;
  const target = satOpen ? 0.6 : 1.0;
  const action = {
    sleeve: 'core',
    name: '沪深300',
    action: 'OPEN',
    action_cn: 'CORE严格尾盘买入',
    side: 'OPEN',
    side_cn: '买入',
    priority: 'P0',
    entry: 'T日14:50-15:00',
    exit: '保持原T+1核心退出日程',
    hold_days: 6,
    weight_target: target,
    weight_hint: `${Math.round(target * 100)}%`,
    why: 'CORE严格条件连续3次盘中采样稳定通过',
    etf_code: '510300',
    etf_name: base.core?.etf_name || '沪深300ETF华泰柏瑞',
    signal_as_of: tail.trade_date,
    execution_mode: 'T_TAIL_1450',
    entry_price_type: 'tail_realtime',
    tail_signal_status: tail.status,
  };
  const withoutCoreOpen = (base.buy_list || []).filter(item => {
    const isCore = String(item.sleeve || '') === 'core' || String(item.etf_code || '') === '510300';
    return !(isCore && FLEX_BUY_ACTIONS.has(String(item.action || item.side || '').toUpperCase()));
  });
  copy.buy_list = [action, ...withoutCoreOpen];
  copy.minimal_actions = [
    action,
    ...(base.minimal_actions || []).filter(item => String(item.etf_code || '') !== '510300'),
  ];
  copy.core = {
    ...(base.core || {}),
    active: true,
    action: 'OPEN',
    action_cn: '尾盘买入',
    detail: tail.execution_cn,
    execution_mode: 'T_TAIL_1450',
  };
  return copy;
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
    version: 4,
    book: book === 'sim' ? 'sim' : 'real',
    capital: 0,
    cash: 0,
    positions: {},
    journal: [],
    updated_at: null,
    strategy_as_of: null,
    risk_exits: {},
    pending_rebalance: null,
    pending_orders: {},
  };
}

function flexOpenPositions(ledger) {
  // Sim pending-price sleeves may reserve capital with qty=0; keep them auditable.
  return Object.values(ledger?.positions || {}).filter(p => {
    if (Number(p.qty) > 1e-9) return true;
    return Number(p.cost_basis) > 0 && String(p.mark_quality || '').startsWith('MISSING');
  });
}

function flexMarkMissing(pos) {
  return String(pos?.mark_quality || '').startsWith('MISSING');
}

function flexDeployedCost(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => sum + (Number(p.cost_basis) || 0), 0);
}

function flexMarkValue(ledger) {
  return flexOpenPositions(ledger).reduce((sum, p) => {
    // Never invent P&L from unit-price fallback when EOD marks are missing.
    if (flexMarkMissing(p)) {
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
    version: 4,
    book: raw?.book === 'sim' || book === 'sim' ? 'sim' : 'real',
    capital: Number(raw?.capital) || 0,
    cash: raw?.cash,
    positions: raw?.positions && typeof raw.positions === 'object' ? { ...raw.positions } : {},
    journal: Array.isArray(raw?.journal) ? raw.journal : [],
    updated_at: raw?.updated_at || null,
    strategy_as_of: raw?.strategy_as_of || null,
    risk_exits: raw?.risk_exits && typeof raw.risk_exits === 'object' ? { ...raw.risk_exits } : {},
    pending_rebalance: raw?.pending_rebalance && typeof raw.pending_rebalance === 'object'
      ? JSON.parse(JSON.stringify(raw.pending_rebalance))
      : null,
    pending_orders: raw?.pending_orders && typeof raw.pending_orders === 'object'
      ? JSON.parse(JSON.stringify(raw.pending_orders))
      : {},
  };
  if (ledger.cash == null || !Number.isFinite(Number(ledger.cash))) {
    // Best-effort migration for pre-v2 books.
    ledger.cash = Math.max(0, ledger.capital - flexDeployedCost(ledger));
  } else {
    ledger.cash = Number(ledger.cash);
  }
  // Backend/saved exit dates are authoritative. Derive only when an old row has none.
  for (const key of Object.keys(ledger.positions || {})) {
    const p = ledger.positions[key];
    if (!p || !(Number(p.qty) > 1e-9)) continue;
    if (!p.exit_date && p.buy_date && p.hold_days != null && Number.isFinite(Number(p.hold_days))) {
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
 * position_state is authoritative. Advisory AVOID rows must not rewrite an
 * already-open paper sleeve before the engine emits a real close transition.
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
    targets.push(row);
  }

  const sat = pos.satellite || {};
  if (String(sat.status || '') === 'open') {
    let wSleeve = Number(alloc.w_sat);
    if (!(wSleeve > 0)) wSleeve = Number(cfg.sat_when_signal != null ? cfg.sat_when_signal : 0.4);
    const names = Array.isArray(sat.names) ? sat.names : [];
    const weights = sat.weights || {};
    const kept = names.slice();
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
  // Entry prices may move forward to the next available bar, never backward.
  return null;
}

/**
 * Latest ETF EOD session we can actually mark to (max bar date in etf_daily_marks).
 * This is "上一交易日收盘" when market is closed / before today' s EOD lands.
 */
function flexEtfMarksCoverage(positionCodes = []) {
  const marks = dashboardState.etfMarks || {};
  const by = marks.by_code || {};
  const requested = (positionCodes || [])
    .map(code => String(code || '').replace(/\D/g, '').padStart(6, '0'))
    .filter(code => /^\d{6}$/.test(code));
  const codes = requested.length ? [...new Set(requested)] : Object.keys(by);
  const fileAsOf = marks.as_of ? String(marks.as_of).slice(0, 10) : null;
  let commonDates = null;
  const staleCodes = [];
  const missingCodes = [];
  for (const code of codes) {
    const bars = by[code]?.bars || {};
    const dates = Object.keys(bars).filter(day => !fileAsOf || day <= fileAsOf).sort();
    if (!dates.length) {
      missingCodes.push(code);
      continue;
    }
    if (fileAsOf && dates[dates.length - 1] < fileAsOf) staleCodes.push(code);
    const set = new Set(dates);
    commonDates = commonDates == null ? set : new Set([...commonDates].filter(day => set.has(day)));
  }
  const common = commonDates ? [...commonDates].sort() : [];
  // A portfolio-level mark/risk check must use one session shared by every holding.
  const session = common.length && !missingCodes.length ? common[common.length - 1] : null;
  return {
    file_as_of: fileAsOf,
    complete_as_of: session,
    session,
    codes,
    stale_codes: staleCodes,
    missing_codes: missingCodes,
    policy: marks.policy || null,
  };
}

/**
 * Effective mark date for holdings P&L.
 * Always prefer last available EOD close — never invent "today" nowcast without bars.
 * Strategy as_of only controls which paper positions exist. It must never delay
 * valuation or a local stop/target check once a newer EOD ETF bar is available.
 */
function flexEffectiveMarkDate(positionCodes = []) {
  const cov = flexEtfMarksCoverage(positionCodes);
  if (cov.session) return cov.session;
  try {
    const td = dashboardState.latest?.trade_date || dashboardState.lastTradeDate;
    if (td) return String(td).slice(0, 10);
  } catch (_) { /* ignore */ }
  return flexDateCn(0);
}

/**
 * Remount open positions to last EOD close for display / totals.
 * Does not rewrite cost/avg (real fills stay). Safe for both real + sim books.
 */
function flexApplyEodMarksToLedger(ledger) {
  const L = normalizeFlexLedger(JSON.parse(JSON.stringify(ledger || {})));
  const positionCodes = flexOpenPositions(L).map(pos => pos.etf_code).filter(Boolean);
  const coverage = flexEtfMarksCoverage(positionCodes);
  const markDate = coverage.session;
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
    const bar = markDate ? flexEtfBarLookup(code, markDate, { prefer: 'exact' }) : null;
    if (bar && Number(bar.close) > 0) {
      pos.last_price = Number(bar.close);
      pos.eod_last_price = Number(bar.close);
      pos.eod_mark_bar_date = bar.trade_date;
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
  L.mark_requested_as_of = coverage.file_as_of;
  L.mark_stale_codes = coverage.stale_codes;
  L.mark_missing_codes = coverage.missing_codes;
  L.mark_policy = 'EOD_LAST_SESSION_CLOSE';
  L.mark_policy_cn = '盯市=最近可得交易日收盘（休市=上一交易日截止）';
  L._eod_mark_stats = { marked, missing, mark_date: markDate };
  return L;
}

function flexRealtimeSymbol(code) {
  const c = String(code || '').replace(/\D/g, '').padStart(6, '0');
  if (!/^\d{6}$/.test(c)) return null;
  return c.startsWith(('15')) || c.startsWith(('16')) || c.startsWith(('18')) ? `sz${c}` : `sh${c}`;
}

function flexQuoteTimestamp(ts) {
  const raw = String(ts || '');
  const m = raw.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/);
  if (!m) return null;
  return {
    tradeDate: `${m[1]}-${m[2]}-${m[3]}`,
    label: `${m[4]}:${m[5]}:${m[6]}`,
    raw,
  };
}

function flexParseTencentQuotes(text, expectedCodes) {
  const expected = new Set((expectedCodes || []).map(c => String(c).replace(/\D/g, '').padStart(6, '0')));
  const quotes = {};
  const today = getShanghaiDateParts().ymd;
  const re = /v_(?:sh|sz)(\d{6})="([^"]*)"/g;
  let match;
  while ((match = re.exec(String(text || '')))) {
    const code = match[1];
    if (!expected.has(code)) continue;
    const f = match[2].split('~');
    const price = Number(f[3]);
    const previousClose = Number(f[4]);
    const stamp = flexQuoteTimestamp(f[30]);
    if (!(price > 0) || !(previousClose > 0) || !stamp || stamp.tradeDate !== today) continue;
    quotes[code] = {
      price,
      previous_close: previousClose,
      quote_at: stamp.label,
      quote_date: stamp.tradeDate,
      source: 'TENCENT',
    };
  }
  return quotes;
}

async function flexFetchEastmoneyQuote(code) {
  const c = String(code || '').replace(/\D/g, '').padStart(6, '0');
  const market = c.startsWith(('15')) || c.startsWith(('16')) || c.startsWith(('18')) ? '0' : '1';
  const url = `https://push2.eastmoney.com/api/qt/stock/get?secid=${market}.${c}&fields=f43,f57,f60,f124`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Eastmoney ${res.status}`);
  const data = (await res.json())?.data || {};
  const price = Number(data.f43) / 100;
  const previousClose = Number(data.f60) / 100;
  const epochSeconds = Number(data.f124);
  const quoteDate = new Date(epochSeconds * 1000);
  if (!(price > 0) || !(previousClose > 0) || !Number.isFinite(epochSeconds) || Number.isNaN(quoteDate.getTime())) {
    throw new Error('Eastmoney invalid quote');
  }
  const now = getShanghaiDateParts();
  const stamp = getShanghaiDateParts(quoteDate);
  const phase = getFlexQuoteWindow().phase;
  // f124 is the provider quote timestamp. A same-day final quote remains valid
  // after close; intraday data must also be recent enough for a live mark.
  if (!FlexExecutionCore.quoteTimestampIsUsable({
    quoteEpochSeconds: epochSeconds,
    nowMs: Date.now(),
    quoteYmd: stamp.ymd,
    todayYmd: now.ymd,
    phase,
  })) {
    throw new Error('Eastmoney stale quote');
  }
  return {
    price,
    previous_close: previousClose,
    quote_at: `${String(stamp.hour).padStart(2, '0')}:${String(stamp.minute).padStart(2, '0')}:00`,
    quote_date: stamp.ymd,
    provider_timestamp: epochSeconds,
    source: 'EASTMONEY',
  };
}

function flexRealtimeQuoteCodes() {
  const codes = new Set();
  for (const book of ['real', 'sim']) {
    for (const pos of flexOpenPositions(loadFlexLedgerForBook(book))) {
      const code = String(pos.etf_code || '').replace(/\D/g, '').padStart(6, '0');
      if (/^\d{6}$/.test(code)) codes.add(code);
    }
  }
  return [...codes].sort();
}

async function flexRefreshRealtimeQuotes() {
  const quoteWindow = getFlexQuoteWindow();
  const codes = flexRealtimeQuoteCodes();
  if (!quoteWindow.active || !codes.length || dashboardState.flexRealtimeQuoteInFlight) return;
  const snapshot = dashboardState.flexRealtimeQuotes || {};
  if (Date.now() - (Number(snapshot.fetchedAt) || 0) < FLEX_REALTIME_QUOTE_TTL_MS) return;
  dashboardState.flexRealtimeQuoteInFlight = true;
  try {
    const symbols = codes.map(flexRealtimeSymbol).filter(Boolean);
    let quotes = {};
    try {
      const res = await fetch(`https://qt.gtimg.cn/q=${symbols.join(',')}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`Tencent ${res.status}`);
      quotes = flexParseTencentQuotes(await res.text(), codes);
    } catch (err) {
      console.warn('Flex Tencent realtime quotes unavailable', err);
    }
    const missing = codes.filter(code => !quotes[code]);
    if (missing.length) {
      const settled = await Promise.allSettled(missing.map(async code => [code, await flexFetchEastmoneyQuote(code)]));
      for (const row of settled) {
        if (row.status === 'fulfilled') quotes[row.value[0]] = row.value[1];
      }
    }
    dashboardState.flexRealtimeQuotes = {
      quotes,
      source: Object.values(quotes).some(q => q.source === 'TENCENT') ? 'TENCENT' : (Object.keys(quotes).length ? 'EASTMONEY' : null),
      fetchedAt: Date.now(),
    };
    if (document.body?.dataset?.appView === 'flex') renderFlexExecUi();
  } finally {
    dashboardState.flexRealtimeQuoteInFlight = false;
  }
}

function flexEnsureRealtimeQuotePolling() {
  flexRefreshRealtimeQuotes();
  if (dashboardState.flexRealtimeQuoteTimer) return;
  dashboardState.flexRealtimeQuoteTimer = setInterval(() => {
    if (!document.hidden && document.body?.dataset?.appView === 'flex') flexRefreshRealtimeQuotes();
  }, FLEX_REALTIME_POLL_MS);
}

/** Apply ephemeral intraday prices for display only. It never writes local storage. */
function flexApplyRealtimeMarksToLedger(ledger) {
  const L = normalizeFlexLedger(JSON.parse(JSON.stringify(ledger || {})));
  const quoteWindow = getFlexQuoteWindow();
  const snapshot = dashboardState.flexRealtimeQuotes || {};
  if (!quoteWindow.active || Date.now() - (Number(snapshot.fetchedAt) || 0) > FLEX_REALTIME_QUOTE_TTL_MS * 2) return L;
  const quotes = snapshot.quotes || {};
  let marked = 0;
  let latestAt = null;
  let source = null;
  Object.values(L.positions || {}).forEach(pos => {
    const code = String(pos?.etf_code || '').replace(/\D/g, '').padStart(6, '0');
    const q = quotes[code];
    if (!q || !(Number(q.price) > 0)) return;
    pos.realtime_price = Number(q.price);
    pos.realtime_quote_at = q.quote_at;
    pos.realtime_quote_date = q.quote_date;
    pos.realtime_quote_source = q.source;
    pos.last_price = Number(q.price);
    pos.mark_price_type = 'realtime';
    pos.mark_quality = 'REALTIME';
    marked += 1;
    latestAt = q.quote_at || latestAt;
    source = q.source || source;
  });
  L._realtime_mark_stats = { marked, latest_at: latestAt, source };
  L._realtime_mark_stats.phase = quoteWindow.phase;
  return L;
}

function flexApplyDisplayMarksToLedger(ledger) {
  return flexApplyRealtimeMarksToLedger(flexApplyEodMarksToLedger(ledger));
}

function flexExecutionReferencePrice(ledger, key) {
  const marked = flexApplyDisplayMarksToLedger(ledger);
  const displayPos = marked.positions?.[key];
  const rawPos = ledger?.positions?.[key];
  const candidates = [
    displayPos?.last_price,
    displayPos?.eod_last_price,
    rawPos?.last_price,
    rawPos?.avg_price,
  ];
  return FlexExecutionCore.firstPositivePrice(candidates);
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

function flexSimJournal(journal, entry) {
  return [{
    id: flexUid('jn'),
    ts: entry.ts || new Date().toISOString(),
    trade_date: entry.trade_date || null,
    ...entry,
  }, ...(journal || [])].slice(0, 200);
}

function flexSimTradeTimestamp(tradeDate, phase = 'open') {
  const time = phase === 'close' ? '15:00:00' : '09:30:00';
  return tradeDate ? `${tradeDate}T${time}+08:00` : new Date().toISOString();
}

function flexSimEntryBar(target) {
  const day = String(target?.buy_date || '').slice(0, 10);
  const bar = flexEtfBarLookup(target?.etf_code, day, { prefer: 'exact' });
  return bar?.trade_date === day && Number(bar.open) > 0 ? bar : null;
}

function flexSimExecutePendingRebalance(ledger) {
  const order = ledger.pending_rebalance;
  if (!order?.execution_date || !Array.isArray(order.targets)) return ledger;
  const targetByKey = new Map(order.targets.map(target => [target.key, target]));
  const relevant = new Set([...Object.keys(ledger.positions || {}), ...targetByKey.keys()]);
  const bars = new Map();
  for (const key of relevant) {
    const target = targetByKey.get(key);
    const pos = ledger.positions[key];
    const code = target?.etf_code || pos?.etf_code;
    const bar = flexEtfBarLookup(code, order.execution_date, { prefer: 'exact' });
    if (!bar || bar.trade_date !== order.execution_date || !(Number(bar.open) > 0)) return ledger;
    bars.set(key, bar);
  }

  const equityAtOpen = Number(ledger.cash) + [...relevant].reduce((sum, key) => {
    const pos = ledger.positions[key];
    return sum + (Number(pos?.qty) || 0) * Number(bars.get(key)?.open || 0);
  }, 0);

  // Sells fund buys. Each leg is rounded to an ETF lot and charged 1bp.
  for (const [key, pos] of Object.entries(ledger.positions)) {
    const target = targetByKey.get(key);
    if (!target || !(Number(pos.qty) > 0)) continue;
    const price = Number(bars.get(key).open);
    const desiredQty = Math.floor((equityAtOpen * Number(target.weight || 0)) / (price * FLEX_ETF_LOT_SIZE)) * FLEX_ETF_LOT_SIZE;
    const sellQty = Math.max(0, Number(pos.qty) - desiredQty);
    if (!(sellQty >= FLEX_ETF_LOT_SIZE)) continue;
    const gross = sellQty * price;
    const fee = gross * FLEX_ONE_WAY_COST_RATE;
    const net = gross - fee;
    const costRemoved = Number(pos.cost_basis) * sellQty / Number(pos.qty);
    const pnl = net - costRemoved;
    ledger.cash += net;
    pos.qty -= sellQty;
    pos.cost_basis -= costRemoved;
    pos.avg_price = pos.qty > 0 ? pos.cost_basis / pos.qty : 0;
    pos.last_price = price;
    ledger.journal = flexSimJournal(ledger.journal, {
      type: 'REDUCE', type_cn: '模拟调仓减', name: pos.name, etf_code: pos.etf_code,
      amount: gross, net_amount: net, price, qty: sellQty, fee, pnl,
      cost_removed: costRemoved, trade_date: order.execution_date,
      ts: flexSimTradeTimestamp(order.execution_date, 'open'),
      note: `目标权重 ${pctLabel(target.weight)} · 信号 ${order.signal_date}`,
    });
  }

  for (const target of order.targets) {
    const pos = ledger.positions[target.key];
    if (!pos || !(Number(pos.qty) > 0)) continue;
    const price = Number(bars.get(target.key).open);
    const currentValue = Number(pos.qty) * price;
    const gap = equityAtOpen * Number(target.weight || 0) - currentValue;
    if (!(gap > price * FLEX_ETF_LOT_SIZE)) continue;
    const buy = flexBuyOrderFromBudget(gap, price, ledger.cash);
    if (!(buy.qty >= FLEX_ETF_LOT_SIZE)) continue;
    const newQty = Number(pos.qty) + buy.qty;
    const newCost = Number(pos.cost_basis) + buy.cash_required;
    ledger.cash -= buy.cash_required;
    pos.qty = newQty;
    pos.cost_basis = newCost;
    pos.avg_price = newCost / newQty;
    pos.last_price = price;
    ledger.journal = flexSimJournal(ledger.journal, {
      type: 'ADD', type_cn: '模拟调仓加', name: pos.name, etf_code: pos.etf_code,
      amount: buy.gross, price, qty: buy.qty, fee: buy.fee,
      trade_date: order.execution_date, ts: flexSimTradeTimestamp(order.execution_date, 'open'),
      note: `目标权重 ${pctLabel(target.weight)} · 信号 ${order.signal_date}`,
    });
  }
  for (const target of order.targets) {
    if (ledger.positions[target.key]) ledger.positions[target.key].target_weight = Number(target.weight) || 0;
  }
  ledger.pending_rebalance = null;
  return ledger;
}

/**
 * Incremental, read-only simulation ledger.
 * Existing fills are immutable; only authoritative position_state membership
 * changes create trades. This prevents target refreshes from repricing history.
 */
function rebuildSimLedgerFromStrategy(flex) {
  const f = flex || {};
  const asOf = String(f.as_of || f.market_state?.trade_date || '').slice(0, 10);
  let targets = collectStrategyPaperTargets(f);
  let targetByKey = new Map(targets.map(target => [flexSimPositionKey(target), target]));
  const raw = loadFlexLedgerForBook('sim');
  let capital = Number(raw.capital) || Number(loadFlexLedgerForBook('real').capital) || 0;

  // v3 simulation books were reconstructed from historical prices. Start one
  // clean v4 baseline instead of carrying irreconcilable synthetic deltas.
  let ledger = Number(raw.version) >= 4 ? normalizeFlexLedger(raw, 'sim') : defaultFlexLedger('sim');
  if (Number(raw.version) < 4) {
    ledger.capital = capital;
    ledger.cash = capital;
    ledger.journal = flexSimJournal([], {
      type: 'MIGRATE',
      type_cn: '模拟账本升级',
      name: '策略纸面',
      amount: capital,
      price: 0,
      qty: 0,
      trade_date: asOf,
      ts: flexSimTradeTimestamp(asOf, 'close'),
      note: '升级为增量记账；旧版追溯重建流水未继承',
    });
  }
  ledger.version = 4;
  ledger.book = 'sim';
  ledger.capital = capital;
  ledger.positions = { ...(ledger.positions || {}) };
  ledger.risk_exits = { ...(ledger.risk_exits || {}) };
  ledger.journal = Array.isArray(ledger.journal) ? ledger.journal.slice(0, 200) : [];
  ledger.cash = Number.isFinite(Number(ledger.cash)) ? Number(ledger.cash) : capital;

  const satSignalId = String(f.position_state?.satellite?.entry_signal_date || '').slice(0, 10);
  for (const key of Object.keys(ledger.risk_exits)) {
    if (key !== satSignalId) delete ledger.risk_exits[key];
  }

  if (!(capital > 0)) {
    ledger.strategy_as_of = asOf;
    return saveFlexLedger(ledger);
  }

  const relevantCodes = [
    ...targets.map(target => target.etf_code),
    ...Object.values(ledger.positions).map(pos => pos.etf_code),
  ].filter(Boolean);
  const markAsOf = flexEffectiveMarkDate(relevantCodes);
  ledger = flexApplyEodMarksToLedger(ledger);

  // Fill previously reserved entries only when the exact T+1 entry bar exists.
  for (const [key, pos] of Object.entries(ledger.positions)) {
    if (!pos?.pending_entry || Number(pos.qty) > 0) continue;
    const target = targetByKey.get(key);
    if (!target) continue;
    const bar = flexSimEntryBar(target);
    if (!bar) continue;
    const reserved = Number(pos.reserved_amount) || Number(pos.cost_basis) || 0;
    const qty = Math.floor(reserved / (Number(bar.open) * (1 + FLEX_ONE_WAY_COST_RATE) * 100)) * 100;
    if (!(qty > 0)) continue;
    const gross = qty * Number(bar.open);
    const fee = gross * FLEX_ONE_WAY_COST_RATE;
    const cost = gross + fee;
    ledger.cash += Math.max(0, reserved - cost);
    Object.assign(pos, {
      qty,
      avg_price: cost / qty,
      cost_basis: cost,
      last_price: Number(bar.open),
      entry_bar_date: bar.trade_date,
      entry_price_type: 'open',
      mark_quality: 'PENDING_EOD',
      pending_entry: false,
      reserved_amount: 0,
      updated_at: flexSimTradeTimestamp(bar.trade_date, 'open'),
      note: '模拟·策略纸面·T+1开盘成交',
    });
    ledger.journal = flexSimJournal(ledger.journal, {
      type: 'OPEN', type_cn: '模拟开仓', name: pos.name, etf_code: pos.etf_code,
      amount: cost, price: Number(bar.open), qty, fee,
      trade_date: bar.trade_date, ts: flexSimTradeTimestamp(bar.trade_date, 'open'),
      note: `策略纸面开仓 · 目标权重 ${pctLabel(pos.target_weight)}`,
    });
  }

  // Satellite basket risk is confirmed on a common official EOD close and
  // executed only at the next session's open. Never manufacture a same-close
  // fill or reuse a stale mark from a different holding date.
  ledger = flexApplyEodMarksToLedger(ledger);
  let satRisk = satSignalId ? ledger.risk_exits[satSignalId] : null;
  if (satSignalId && !satRisk && ledger.mark_as_of && Number(ledger._eod_mark_stats?.missing) === 0) {
    const basket = flexSatelliteBasketRiskStatus(ledger, f);
    if (basket?.triggered) {
      const executionDate = flexAddTradingDays(ledger.mark_as_of, 1);
      satRisk = {
        status: 'PENDING',
        close_code: basket.close_code,
        signal_date: ledger.mark_as_of,
        execution_date: executionDate,
        return_pct: basket.ret,
      };
      ledger.risk_exits[satSignalId] = satRisk;
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'SIGNAL', type_cn: basket.action_cn, name: '卫星组合', amount: 0,
        price: 0, qty: 0, trade_date: ledger.mark_as_of,
        ts: flexSimTradeTimestamp(ledger.mark_as_of, 'close'),
        note: `${basket.rule.ruleCn} · 下一交易日 ${executionDate} 开盘执行`,
      });
    }
  }
  if (satRisk) {
    targets = targets.filter(target => String(target.sleeve || '').toLowerCase() !== 'satellite');
    targetByKey = new Map(targets.map(target => [flexSimPositionKey(target), target]));
  }
  if (satRisk?.status === 'PENDING') {
    let allExecuted = true;
    for (const [key, pos] of Object.entries(ledger.positions)) {
      if (String(pos.sleeve || '').toLowerCase() !== 'satellite') continue;
      if (!(Number(pos.qty) > 0)) {
        ledger.cash += Number(pos.reserved_amount) || Number(pos.cost_basis) || 0;
        delete ledger.positions[key];
        continue;
      }
      const bar = flexEtfBarLookup(pos.etf_code, satRisk.execution_date, { prefer: 'exact' });
      const price = bar?.trade_date === satRisk.execution_date ? Number(bar.open) : null;
      if (!(price > 0)) {
        pos.pending_close = true;
        pos.pending_close_date = satRisk.execution_date;
        allExecuted = false;
        continue;
      }
      const qty = Number(pos.qty);
      const gross = qty * price;
      const fee = gross * FLEX_ONE_WAY_COST_RATE;
      const net = gross - fee;
      const cost = Number(pos.cost_basis) || 0;
      const pnl = net - cost;
      ledger.cash += net;
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'CLOSE', type_cn: `模拟${satRisk.close_code === 'LOCAL_STOP_LOSS' ? '止损' : '止盈'}`,
        name: pos.name, etf_code: pos.etf_code, amount: gross, net_amount: net,
        price, qty, fee, pnl, cost_removed: cost, return_pct: cost > 0 ? pnl / cost : null,
        trade_date: satRisk.execution_date,
        ts: flexSimTradeTimestamp(satRisk.execution_date, 'open'),
        note: `卫星组合 ${satRisk.close_code} · EOD ${satRisk.signal_date} 触发`,
      });
      delete ledger.positions[key];
    }
    if (allExecuted) {
      satRisk.status = 'EXECUTED';
      satRisk.executed_at = flexSimTradeTimestamp(satRisk.execution_date, 'open');
    }
  }

  // Authoritative state removal closes at that strategy day's open. If the bar
  // is not available yet, retain a visibly pending close instead of inventing one.
  for (const [key, pos] of Object.entries(ledger.positions)) {
    if (targetByKey.has(key)) continue;
    if (satRisk?.status === 'PENDING' && String(pos.sleeve || '').toLowerCase() === 'satellite') continue;
    if (!(Number(pos.qty) > 0) && pos.pending_entry) {
      const released = Number(pos.reserved_amount) || Number(pos.cost_basis) || 0;
      ledger.cash += released;
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'CANCEL', type_cn: '取消待开仓', name: pos.name, etf_code: pos.etf_code,
        amount: released, price: 0, qty: 0, trade_date: asOf,
        ts: flexSimTradeTimestamp(asOf, 'open'), note: `策略状态已退出 · as_of=${asOf}`,
      });
      delete ledger.positions[key];
      continue;
    }
    const bar = flexEtfBarLookup(pos.etf_code, asOf, { prefer: 'exact' });
    const price = bar?.trade_date === asOf ? Number(bar.open) : null;
    if (!(price > 0) || !(Number(pos.qty) > 0)) {
      pos.pending_close = true;
      pos.pending_close_date = asOf;
      continue;
    }
    const gross = Number(pos.qty) * price;
    const fee = gross * FLEX_ONE_WAY_COST_RATE;
    const net = gross - fee;
    const cost = Number(pos.cost_basis) || 0;
    const pnl = net - cost;
    ledger.cash += net;
    ledger.journal = flexSimJournal(ledger.journal, {
      type: 'CLOSE', type_cn: '模拟平仓', name: pos.name, etf_code: pos.etf_code,
      amount: gross, net_amount: net, price, qty: Number(pos.qty), fee, pnl, cost_removed: cost,
      return_pct: cost > 0 ? pnl / cost : null,
      trade_date: asOf, ts: flexSimTradeTimestamp(asOf, 'open'),
      note: `策略持仓状态退出 · as_of=${asOf}`,
    });
    delete ledger.positions[key];
  }

  if (ledger.pending_rebalance) {
    const signature = rows => JSON.stringify((rows || [])
      .map(row => [String(row.key || flexSimPositionKey(row)), Math.round((Number(row.weight) || 0) * 1000000)])
      .sort((a, b) => a[0].localeCompare(b[0])));
    const currentTargets = targets
      .filter(target => ledger.positions[flexSimPositionKey(target)])
      .map(target => ({ ...target, key: flexSimPositionKey(target) }));
    if (signature(ledger.pending_rebalance.targets) !== signature(currentTargets)) {
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'CANCEL', type_cn: '取消旧调仓', name: '组合', amount: 0,
        price: 0, qty: 0, trade_date: asOf, ts: flexSimTradeTimestamp(asOf, 'close'),
        note: '策略目标已变化，旧调仓指令作废',
      });
      ledger.pending_rebalance = null;
    }
  }
  ledger = flexSimExecutePendingRebalance(ledger);
  if (!ledger.pending_rebalance) {
    const changedTargets = targets.filter(target => {
      const pos = ledger.positions[flexSimPositionKey(target)];
      return pos && Math.abs((Number(pos.target_weight) || 0) - (Number(target.weight) || 0)) > 0.001;
    });
    if (changedTargets.length) {
      ledger.pending_rebalance = {
        status: 'PENDING',
        signal_date: asOf,
        execution_date: flexAddTradingDays(asOf, 1),
        targets: targets
          .filter(target => ledger.positions[flexSimPositionKey(target)])
          .map(target => ({
            key: flexSimPositionKey(target),
            name: target.name,
            etf_code: target.etf_code,
            weight: Number(target.weight) || 0,
          })),
      };
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'SIGNAL', type_cn: '模拟调仓信号', name: '组合', amount: 0,
        price: 0, qty: 0, trade_date: asOf, ts: flexSimTradeTimestamp(asOf, 'close'),
        note: `目标权重变化 · ${flexAddTradingDays(asOf, 1)} 开盘执行`,
      });
    }
  }

  const openingEquity = flexEquity(ledger);
  for (const target of targets) {
    const key = flexSimPositionKey(target);
    const existing = ledger.positions[key];
    const buyDate = String(target.buy_date || asOf).slice(0, 10);
    const holdDays = target.hold_days != null ? Number(target.hold_days) : null;
    const exitDate = target.exit_date ? String(target.exit_date).slice(0, 10) : null;
    if (existing) {
      Object.assign(existing, {
        key,
        name: target.name,
        etf_code: target.etf_code || existing.etf_code || '',
        etf_name: target.etf_name || existing.etf_name || '',
        sleeve: target.sleeve || existing.sleeve || '',
        target_weight: ledger.pending_rebalance
          ? (Number(existing.target_weight) || 0)
          : (Number(target.weight) || 0),
        signal_as_of: target.signal_as_of || existing.signal_as_of || '',
        hold_days: holdDays,
        exit_date: exitDate || existing.exit_date || null,
        exit_date_authoritative: Boolean(exitDate || existing.exit_date_authoritative),
        pending_close: false,
        pending_close_date: null,
      });
      continue;
    }

    const targetAmount = Math.min(
      Math.max(0, Number(ledger.cash) || 0),
      Math.max(0, openingEquity * (Number(target.weight) || 0)),
    );
    if (!(targetAmount > 0)) continue;
    const bar = flexSimEntryBar(target);
    const entryPrice = Number(bar?.open);
    const qty = entryPrice > 0
      ? Math.floor(targetAmount / (entryPrice * (1 + FLEX_ONE_WAY_COST_RATE) * 100)) * 100
      : 0;
    const gross = qty > 0 ? qty * entryPrice : 0;
    const fee = gross * FLEX_ONE_WAY_COST_RATE;
    const cost = qty > 0 ? gross + fee : targetAmount;
    ledger.cash -= cost;
    ledger.positions[key] = {
      id: flexUid('sim'), key, name: target.name, etf_code: target.etf_code || '',
      etf_name: target.etf_name || '', sleeve: target.sleeve || '',
      target_weight: Number(target.weight) || 0,
      qty, avg_price: qty > 0 ? cost / qty : 0, cost_basis: cost,
      last_price: qty > 0 ? entryPrice : 0,
      opened_at: flexSimTradeTimestamp(buyDate, 'open'),
      updated_at: flexSimTradeTimestamp(buyDate, 'open'),
      signal_as_of: target.signal_as_of || '', buy_date: buyDate,
      hold_days: holdDays, exit_date: exitDate,
      exit_date_authoritative: Boolean(exitDate),
      entry_price_type: 'open', entry_bar_date: bar?.trade_date || null,
      mark_price_type: qty > 0 ? 'open' : null,
      mark_quality: qty > 0 ? 'PENDING_EOD' : 'MISSING_ENTRY',
      pending_entry: !(qty > 0), reserved_amount: qty > 0 ? 0 : targetAmount,
      note: qty > 0 ? '模拟·策略纸面·T+1开盘成交' : `模拟·等待${buyDate}入场日行情`,
      sim: true,
    };
    if (qty > 0) {
      ledger.journal = flexSimJournal(ledger.journal, {
        type: 'OPEN', type_cn: '模拟开仓', name: target.name, etf_code: target.etf_code || '',
        amount: cost, price: entryPrice, qty, fee,
        trade_date: buyDate, ts: flexSimTradeTimestamp(buyDate, 'open'),
        note: `策略纸面开仓 · 目标权重 ${pctLabel(target.weight)}`,
      });
    }
  }

  ledger.strategy_as_of = asOf;
  ledger.mark_as_of = markAsOf;
  ledger.mark_policy = 'SIM_INCREMENTAL_ENTRY_OPEN_MARK_CLOSE';
  ledger.mark_policy_cn = '增量账本：入场=实际T+1开盘 · 盯市=持仓共同可得EOD · 不追溯重定价';
  dashboardState.flexSimSyncedAsOf = asOf;
  return saveFlexLedger(ledger);
}

function flexPositionKey(item) {
  const name = String(item?.name || item?.sector || 'unknown').trim();
  const sleeve = String(item?.sleeve || 'na').trim();
  if (name && name !== 'unknown') return `name:${sleeve}:${name}`;
  const code = String(item?.etf_code || item?.code || '').trim();
  return code ? `etf:${code}` : `name:${sleeve}:unknown`;
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

function flexRealizedReturnPct(ledger) {
  let pnl = 0;
  let cost = 0;
  for (const row of ledger?.journal || []) {
    const type = String(row.type || '').toUpperCase();
    if (type !== 'CLOSE' && type !== 'REDUCE') continue;
    const rowPnl = Number(row.pnl);
    if (!Number.isFinite(rowPnl)) continue;
    let rowCost = Number(row.cost_removed);
    if (!(rowCost > 0)) {
      const net = Number(row.net_amount);
      rowCost = Number.isFinite(net) ? net - rowPnl : NaN;
    }
    if (!(rowCost > 0)) continue;
    pnl += rowPnl;
    cost += rowCost;
  }
  return cost > 0 ? pnl / cost : null;
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
  if (flexMarkMissing(pos)) return null;
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

/** Risk exits remain EOD-confirmed even while the desk shows an intraday quote. */
function flexPositionEodReturnPct(pos) {
  if (flexMarkMissing(pos)) return null;
  const cost = Number(pos?.cost_basis);
  const qty = Number(pos?.qty);
  const eodPrice = Number(pos?.eod_last_price);
  if (!(cost > 0) || !(qty > 0) || !(eodPrice > 0)) return null;
  return (qty * eodPrice) / cost - 1;
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
  const ret = flexPositionEodReturnPct(pos);
  const rule = flexSatelliteRiskRule(flex);
  const markDay = pos?.eod_mark_bar_date || pos?.mark_bar_date || flex?.as_of || flex?.market_state?.trade_date || flexSessionTradeDate();
  const daysHeld = flexPositionDaysHeld(pos, markDay);
  if (ret == null) {
    return {
      triggered: false,
      label: `满${FLEX_SAT_MIN_HOLD_DAYS}日后按EOD查止损${flexFormatSignedPct(rule.stopLoss, 0)} / 止盈${flexFormatSignedPct(rule.takeProfit, 0)} · 缺价`,
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
      why: `卫星持仓EOD收益 ${flexFormatSignedPct(ret)} 已低于止损线 ${flexFormatSignedPct(rule.stopLoss, 0)}；按规则下一交易日开盘平仓`,
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
      why: `卫星持仓EOD收益 ${flexFormatSignedPct(ret)} 已高于止盈线 ${flexFormatSignedPct(rule.takeProfit, 0)}；按规则下一交易日开盘平仓`,
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

function flexEodDecisionGate(marked, flex) {
  const markDate = String(marked?.mark_as_of || '').slice(0, 10);
  const requiredDates = [
    flex?.as_of,
    flex?.market_state?.trade_date,
    dashboardState.latest?.official_close?.trade_date,
  ]
    .map(value => String(value || '').slice(0, 10))
    .filter(value => /^\d{4}-\d{2}-\d{2}$/.test(value));
  const requiredDate = requiredDates.sort().at(-1) || null;
  const missing = Number(marked?._eod_mark_stats?.missing) || 0;
  const staleCodes = marked?.mark_stale_codes || [];
  const gate = FlexExecutionCore.eodDecisionGate({
    markDate,
    requiredDate,
    missing,
    staleCount: staleCodes.length,
  });
  if (gate.code === 'MISSING') {
    return { ok: false, reason: '共同EOD缺价', markDate, requiredDate };
  }
  if (gate.code === 'STALE') {
    return {
      ok: false,
      reason: `共同EOD仅到 ${markDate}${requiredDate ? `，要求 ${requiredDate}` : ''}`,
      markDate,
      requiredDate,
    };
  }
  return { ok: gate.ok, reason: '', markDate, requiredDate };
}

function flexSatelliteBasketRiskStatus(ledger, flex) {
  const satellites = flexOpenPositions(ledger).filter(
    pos => String(pos?.sleeve || '').toLowerCase() === 'satellite'
  );
  if (!satellites.length) return null;
  const cost = satellites.reduce((sum, pos) => sum + (Number(pos.cost_basis) || 0), 0);
  const value = satellites.reduce((sum, pos) => {
    const px = Number(pos.eod_last_price);
    const qty = Number(pos.qty);
    return sum + (px > 0 && qty > 0 ? px * qty : 0);
  }, 0);
  const missing = satellites.some(pos =>
    flexMarkMissing(pos) || !(Number(pos.eod_last_price) > 0)
  );
  const rule = flexSatelliteRiskRule(flex);
  const decisionGate = flexEodDecisionGate(ledger, flex);
  if (!decisionGate.ok) {
    return {
      triggered: false,
      blocked: true,
      label: `卫星风控暂停 · ${decisionGate.reason}`,
      rule,
      markDate: decisionGate.markDate,
      requiredDate: decisionGate.requiredDate,
    };
  }
  const markDay = satellites.map(pos => pos.eod_mark_bar_date || pos.mark_bar_date || '').filter(Boolean).sort().at(-1)
    || flex?.as_of || flexSessionTradeDate();
  const daysHeld = Math.min(...satellites.map(pos => flexPositionDaysHeld(pos, markDay)));
  if (missing || !(cost > 0) || !(value > 0)) {
    return { triggered: false, label: `卫星篮子EOD缺价 · 无法核验${flexFormatSignedPct(rule.stopLoss, 0)}/${flexFormatSignedPct(rule.takeProfit, 0)}`, rule };
  }
  const ret = value / cost - 1;
  if (daysHeld < FLEX_SAT_MIN_HOLD_DAYS) {
    return { triggered: false, label: `卫星篮子已持有${daysHeld}日 · 满${FLEX_SAT_MIN_HOLD_DAYS}日后检查止损/止盈`, rule, ret, daysHeld };
  }
  if (ret <= rule.stopLoss || ret >= rule.takeProfit) {
    const stop = ret <= rule.stopLoss;
    return {
      triggered: true,
      close_code: stop ? 'LOCAL_STOP_LOSS' : 'LOCAL_TAKE_PROFIT',
      action_cn: stop ? '卫星篮子止损卖出' : '卫星篮子止盈卖出',
      badge: stop ? '止损平仓' : '止盈平仓',
      label: `卫星篮子${stop ? '止损' : '止盈'}已触发 ${flexFormatSignedPct(ret)}`,
      why: `卫星篮子EOD累计收益 ${flexFormatSignedPct(ret)} 已触发 ${stop ? flexFormatSignedPct(rule.stopLoss, 0) : flexFormatSignedPct(rule.takeProfit, 0)}；下一交易日开盘整篮平仓`,
      rule,
      ret,
      daysHeld,
    };
  }
  return {
    triggered: false,
    label: `卫星篮子距止损${((ret - rule.stopLoss) * 100).toFixed(1)}个百分点 · 距止盈${((rule.takeProfit - ret) * 100).toFixed(1)}个百分点`,
    rule,
    ret,
    daysHeld,
  };
}

function flexSuggestedAmount(item, capital, ledger = null, localPosition = null) {
  const cap = Number(capital) || 0;
  if (!(cap > 0)) return null;
  const w = Number(item?.weight_target);
  if (Number.isFinite(w) && w > 0) {
    const target = cap * w;
    const cashCap = ledger ? Math.floor(flexAvailableCash(ledger) / (1 + FLEX_ONE_WAY_COST_RATE)) : Infinity;
    if (ledger && localPosition) {
      const mark = Number(localPosition.last_price) > 0 ? Number(localPosition.last_price) : Number(localPosition.avg_price);
      const current = Number(localPosition.qty) > 0 && mark > 0
        ? Number(localPosition.qty) * mark
        : Number(localPosition.cost_basis) || 0;
      return Math.max(0, Math.min(cashCap, Math.round(target - current)));
    }
    return Math.min(cashCap, Math.round(target));
  }
  const hint = String(item?.weight_hint || '');
  const m = hint.match(/(\d+(?:\.\d+)?)\s*%/);
  if (m) return Math.round(cap * (Number(m[1]) / 100));
  return null;
}

function deskLocalRebalanceActions(flex, ledger = loadFlexLedger()) {
  if (isFlexSimBook()) return { adds: [], reduces: [], fresh: false, signalAsOf: null };
  // Rebalance instructions are decisions, not display marks. Use one common
  // official EOD session so an intraday quote cannot continuously move orders.
  const marked = flexApplyEodMarksToLedger(ledger);
  if (!flexEodDecisionGate(marked, flex).ok) {
    return { adds: [], reduces: [], fresh: false, signalAsOf: marked.mark_as_of || null };
  }
  const equity = flexEquity(marked);
  if (!(equity > 0)) return { adds: [], reduces: [], fresh: false, signalAsOf: marked.mark_as_of || null };
  const allocation = flex?.allocation || {};
  const satWeights = flex?.position_state?.satellite?.weights || flex?.satellite?.weights || {};
  const satWeightSum = Object.values(satWeights).reduce((sum, value) => sum + (Number(value) || 0), 0) || 1;
  const adds = [];
  const reduces = [];
  const tolerance = Math.max(100, equity * 0.005);

  for (const pos of flexOpenPositions(marked)) {
    const sleeve = String(pos.sleeve || '').toLowerCase();
    let targetWeight = null;
    if (sleeve === 'core' && allocation.w_core != null) {
      targetWeight = Number(allocation.w_core);
    } else if (sleeve === 'satellite' && allocation.w_sat != null && satWeights[pos.name] != null) {
      targetWeight = Number(allocation.w_sat) * Number(satWeights[pos.name]) / satWeightSum;
    }
    if (!Number.isFinite(targetWeight) || targetWeight <= 1e-9) continue;
    const mark = Number(pos.last_price) > 0 ? Number(pos.last_price) : Number(pos.avg_price);
    const currentValue = Number(pos.qty) > 0 && mark > 0 ? Number(pos.qty) * mark : Number(pos.cost_basis) || 0;
    const targetValue = equity * targetWeight;
    const gap = targetValue - currentValue;
    if (Math.abs(gap) < tolerance) continue;
    const base = {
      sleeve: pos.sleeve || '',
      name: pos.name || '—',
      etf_code: pos.etf_code || '',
      etf_name: pos.etf_name || '',
      weight_target: targetWeight,
      weight_hint: pctLabel(targetWeight),
      current_weight: currentValue / equity,
      rebalance_amount: Math.round(Math.abs(gap)),
      signal_as_of: marked.mark_as_of,
      _key: pos.key || flexPositionKey(pos),
      _deskRebalance: true,
      priority: 'P0',
      why: `进取模式目标再平衡：当前 ${pctLabel(currentValue / equity)} → 目标 ${pctLabel(targetWeight)}`,
    };
    if (gap > 0) {
      adds.push({ ...base, action: 'REBALANCE_ADD', action_cn: '目标加仓', side: 'BUY' });
    } else {
      reduces.push({ ...base, action: 'REBALANCE_REDUCE', action_cn: '目标减仓', side: 'SELL' });
    }
  }
  return { adds, reduces, fresh: true, signalAsOf: marked.mark_as_of };
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
  const observed = new Set();
  const official = dashboardState.tradeCalendar || {};
  const add = (v) => {
    const s = String(v || '').slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) set.add(s);
  };
  const addObserved = (v) => {
    const s = String(v || '').slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      set.add(s);
      observed.add(s);
    }
  };
  for (const row of dashboardState.history || []) addObserved(row.date || row.trade_date);
  const nh = dashboardState.nowcastHistory;
  const nhRows = Array.isArray(nh) ? nh : (nh?.rows || []);
  for (const row of nhRows) addObserved(row.date || row.trade_date);
  for (const day of official.dates || []) add(day);
  addObserved(dashboardState.lastTradeDate);
  const flex = dashboardState.flexActive || dashboardState.flexPlaybook?.flex_panel;
  addObserved(flex?.as_of);
  addObserved(flex?.market_state?.trade_date);
  const satPaths = flex?.exit_plan?.satellite?.paths || {};
  Object.values(satPaths).forEach(add);
  const coreEp = flex?.exit_plan?.core || {};
  add(coreEp.max_signal_date);
  add(coreEp.max_exec_next_open);
  add(coreEp.exit_due_date);
  add(flex?.position_state?.core?.entry_date);
  add(flex?.position_state?.satellite?.entry_date);

  let list = Array.from(set).sort();
  const officialThrough = String(official.coverage_through || '').slice(0, 10);
  const hasOfficialCoverage = Boolean(official.authoritative && officialThrough && (official.dates || []).length);
  if (hasOfficialCoverage) {
    const horizon = flexAddCalendarDays(flexDateCn(0), 200);
    if (horizon > officialThrough) {
      const fallback = flexGenerateWeekdayRange(flexAddCalendarDays(officialThrough, 1), horizon);
      list = Array.from(new Set([...list, ...fallback])).sort();
    }
  // Fallback if history/calendar is unavailable: Mon-Fri skeleton only beyond known evidence.
  } else if (observed.size < 30) {
    const today = flexDateCn(0);
    const skeleton = flexGenerateWeekdayRange('2020-01-01', flexAddCalendarDays(today, 200));
    list = Array.from(new Set([...skeleton, ...list])).sort();
  } else {
    // Explicit exit-plan dates can be sparse. Fill forward from the last
    // observed market session, not from the furthest plan date, or weekdays
    // between the two disappear and exit countdowns drift.
    const lastObserved = Array.from(observed).sort().at(-1);
    const furthestExplicit = list.at(-1) || lastObserved;
    const horizon = [furthestExplicit, flexAddCalendarDays(flexDateCn(0), 200)].sort().at(-1);
    const future = flexGenerateWeekdayRange(lastObserved, horizon);
    list = Array.from(new Set([...list, ...future])).sort();
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
  // Strategy/backend due dates are authoritative and may include a trading
  // calendar unavailable to the browser.
  if (pos.exit_date) return String(pos.exit_date).slice(0, 10);
  if (pos.buy_date && pos.hold_days != null && Number.isFinite(Number(pos.hold_days))) {
    return flexAddTradingDays(pos.buy_date, Number(pos.hold_days));
  }
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
  if (pos.exit_date_authoritative && exitDate) {
    left = Math.max(0, flexTradingDaysBetween(today, exitDate));
  } else if (holdDays != null && daysHeld != null) {
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

function flexBuyOrderFromBudget(budget, price, cashAvailable) {
  return FlexExecutionCore.buyOrderFromBudget(budget, price, cashAvailable);
}

function flexSellQuantity(positionQty, price, { amount = null, pct = null } = {}) {
  return FlexExecutionCore.sellQuantity(positionQty, price, { amount, pct });
}

function flexApplyBuy(ledger, draft) {
  ledger = normalizeFlexLedger(ledger);
  const amount = Number(draft.amount);
  const price = Number(draft.price);
  if (!(amount > 0) || !(price > 0)) {
    throw new Error('请输入有效的买入金额和成交价');
  }
  const cash = flexAvailableCash(ledger);
  const order = flexBuyOrderFromBudget(amount, price, cash);
  if (!(order.qty >= FLEX_ETF_LOT_SIZE)) {
    throw new Error(`预算不足一手（${FLEX_ETF_LOT_SIZE}份，含1bp成本）`);
  }
  const { qty, gross, fee, cash_required: cashRequired } = order;
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
    const newCost = Number(existing.cost_basis) + cashRequired;
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
      avg_price: cashRequired / qty,
      cost_basis: cashRequired,
      last_price: price,
      opened_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      signal_as_of: draft.signal_as_of || '',
      buy_date: buyDate,
      hold_days: holdDays,
      exit_date: exitDate,
      note: draft.note || '',
      execution_mode: draft.execution_mode || 'MANUAL',
      entry_price_type: draft.entry_price_type || 'manual_fill',
    };
  }
  ledger.cash = cash - cashRequired;
  appendFlexJournal(ledger, {
    type: existing ? 'ADD' : 'BUY',
    type_cn: existing ? '加仓' : '买入',
    key,
    name: draft.name,
    etf_code: draft.etf_code || '',
    amount: gross,
    price,
    qty,
    fee,
    cost_rate: FLEX_ONE_WAY_COST_RATE,
    signal_as_of: draft.signal_as_of || '',
    budget_amount: amount,
    execution_mode: draft.execution_mode || 'MANUAL',
  });
  return saveFlexLedger(ledger);
}

function flexApplyReduce(ledger, key, { amount, price, pct }) {
  ledger = normalizeFlexLedger(ledger);
  const pos = ledger.positions[key];
  if (!pos || !(Number(pos.qty) > 0)) throw new Error('持仓不存在或已平仓');
  const px = Number(price);
  if (!(px > 0)) throw new Error('请输入有效成交价');

  if (!((amount != null && Number(amount) > 0) || (pct != null && Number(pct) > 0))) {
    throw new Error('请输入减仓金额或比例');
  }
  const sellQty = flexSellQuantity(pos.qty, px, { amount, pct });
  if (!(sellQty > 0)) throw new Error(`减仓不足一手（${FLEX_ETF_LOT_SIZE}份）`);
  const sellAmount = sellQty * px;
  if (sellQty > Number(pos.qty) + 1e-9) {
    throw new Error('减仓数量超过持仓');
  }

  const costRemoved = (Number(pos.cost_basis) / Number(pos.qty)) * sellQty;
  const remainQty = Number(pos.qty) - sellQty;
  const fee = sellAmount * FLEX_ONE_WAY_COST_RATE;
  const netProceeds = sellAmount - fee;
  const pnl = netProceeds - costRemoved;
  // Net proceeds return to cash (realized PnL and one-way cost included).
  ledger.cash = Number(ledger.cash) + netProceeds;

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
      cost_removed: costRemoved,
      fee,
      net_amount: netProceeds,
      cost_rate: FLEX_ONE_WAY_COST_RATE,
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
      cost_removed: costRemoved,
      fee,
      net_amount: netProceeds,
      cost_rate: FLEX_ONE_WAY_COST_RATE,
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
  // EOD is canonical; an in-session quote overlays it only for display totals.
  const ledger = flexApplyDisplayMarksToLedger(raw);
  const capitalInput = document.getElementById('flexCapitalInput');
  if (capitalInput && document.activeElement !== capitalInput) {
    capitalInput.value = ledger.capital > 0 ? String(ledger.capital) : '';
  }
  const deployed = flexDeployedCost(ledger);
  const cash = flexAvailableCash(ledger);
  const mtm = flexMarkValue(ledger);
  const equity = flexEquity(ledger);
  const uPnl = flexUnrealizedPnl(ledger);
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
  const rRet = flexRealizedReturnPct(ledger);

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
    const strategyAsOf = String(dashboardState.flexActive?.as_of || ledger.strategy_as_of || '').slice(0, 10);
    const quoteWindow = typeof getFlexQuoteWindow === 'function' ? getFlexQuoteWindow() : { active: false, phase: 'eod' };
    const rt = ledger._realtime_mark_stats;
    const parts = [strategyAsOf ? `策略信号：正式EOD ${strategyAsOf}` : null];
    if (hasBook) {
      parts.push(md ? `收益/风控：持仓共同EOD ${md}` : '收益/风控：共同EOD缺失');
      if ((ledger.mark_stale_codes || []).length) parts.push(`滞后标的 ${(ledger.mark_stale_codes || []).length}只`);
      if ((ledger.mark_missing_codes || []).length) parts.push(`缺价 ${(ledger.mark_missing_codes || []).length}只`);
      if (quoteWindow.active && rt?.marked) {
        const source = rt.source === 'TENCENT' ? '腾讯' : rt.source === 'EASTMONEY' ? '东财' : '实时源';
        parts.push(`${rt.phase === 'final' ? '收盘报价' : '盘中盯市'}：${source} ${rt.latest_at || '—'}（仅展示）`);
      } else if (quoteWindow.active) {
        parts.push('实时盯市不可用，已回退共同EOD');
      }
    }
    note.hidden = parts.length === 0;
    note.textContent = parts.join(' · ');
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
  const ledger = flexApplyDisplayMarksToLedger(loadFlexLedger());
  const positions = flexOpenPositions(ledger);
  const equity = flexEquity(ledger);
  const basketRisk = flexSatelliteBasketRiskStatus(ledger, dashboardState.flexActive);
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
    const markValue = flexMarkMissing(pos)
      ? Number(pos.cost_basis) || 0
      : (Number(pos.qty) || 0) * (Number(pos.last_price) || Number(pos.avg_price) || 0);
    const weight = equity > 0 ? markValue / equity : null;
    const missingPx = flexMarkMissing(pos);
    const ret = flexPositionReturnPct(pos);
    const pnlCls = ret == null ? '' : ret > 0 ? 'up' : ret < 0 ? 'down' : '';
    const pnlTxt = missingPx ? '缺价' : (ret == null ? '—' : flexFormatSignedPct(ret));
    const exitInfo = flexPositionExitInfo(pos);
    const riskStatus = String(pos.sleeve || '').toLowerCase() === 'satellite' ? basketRisk : null;
    const exitLabel = riskStatus?.triggered
      ? riskStatus.label
      : [exitInfo.label, riskStatus?.label].filter(Boolean).join(' · ');
    const markDay = pos.mark_price_type === 'realtime'
      ? `${pos.realtime_quote_date || flexDateCn(0)} ${pos.realtime_quote_at || '—'}`
      : (pos.mark_bar_date || ledger.mark_as_of || '');
    const markSource = pos.mark_price_type === 'realtime'
      ? (pos.realtime_quote_source === 'TENCENT' ? '腾讯实时' : pos.realtime_quote_source === 'EASTMONEY' ? '东财实时' : '实时')
      : '正式EOD';
    const titleBits = [
      exitLabel,
      riskStatus?.rule?.ruleCn,
      missingPx
        ? '缺 EOD 行情，涨跌幅不可用'
        : (ret != null ? `涨跌幅 ${flexFormatSignedPct(ret)} · ${markDay || '—'}` : ''),
      Number(pos.avg_price) > 0 ? `入场 ${formatPrice(pos.avg_price)}` : (missingPx ? '入场价缺失' : ''),
      Number(pos.last_price) > 0 ? `盯市 ${formatPrice(pos.last_price)}` : (missingPx ? '盯市价缺失' : ''),
      pos.note || '',
    ].filter(Boolean).join(' · ');
    return `<div class="flex-row flex-row-book" data-pos-key="${escapeHtml(pos.key)}" title="${escapeHtml(titleBits)}">
      <span class="flex-row-code" data-label="代码">${escapeHtml(pos.etf_code || '—')}</span>
      <span class="flex-row-name" data-label="名称">${escapeHtml(pos.name || '—')}</span>
      <span class="flex-row-num" data-label="份额">${formatShares(pos.qty)}</span>
      <span class="flex-row-num flex-row-stack" data-label="成本/均价"><strong>${formatMoney(pos.cost_basis)}</strong><small>@ ${Number(pos.avg_price) > 0 ? formatPrice(pos.avg_price) : (missingPx ? '缺价' : '—')}</small></span>
      <span class="flex-row-num flex-row-stack" data-label="盯市/时点"><strong>${Number(pos.last_price) > 0 ? formatPrice(pos.last_price) : '缺价'}</strong><small>${escapeHtml(`${markSource} · ${markDay || '—'}`)}</small></span>
      <span class="flex-row-num" data-label="仓位">${weight != null ? pctLabel(weight) : '—'}</span>
      <span class="flex-row-num ${pnlCls}" data-label="涨跌幅">${pnlTxt}</span>
      <span class="flex-row-num flex-row-exit" data-label="清仓">${escapeHtml(exitLabel)}</span>
      <span class="flex-row-acts" data-label="操作">${isFlexSimBook()
        ? '<span class="flex-chip ghost" title="模拟仓由策略状态机自动维护">自动管理</span>'
        : `<button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(pos.key)}">加</button>
        <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(pos.key)}">减</button>
        <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(pos.key)}">平</button>`}
      </span>
    </div>`;
  }).join('');
}

function renderFlexJournal() {
  const el = document.getElementById('flexJournalList');
  if (!el) return;
  const ledger = loadFlexLedger();
  const rows = FlexExecutionCore.sortJournalNewestFirst(ledger.journal).slice(0, 50);
  if (!rows.length) {
    el.innerHTML = `<div class="flex-empty-state soft">
      <strong>暂无流水</strong>
      <p>买卖、调仓、调整全仓金额会记录在此。可导出 JSON 备份。</p>
    </div>`;
    return;
  }
  el.innerHTML = rows.map(row => {
    const tradeDay = String(row.trade_date || row.ts || '').slice(0, 10);
    const when = row.ts
      ? new Date(row.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      : (tradeDay || '—');
    const label = row.type_cn || row.type || '—';
    const code = row.etf_code || row.name || '—';
    // Journal: prefer return % (pnl / cost). CLOSE/REDUCE: cost ≈ amount - pnl when amount is proceeds.
    let retTxt = '—';
    let retCls = '';
    const pnlN = Number(row.pnl);
    const amtN = Number(row.amount);
    if (Number.isFinite(pnlN)) {
      const t = String(row.type || '').toUpperCase();
      let cost = Number(row.cost_removed);
      if ((t === 'CLOSE' || t === 'REDUCE') && Number.isFinite(amtN)) {
        const net = Number(row.net_amount);
        if (!(cost > 0)) cost = (Number.isFinite(net) ? net : amtN - (Number(row.fee) || 0)) - pnlN;
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
      <span class="flex-row-time" data-label="成交时间">${escapeHtml(when)}</span>
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

function setFlexReduceMode(mode, { refresh = true } = {}) {
  const state = dashboardState.flexModal;
  if (!state || state.mode !== 'reduce') return;
  const next = mode === 'amount' ? 'amount' : 'pct';
  state.reduceMode = next;
  const amountField = document.getElementById('flexModalAmountField');
  const pctField = document.getElementById('flexModalPctField');
  if (amountField) amountField.hidden = next !== 'amount';
  if (pctField) pctField.hidden = next !== 'pct';
  document.querySelectorAll('#flexModalReduceMode [data-flex-reduce-mode]').forEach(btn => {
    const active = btn.dataset.flexReduceMode === next;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  if (refresh) updateFlexModalPreview();
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
      const order = flexBuyOrderFromBudget(amount, price, cash);
      const w = capital > 0 ? pctLabel(order.gross / capital) : '—';
      const afterCash = cash - order.cash_required;
      preview.textContent = order.qty > 0
        ? `${formatShares(order.qty)} 份（整手）· 实际成交 ${formatMoney(order.gross)} · 占全仓 ${w} · 1bp ${formatMoney(order.fee, 2)} · 余现 ${formatMoney(Math.max(0, afterCash))}`
        : `预算不足 ${FLEX_ETF_LOT_SIZE} 份（含1bp成本）`;
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
    const reduction = FlexExecutionCore.reductionInstruction(state.reduceMode, amount, pct);
    if (reduction.amount && price > 0) {
      sellQty = flexSellQuantity(pos.qty, price, reduction);
      sellAmt = sellQty * price;
    } else if (reduction.pct && price > 0) {
      sellQty = flexSellQuantity(pos.qty, price, reduction);
      sellAmt = sellQty * price;
    }
    if (sellQty > 0) {
      const costRemoved = (Number(pos.cost_basis) / Number(pos.qty)) * sellQty;
      const pnl = sellAmt - costRemoved;
      const fee = sellAmt * FLEX_ONE_WAY_COST_RATE;
      const netPnl = pnl - fee;
      const ret = costRemoved > 0 ? netPnl / costRemoved : null;
      preview.textContent = `卖出 ${formatShares(sellQty)} · 金额 ${formatMoney(sellAmt)} · 成本1bp约 ${formatMoney(fee, 2)} · 预计涨跌幅 ${ret != null ? flexFormatSignedPct(ret) : '—'} · 剩余 ${formatShares(Math.max(0, Number(pos.qty) - sellQty))}`;
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
      const fee = amt * FLEX_ONE_WAY_COST_RATE;
      const ret = cost > 0 ? (pnl - fee) / cost : null;
      preview.textContent = `全平约 ${formatMoney(amt)} · 成本1bp约 ${formatMoney(fee, 2)} · 预计涨跌幅 ${ret != null ? flexFormatSignedPct(ret) : '—'} · 回现金`;
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
  const reduceMode = document.getElementById('flexModalReduceMode');
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
    if (reduceMode) reduceMode.hidden = true;
    if (chips) chips.hidden = false;
    if (amountLabel) amountLabel.textContent = '预算上限（元）';
    if (amountEl) amountEl.value = spec.defaultAmount != null ? String(spec.defaultAmount) : '';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  } else if (spec.mode === 'reduce') {
    if (priceField) priceField.hidden = false;
    if (reduceMode) reduceMode.hidden = false;
    if (chips) chips.hidden = true;
    if (amountLabel) amountLabel.textContent = '金额（元）';
    if (amountEl) amountEl.value = spec.defaultAmount != null ? String(spec.defaultAmount) : '';
    if (pctEl) pctEl.value = '50';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
    dashboardState.flexModal.reduceMode = Number(spec.defaultAmount) > 0 ? 'amount' : 'pct';
    setFlexReduceMode(dashboardState.flexModal.reduceMode, { refresh: false });
  } else if (spec.mode === 'close') {
    if (amountField) amountField.hidden = true;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = true;
    if (reduceMode) reduceMode.hidden = true;
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
    const pendingOrder = state.pendingOrderId ? ledger.pending_orders?.[state.pendingOrderId] : null;
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
        execution_mode: state.execution_mode || 'MANUAL',
        entry_price_type: state.execution_mode === 'T_TAIL_1450' ? 'tail_realtime' : 'manual_fill',
        note: state.execution_mode === 'T_TAIL_1450' ? 'CORE严格条件·T日尾盘执行' : '',
      });
    } else if (state.mode === 'reduce') {
      const reduction = FlexExecutionCore.reductionInstruction(state.reduceMode, amount, pct);
      ledger = flexApplyReduce(ledger, state.key, {
        price,
        ...reduction,
      });
    } else if (state.mode === 'close') {
      ledger = flexApplyClose(ledger, state.key, price);
    }
    if (
      state.pendingOrderId
      && (pendingOrder?.kind === 'rebalance' || !ledger.positions?.[state.key])
    ) {
      flexClearPendingOrder(state.pendingOrderId);
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
    if (state.mode === 'buy' || state.mode === 'add' || state.mode === 'reduce' || state.mode === 'close') {
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
    if (item?.execution_mode === 'T_TAIL_1450' && lag === 0) {
      return { text: '14:50尾盘买', cls: 'buy' };
    }
    if (lag === 1) return { text: 'T+1可确认', cls: 'buy' };
    return { text: '可买·至T+1', cls: 'buy' };
  }
  if (FLEX_CLOSE_ACTIONS.has(action) || action === 'CLOSE') {
    if (item?._strategyPaper && !options.localHeld) {
      return { text: '策略平仓', cls: 'sell' };
    }
    const code = item?.close_code || '';
    if (code === 'MAX_HOLD' || code === 'CORE_MAX_HOLD' || code === 'LOCAL_MAX_HOLD') return { text: '到期平仓', cls: 'sell' };
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
  const positionByName = openPos.find(p => {
    const pn = String(p.name || '').trim();
    return name && pn && name === pn;
  });
  if (positionByName) {
    return { key: positionByName.key || flexPositionKey(positionByName), position: positionByName };
  }
  // Named strategy intents must not fall through to code-only matching. Several
  // sector proxies share one ETF; code matching could close the wrong sleeve.
  if (name) return null;
  const position = openPos.find(p => {
    const pc = String(p.etf_code || '').trim();
    return code && pc && code === pc;
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

  const byKey = new Map();
  const put = (item) => {
    if (!item) return;
    const action = String(item.action || item.side || '').toUpperCase();
    if (!openKeys.has(action)) return;
    const signalDay = String(item.signal_as_of || asOf).slice(0, 10);
    if (!deskSignalWindowOpen(signalDay)) return;
    const key = flexPositionKey(item);
    if (!key || byKey.has(key)) return;
    byKey.set(key, {
      ...item,
      action: action === 'BUY' ? 'OPEN' : item.action || 'OPEN',
      signal_as_of: signalDay,
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
    const satSleeveWeight = Number(f.allocation?.w_sat ?? f.satellite?.weight_target ?? 0);
    for (const name of names) {
      const base = holdByName.get(name) || buyByName.get(name) || { name, sleeve: 'satellite' };
      const w = weights[name];
      pushRow({
        ...base,
        sleeve: 'satellite',
        name,
        weight_target: base.weight_target != null ? base.weight_target : satSleeveWeight * Number(w || 0),
        weight_hint: base.weight_hint || (w != null ? `${Math.round(satSleeveWeight * Number(w) * 100)}%` : '—'),
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
      weight_target: core.weight_target ?? f.allocation?.w_core,
      weight_hint: core.weight_hint || (f.allocation?.w_core != null ? pctLabel(f.allocation.w_core) : '—'),
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
    const cache = loadOpenSignalCache();
    const cached = cache?.as_of && Array.isArray(cache.items)
      ? cache.items.map(item => ({
        ...item,
        signal_as_of: item.signal_as_of || cache.as_of,
        action: item.action || 'OPEN',
      })).filter(item => deskSignalWindowOpen(item.signal_as_of))
      : [];
    const merged = new Map();
    for (const item of [...fresh, ...cached]) {
      const key = flexPositionKey(item);
      if (key && !merged.has(key)) merged.set(key, item);
    }
    const cacheable = fresh.filter(item => item.execution_mode !== 'T_TAIL_1450');
    if (cacheable.length) saveOpenSignalCache(asOf, cacheable);
    return [...merged.values()];
  }

  // 2) Browser cache only if its stored signal day is still T or T+1.
  const cache = loadOpenSignalCache();
  if (cache?.as_of && deskSignalWindowOpen(cache.as_of) && Array.isArray(cache.items) && cache.items.length) {
    return cache.items.map(item => ({
      ...item,
      signal_as_of: item.signal_as_of || cache.as_of,
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
      signal_as_of: info.exitDate || today,
      _key: pos.key || flexPositionKey(pos),
      _deskLocalDue: true,
    });
  }
  return rows;
}

/** Satellite stop-loss / take-profit closes from local EOD marks. */
function deskLocalRiskCloses(flex, ledger = loadFlexLedger()) {
  const marked = flexApplyEodMarksToLedger(ledger);
  const basket = flexSatelliteBasketRiskStatus(marked, flex);
  if (!basket?.triggered) return [];
  const rows = [];
  for (const pos of flexOpenPositions(marked)) {
    if (String(pos.sleeve || '').toLowerCase() !== 'satellite') continue;
    rows.push({
      action: 'CLOSE',
      action_cn: basket.action_cn,
      side: 'CLOSE',
      side_cn: '卖出',
      sleeve: pos.sleeve || 'satellite',
      name: pos.name || '—',
      etf_code: pos.etf_code || '',
      etf_name: pos.etf_name || '',
      priority: 'P0',
      entry: '下一交易日开盘',
      exit: '平仓',
      why: basket.why,
      close_code: basket.close_code,
      guaranteed: true,
      weight_target: 0,
      weight_hint: '0%',
      return_pct: basket.ret,
      signal_as_of: marked.mark_as_of || flex?.as_of || flexSessionTradeDate(),
      _key: pos.key || flexPositionKey(pos),
      _deskLocalRisk: true,
    });
  }
  return rows;
}

function flexSignalLocalKey(item, ledger) {
  return flexFindLocalPosition(item, ledger)?.key || item?._key || flexPositionKey(item);
}

function flexPendingOrderItem(order) {
  return {
    ...(order.item || {}),
    _key: order.position_key,
    _pendingOrderId: order.id,
    _pendingOrder: true,
    signal_as_of: order.signal_as_of,
    execution_date: order.execution_date,
  };
}

function flexSyncRealPendingOrders(
  ledger,
  closeCandidates,
  rebalanceCandidates,
  blockedRebalanceKeys = new Set(),
  rebalanceSnapshot = null,
) {
  if (isFlexSimBook()) {
    const closeKeys = new Set(closeCandidates.map(item => flexSignalLocalKey(item, ledger)));
    return {
      ledger,
      closes: closeCandidates,
      rebalances: rebalanceCandidates.filter(item => !closeKeys.has(flexSignalLocalKey(item, ledger))),
    };
  }

  const next = normalizeFlexLedger(ledger, 'real');
  const before = JSON.stringify(next.pending_orders || {});
  const orders = { ...(next.pending_orders || {}) };
  const openKeys = new Set(flexOpenPositions(next).map(pos => pos.key || flexPositionKey(pos)));

  for (const [id, order] of Object.entries(orders)) {
    if (!order || !openKeys.has(order.position_key)) delete orders[id];
  }

  for (const item of closeCandidates) {
    const positionKey = flexSignalLocalKey(item, next);
    if (!openKeys.has(positionKey)) continue;
    const id = `close:${positionKey}`;
    if (orders[id]) continue;
    const signalAsOf = String(item.signal_as_of || next.mark_as_of || flexSessionTradeDate()).slice(0, 10);
    orders[id] = {
      id,
      kind: 'close',
      position_key: positionKey,
      signal_as_of: signalAsOf,
      execution_date: flexAddTradingDays(signalAsOf, 1),
      created_at: new Date().toISOString(),
      status: 'PENDING',
      item: { ...item, _key: positionKey },
    };
    delete orders[`rebalance:${positionKey}`];
  }

  const pendingCloseKeys = new Set(Object.values(orders)
    .filter(order => order?.kind === 'close')
    .map(order => order.position_key));
  for (const key of blockedRebalanceKeys) pendingCloseKeys.add(key);
  if (rebalanceSnapshot?.fresh && rebalanceSnapshot.signalAsOf) {
    const currentKeys = new Set(rebalanceCandidates.map(item => flexSignalLocalKey(item, next)));
    for (const [id, order] of Object.entries(orders)) {
      if (
        order?.kind === 'rebalance'
        && !currentKeys.has(order.position_key)
        && String(order.signal_as_of || '') <= String(rebalanceSnapshot.signalAsOf)
      ) {
        delete orders[id];
      }
    }
  }
  for (const item of rebalanceCandidates) {
    const positionKey = flexSignalLocalKey(item, next);
    if (!openKeys.has(positionKey) || pendingCloseKeys.has(positionKey)) continue;
    const id = `rebalance:${positionKey}`;
    const signalAsOf = String(item.signal_as_of || next.mark_as_of || flexSessionTradeDate()).slice(0, 10);
    const existing = orders[id];
    if (existing && String(existing.signal_as_of || '') > signalAsOf) continue;
    orders[id] = {
      id,
      kind: 'rebalance',
      position_key: positionKey,
      signal_as_of: signalAsOf,
      execution_date: flexAddTradingDays(signalAsOf, 1),
      created_at: existing?.created_at || new Date().toISOString(),
      status: 'PENDING',
      item: { ...item, _key: positionKey },
    };
  }

  for (const [id, order] of Object.entries(orders)) {
    if (order?.kind === 'rebalance' && pendingCloseKeys.has(order.position_key)) delete orders[id];
  }
  next.pending_orders = orders;
  const changed = before !== JSON.stringify(orders);
  const saved = changed ? saveFlexLedger(next) : next;
  const values = Object.values(saved.pending_orders || {});
  return {
    ledger: saved,
    closes: values.filter(order => order.kind === 'close').map(flexPendingOrderItem),
    rebalances: values.filter(order => order.kind === 'rebalance').map(flexPendingOrderItem),
  };
}

function flexClearPendingOrder(orderId) {
  if (!orderId || isFlexSimBook()) return;
  const ledger = loadFlexLedger();
  if (!ledger.pending_orders?.[orderId]) return;
  delete ledger.pending_orders[orderId];
  saveFlexLedger(ledger);
}

/** Split engine lists into desk buckets — personal book only. */
function splitFlexSignalBuckets(flex) {
  const f = flex || {};
  const closeKeys = new Set(['CLOSE', 'SELL']);
  const avoidKeys = new Set(['AVOID', 'UNDERWEIGHT_RELATIVE']);

  const buckets = { open: [], rebalance: [], hold: [], close: [], avoid: [] };
  const seen = { open: new Set(), rebalance: new Set(), hold: new Set(), close: new Set(), avoid: new Set() };
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

  const closeCandidates = [];
  const paperCloseRows = [];
  // Local satellite risk exits have the clearest user-facing reason; show before paper exits.
  closeCandidates.push(...deskLocalRiskCloses(f, ledger));

  // Target-weight transitions must be executable as a pair. In aggressive mode,
  // single-sleeve 100% → dual-sleeve 60/40 requires reducing the old sleeve
  // before the new sleeve can be funded.
  const rebalance = deskLocalRebalanceActions(f, ledger);
  const rebalanceCandidates = [...rebalance.reduces, ...rebalance.adds];

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
      const row = {
        ...item,
        signal_as_of: item.signal_as_of || asOf,
        _strategyPaper: !held,
        _deskForceShow: true,
        action_cn: held
          ? (item.action_cn || '平仓')
          : `策略纸面·${item.action_cn || item.close_code || '平仓'}`,
        why: item.why || item.close_code || '策略退出',
      };
      if (held) closeCandidates.push(row);
      else paperCloseRows.push(row);
    }
  }
  // Personal hold-days expired (from the day user clicked 买) — always.
  closeCandidates.push(...deskLocalDueCloses(ledger));

  const avoidRows = [];
  for (const item of f.avoid_list || []) {
    const action = String(item.action || item.side || '').toUpperCase();
    if (!avoidKeys.has(action) && action !== 'FLAT') continue;
    if (!flexIsLocallyHeld(item, ledger)) continue;
    avoidRows.push({
      ...item,
      _simAuto: false,
      action_cn: isFlexSimBook()
        ? '策略回避提示·等待状态机确认'
        : (item.action_cn || '回避/条件减配'),
    });
  }
  const avoidLocalKeys = new Set(avoidRows.map(item => flexSignalLocalKey(item, ledger)));
  const compatibleRebalances = isFlexSimBook()
    ? rebalanceCandidates
    : rebalanceCandidates.filter(item => !avoidLocalKeys.has(flexSignalLocalKey(item, ledger)));
  const synced = flexSyncRealPendingOrders(
    ledger,
    closeCandidates,
    compatibleRebalances,
    avoidLocalKeys,
    { fresh: rebalance.fresh, signalAsOf: rebalance.signalAsOf },
  );
  const syncedCloseKeys = new Set(synced.closes.map(item => flexSignalLocalKey(item, synced.ledger)));
  const visibleAvoidRows = avoidRows.filter(item => !syncedCloseKeys.has(flexSignalLocalKey(item, synced.ledger)));
  for (const item of synced.rebalances) pushUnique('rebalance', item);
  for (const item of [...synced.closes, ...paperCloseRows]) pushUnique('close', item);

  // AVOID: real book can act; sim book only explains the advisory until position_state exits.
  for (const item of visibleAvoidRows) pushUnique('avoid', item);

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
    const rowSignalAsOf = item.signal_as_of || signalAsOf;
    const localMatch = flexFindLocalPosition(item, ledger);
    const held = !!localMatch;
    const key = localMatch?.key || signalKey;
    const isHoldRow = forceKind === 'hold' || action === 'HOLD';
    const badgeInfo = isHoldRow
      ? flexActionBadge({ ...item, action: 'HOLD' }, flex, { localHeld: held, signalAsOf })
      : flexActionBadge(item, flex, {
        localHeld: held,
        signalAsOf: rowSignalAsOf,
      });
    const suggested = item.rebalance_amount != null
      ? Number(item.rebalance_amount)
      : flexSuggestedAmount(item, flexEquity(flexApplyDisplayMarksToLedger(ledger)), ledger, localMatch?.position || null);
    const etfCode = item.etf_code || '';
    const name = item.name || '—';
    const w = item.weight_hint || (item.weight_target != null ? pctLabel(item.weight_target) : '—');
    const amt = suggested != null ? formatMoney(suggested) : '—';
    // 清仓列：本机持仓用个人 exit 计划；策略平仓提示用「下一交易日开盘」
    const localPos = localMatch?.position || null;
    let left = '—';
    if (item._pendingOrder && item.execution_date) {
      const executionDate = String(item.execution_date).slice(0, 10);
      const sessionDate = flexSessionTradeDate();
      const shanghai = getShanghaiDateParts();
      if (sessionDate > executionDate) left = `逾期待执行 · ${executionDate.slice(5)}`;
      else if (sessionDate === executionDate && shanghai.minutes >= 9 * 60 + 30) left = '今日开盘指令 · 待记账';
      else left = `待${executionDate.slice(5)}开盘`;
    } else if (held) left = flexPositionExitInfo(localPos).label;
    else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
      left = item.entry && item.entry !== '—' ? String(item.entry) : '下一交易日开盘';
    } else if (String(item.sleeve || '').toLowerCase() === 'core' && (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action))) {
      left = item.entry || 'T+1开盘';
    } else if (String(item.sleeve || '').toLowerCase() === 'satellite' && (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action))) {
      left = item.risk_rule_cn || item.exit || flexSatelliteRiskRule(flex).ruleCn;
    }
    const isAvoid = forceKind === 'avoid' || action === 'AVOID' || action === 'UNDERWEIGHT_RELATIVE' || action === 'FLAT';
    const isRebalanceAdd = action === 'REBALANCE_ADD';
    const isRebalanceReduce = action === 'REBALANCE_REDUCE';
    const pendingOrderAttr = item._pendingOrderId
      ? ` data-pending-order-id="${escapeHtml(item._pendingOrderId)}"`
      : '';
    const executionSignalDay = String(item.signal_as_of || signalAsOf || flex?.as_of || '').slice(0, 10);
    const executionLag = flexBookLagDays(executionSignalDay);
    const strictExecutionReady = executionLag != null && executionLag >= 1;
    // Avoid rows only appear when held; strategy CLOSE always listed (tip if not held).
    const interactive = !isAvoid || held;

    // Buy plan starts when user confirms (today's bookkeeping); full default hold window.
    const planDays = item.hold_days != null
      ? Number(item.hold_days)
      : (String(item.sleeve || '').toLowerCase() === 'satellite' ? 8 : (options.defaultHoldDays != null ? Number(options.defaultHoldDays) : null));

    let acts = '';
    if (interactive) {
      if (isFlexSimBook()) {
        // Sim book follows authoritative position_state; advisory AVOID stays a visible warning.
        if (isAvoid) {
          acts = held
            ? '<span class="flex-chip ghost" title="模拟仓等待策略状态机确认退出，不提前改写纸面持仓">策略提示</span>'
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
      } else if (isRebalanceReduce) {
        acts = held && strictExecutionReady
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}" data-suggested="${suggested || ''}"${pendingOrderAttr}>按目标减</button>`
          : '<span class="flex-row-muted" title="T收盘确认，下一交易日开盘执行">待下个开盘</span>';
      } else if (forceKind === 'close' || FLEX_CLOSE_ACTIONS.has(action)) {
        acts = held && strictExecutionReady
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}"${pendingOrderAttr}>减</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}"${pendingOrderAttr}>平</button>`
          : held
            ? '<span class="flex-row-muted" title="T收盘确认，下一交易日开盘执行">待下个开盘</span>'
            : '<span class="flex-row-muted" title="未点买，仅策略提示">仅提示</span>';
      } else if (isRebalanceAdd) {
        acts = held && strictExecutionReady
          ? `<button type="button" class="flex-chip primary" data-flex-act="add" data-pos-key="${escapeHtml(key)}" data-suggested="${suggested || ''}"${pendingOrderAttr}>按目标加</button>`
          : '<span class="flex-row-muted" title="T收盘确认，下一交易日开盘执行">待T+1开盘</span>';
      } else if (held) {
        acts = `<button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(key)}">加</button>
          <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">减</button>
          <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">平</button>`;
      } else if (forceKind === 'open' || FLEX_BUY_ACTIONS.has(action)) {
        const tailEntry = item.execution_mode === 'T_TAIL_1450';
        const executionReady = tailEntry || strictExecutionReady;
        acts = executionReady ? `<button type="button" class="flex-chip primary"
          data-flex-act="buy"
          data-pos-key="${escapeHtml(key)}"
          data-name="${escapeHtml(item.name || '')}"
          data-etf-code="${escapeHtml(etfCode)}"
          data-etf-name="${escapeHtml(item.etf_name || '')}"
          data-sleeve="${escapeHtml(item.sleeve || '')}"
          data-suggested="${suggested != null ? suggested : ''}"
          data-signal-as-of="${escapeHtml(rowSignalAsOf)}"
          data-hold-days="${planDays != null ? planDays : ''}"
          data-execution-mode="${escapeHtml(item.execution_mode || '')}"
        >${tailEntry ? '记尾盘买入' : '记买入'}</button>`
          : '<span class="flex-row-muted" title="T收盘确认，T+1开盘后记录实际成交">待T+1开盘</span>';
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
  const buckets = options.buckets || splitFlexSignalBuckets(flex || {});
  const defaultHoldDays = flex?.hold_days != null ? Number(flex.hold_days) : 5;
  // hold bucket intentionally omitted (paper HOLD is not a desk action)
  const map = [
    { kind: 'open', id: 'flexOpenList', forceKind: 'open', countId: 'flexOpenCount' },
    { kind: 'rebalance', id: 'flexRebalanceList', forceKind: 'rebalance', countId: 'flexRebalanceCount' },
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
      : (flexCoreTailActionableNow(flex?.core_tail_signal)
        ? '真实仓：<strong>CORE严格尾盘窗口已开启</strong>；510300请在15:00前按实际成交价记账。其他信号仍按T+1。'
        : '真实仓：信号日 <strong>T</strong> 与 <strong>T+1</strong> 可点「记买入」；未点则 <strong>T+2</strong> 消失。');
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
    actionable += [...el.querySelectorAll('.flex-row')]
      .filter(row => row.querySelector('[data-flex-act]')).length;
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
    if (isFlexSimBook()) {
      flexToast('模拟仓由策略自动维护，不能手动清空', 'warn', 2800);
      return;
    }
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
  document.getElementById('flexModalReduceMode')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-flex-reduce-mode]');
    if (!btn) return;
    setFlexReduceMode(btn.dataset.flexReduceMode);
    const focusId = btn.dataset.flexReduceMode === 'amount' ? 'flexModalAmount' : 'flexModalPct';
    document.getElementById(focusId)?.focus();
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
    if (isFlexSimBook()) {
      flexToast('模拟仓为只读账本，由策略状态机自动维护', 'warn', 2800);
      return;
    }
    const act = btn.dataset.flexAct;
    const key = btn.dataset.posKey;
    const ledger = loadFlexLedger();

    if (act === 'buy') {
      const suggested = btn.dataset.suggested ? Number(btn.dataset.suggested) : null;
      const isAdd = ledger.positions[key] && Number(ledger.positions[key].qty) > 0;
      const codeName = `${btn.dataset.etfCode || ''} ${btn.dataset.name || ''}`.trim();
      const executionMode = btn.dataset.executionMode || '';
      const code = String(btn.dataset.etfCode || '').replace(/\D/g, '').padStart(6, '0');
      const realtimePrice = Number(dashboardState.flexRealtimeQuotes?.quotes?.[code]?.price);
      openFlexTradeModal({
        mode: isAdd ? 'add' : 'buy',
        title: isAdd ? '加仓记账' : '买入记账',
        subtitle: `${codeName}${suggested ? ` · 建议 ${formatMoney(suggested)}` : ''} · ${executionMode === 'T_TAIL_1450' ? 'T日尾盘实际成交价' : '请填实际成交价'}`,
        key,
        name: btn.dataset.name || '',
        etf_code: btn.dataset.etfCode || '',
        etf_name: btn.dataset.etfName || '',
        sleeve: btn.dataset.sleeve || '',
        signal_as_of: btn.dataset.signalAsOf || '',
        hold_days: btn.dataset.holdDays !== '' && btn.dataset.holdDays != null
          ? Number(btn.dataset.holdDays)
          : null,
        execution_mode: executionMode,
        defaultAmount: suggested,
        defaultPrice: (realtimePrice > 0 ? realtimePrice : null)
          || ledger.positions[key]?.last_price
          || ledger.positions[key]?.avg_price
          || null,
      });
      return;
    }

    if (act === 'add' || act === 'reduce' || act === 'close') {
      const pos = ledger.positions[key];
      if (!pos || !(Number(pos.qty) > 0)) return;
      const sub = `${pos.etf_code || pos.name || ''} · 成本 ${formatMoney(pos.cost_basis)}`;
      const referencePrice = flexExecutionReferencePrice(ledger, key);
      if (act === 'add') {
        const suggested = btn.dataset.suggested ? Number(btn.dataset.suggested) : null;
        openFlexTradeModal({
          mode: 'add',
          title: '加仓记账',
          subtitle: sub,
          key,
          name: pos.name,
          etf_code: pos.etf_code,
          etf_name: pos.etf_name,
          sleeve: pos.sleeve,
          pendingOrderId: btn.dataset.pendingOrderId || '',
          defaultAmount: suggested,
          defaultPrice: referencePrice,
        });
      } else if (act === 'reduce') {
        const suggested = btn.dataset.suggested ? Number(btn.dataset.suggested) : null;
        openFlexTradeModal({
          mode: 'reduce',
          title: '减仓记账',
          subtitle: sub,
          key,
          pendingOrderId: btn.dataset.pendingOrderId || '',
          defaultAmount: suggested,
          defaultPrice: referencePrice,
        });
      } else {
        openFlexTradeModal({
          mode: 'close',
          title: '平仓记账',
          subtitle: sub,
          key,
          pendingOrderId: btn.dataset.pendingOrderId || '',
          defaultPrice: referencePrice,
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
  const tailSignal = dashboardState.intradayTemperature?.core_tail_signal || null;
  renderFlexCoreTailAlert(tailSignal);
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
  flex = flexWithCoreTailSignal(flex, tailSignal);
  // Product lock: only aggressive Flex sizing is exposed in the app; backend is the sizing source of truth.
  const mode = 'aggressive';
  dashboardState.flexMode = 'aggressive';
  if (flex.mode !== 'aggressive') {
    flex = applyFlexModeOverlay(flex, 'aggressive');
  }
  if (flexCoreTailActionableNow(tailSignal)) {
    flex = applyFlexModeOverlay(flex, 'aggressive');
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
    else if (lag === 1) label = `${label}·EOD`;
    else if (lag != null && lag > 1) label = `${label}·滞${lag}d`;
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
  setText('flexHold', `核心${flex.hold_days || 5}日 · 卫星满${FLEX_SAT_MIN_HOLD_DAYS}日查 ${flexFormatSignedPct(satRule.stopLoss, 0)}/${flexFormatSignedPct(satRule.takeProfit, 0)} · 最长${flex.hold_days_sat || flex.satellite?.hold_days || 8}日`);
  // Win rate + ann return (1bp baseline when present in stats)
  const win = full.win_rate;
  const ann = full.ann_return;
  if (win != null && Number.isFinite(Number(win)) && ann != null && Number.isFinite(Number(ann))) {
    setText('flexStatsShort', `胜率${(Number(win) * 100).toFixed(0)}% · 年化${(Number(ann) * 100).toFixed(0)}%`);
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
  const signalBuckets = splitFlexSignalBuckets(flex);
  const openNow = signalBuckets.open || [];
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
  const markedLedger = flexApplyDisplayMarksToLedger(ledgerNow);
  const ledgerEquity = flexEquity(markedLedger);
  const coreLocal = flexFindLocalPosition({ sleeve: 'core', name: '沪深300', etf_code: core.etf_code || '510300' }, markedLedger);
  const coreLocalKey = coreLocal?.key || null;
  const satLocalKeys = new Set(flexOpenPositions(ledgerNow)
    .filter(pos => String(pos.sleeve || '') === 'satellite')
    .map(pos => pos.key || flexPositionKey(pos)));
  const rowLocalKey = row => flexSignalLocalKey(row, ledgerNow);
  const coreClosingLocal = (signalBuckets.close || []).some(row => (
    (coreLocalKey && rowLocalKey(row) === coreLocalKey)
    || String(row.sleeve || '') === 'core'
  ));
  const satClosingLocal = (signalBuckets.close || []).some(row => (
    satLocalKeys.has(rowLocalKey(row))
    || String(row.sleeve || '') === 'satellite'
  ));
  const coreAvoidLocal = (signalBuckets.avoid || []).some(row => coreLocalKey && rowLocalKey(row) === coreLocalKey);
  const satAvoidLocal = (signalBuckets.avoid || []).some(row => satLocalKeys.has(rowLocalKey(row)));
  const currentWeight = pos => {
    if (!pos || !(ledgerEquity > 0)) return null;
    const px = Number(pos.last_price) > 0 ? Number(pos.last_price) : Number(pos.avg_price);
    const value = Number(pos.qty) > 0 && px > 0 ? Number(pos.qty) * px : Number(pos.cost_basis) || 0;
    return value / ledgerEquity;
  };
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
  const paperCoreClosing = (flex.close_list || []).some(item => String(item.sleeve || '') === 'core');
  const paperSatClosing = (flex.close_list || []).some(item => String(item.sleeve || '') === 'satellite');
  let coreActionLabel = '观望';
  if (isFlexSimBook()) {
    if (coreClosingLocal || (paperCoreOpen && String(core.action || '').toUpperCase() === 'CLOSE')) coreActionLabel = '策略待平';
    else if (coreAvoidLocal) coreActionLabel = '持有·回避提示';
    else if (coreHeld || paperCoreOpen) coreActionLabel = '策略持有';
    else if (coreOpenNow) coreActionLabel = '策略可开';
  } else {
    if (coreHeld && coreClosingLocal) coreActionLabel = '待平仓';
    else if (coreHeld && coreAvoidLocal) coreActionLabel = '待回避';
    else if (coreHeld) coreActionLabel = '已持有';
    else if (coreOpenNow) coreActionLabel = flexCoreTailActionableNow(tailSignal)
      ? '14:50尾盘买'
      : '可买·T～T+1';
    else if (paperCoreOpen) coreActionLabel = paperCoreClosing ? '纸面待平·未记' : '纸面持有·未记';
  }
  setText('flexCoreAction', coreActionLabel);
  const coreActualWeight = currentWeight(coreLocal?.position);
  setText(
    'flexCoreWeight',
    coreActualWeight != null
      ? `实际${pctLabel(coreActualWeight)}`
      : (wCore != null ? `目标${pctLabel(wCore)}` : (core.etf_code || '—'))
  );
  let satActionLabel = '空仓';
  if (isFlexSimBook()) {
    if (satClosingLocal || paperSatClosing) satActionLabel = '策略待平';
    else if (satAvoidLocal) satActionLabel = '持有·回避提示';
    else if (satHeld || paperSatOpen) satActionLabel = '策略持有';
    else if (satOpenNow) satActionLabel = '策略可开';
  } else {
    if (satHeld && satClosingLocal) satActionLabel = '待平仓';
    else if (satHeld && satAvoidLocal) satActionLabel = '待回避';
    else if (satHeld) satActionLabel = '已持有';
    else if (satOpenNow) satActionLabel = '可买·T～T+1';
    else if (paperSatOpen) satActionLabel = paperSatClosing ? '纸面待平·未记' : '纸面持有·未记';
  }
  setText('flexSatStage', satActionLabel);
  const satActualValue = flexOpenPositions(markedLedger)
    .filter(pos => String(pos.sleeve || '') === 'satellite')
    .reduce((sum, pos) => {
      const px = Number(pos.last_price) > 0 ? Number(pos.last_price) : Number(pos.avg_price);
      return sum + (Number(pos.qty) > 0 && px > 0 ? Number(pos.qty) * px : Number(pos.cost_basis) || 0);
    }, 0);
  const satActualWeight = ledgerEquity > 0 && satActualValue > 0 ? satActualValue / ledgerEquity : null;
  setText('flexSatWeight', satActualWeight != null ? `实际${pctLabel(satActualWeight)}` : (wSat != null ? `目标${pctLabel(wSat)}` : '—'));
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

  renderFlexSignalList(flex, { signalAsOf: asOf, buckets: signalBuckets });
  renderFlexExecUi();
  flexEnsureRealtimeQuotePolling();
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

async function refreshDashboardOnce({ forceFull = false } = {}) {
  let dataChanged = false;
  try {
    if (!forceFull && dashboardState.lastUpdateTime) {
      const [latest, buildInfo] = await Promise.all([
        loadJSON('./data/latest.json', { fresh: true }),
        loadJSON('./data/build_info.json', { fresh: true }).catch(() => null),
      ]);
      const buildTime = buildInfo?.build_time || null;
      if (
        latest?.update_time === dashboardState.lastUpdateTime
        && latest?.trade_date === dashboardState.lastTradeDate
        && (!buildTime || buildTime === dashboardState.lastBuildTime)
      ) {
        updateFreshness(latest);
        updateRefreshStatus('ok', '数据未变化，跳过全量刷新');
        return;
      }
      // New data: refresh cache bust and reload critical + heavy.
      dashboardState.cacheBust = dashboardDataRevision({
        buildTime,
        updateTime: latest?.update_time || latest?.as_of || null,
        tradeDate: latest?.trade_date || null,
      });
      if (buildTime) dashboardState.lastBuildTime = buildTime;
      dataChanged = true;
    } else {
      await resolveCacheBust();
    }

    const fresh = forceFull || dataChanged;
    const critical = await loadCriticalDashboardData({ fresh });
    renderCriticalDashboard(critical);
    const heavy = await loadHeavyDashboardData({ fresh });
    renderHeavyDashboard(heavy);
    updateRefreshStatus('ok');
  } catch (err) {
    console.error(err);
    updateRefreshStatus('error', err.message || String(err));
    showLoadError(err.message || String(err));
  }
}

function refreshDashboard({ forceFull = false } = {}) {
  if (dashboardState.refreshPromise) {
    if (forceFull) dashboardState.forceRefreshQueued = true;
    return dashboardState.refreshPromise;
  }

  dashboardState.forceRefreshQueued = Boolean(forceFull);
  dashboardState.refreshInFlight = true;
  const operation = (async () => {
    try {
      do {
        const runForced = dashboardState.forceRefreshQueued;
        dashboardState.forceRefreshQueued = false;
        await refreshDashboardOnce({ forceFull: runForced });
      } while (dashboardState.forceRefreshQueued);
    } finally {
      dashboardState.refreshInFlight = false;
      dashboardState.refreshPromise = null;
    }
  })();
  dashboardState.refreshPromise = operation;
  return operation;
}

const APP_VIEW_KEY = 'ashare_app_view_v1';

function resizeVisibleCharts() {
  try {
    dashboardState.componentChart?.resize();
    dashboardState.intradayTemperatureChart?.resize();
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
  if (view === 'flex') flexEnsureRealtimeQuotePolling();
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
  if (location.hostname.endsWith('.github.io')) {
    dashboardState.dataPlane.available = false;
    dashboardState.dataPlane.status = null;
    renderDataPlaneBar(null);
    return null;
  }
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

async function findReusableGithubActionsRun(workflow, mode, baselineTime) {
  const pat = getGithubActionsPat();
  const cfg = dashboardState.dataPlane.actions;
  const url = `https://api.github.com/repos/${cfg.owner}/${cfg.repo}/actions/workflows/${workflow}/runs`
    + `?branch=${encodeURIComponent(cfg.ref)}&per_page=10`;
  try {
    const res = await fetch(url, {
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${pat}`,
        'X-GitHub-Api-Version': '2022-11-28',
      },
      cache: 'no-store',
    });
    if (!res.ok) return null;
    const body = await res.json();
    const active = (body.workflow_runs || []).filter(run =>
      ['queued', 'in_progress', 'waiting', 'pending', 'requested'].includes(String(run.status || ''))
    );
    const baselineMs = Date.parse(baselineTime || '');
    return active.find(run => {
      if (run.status !== 'in_progress') return true;
      const startedMs = Date.parse(run.run_started_at || run.created_at || '');
      return !Number.isFinite(baselineMs)
        || !Number.isFinite(startedMs)
        || baselineMs < startedMs;
    }) || null;
  } catch (err) {
    console.warn(`Unable to inspect active ${mode} workflow`, err);
    return null;
  }
}

async function dispatchGithubActionsWorkflow(mode, { baselineTime = null } = {}) {
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
  const activeRun = await findReusableGithubActionsRun(workflow, mode, baselineTime);
  if (activeRun) {
    return { ok: true, workflow, reused: true, runId: activeRun.id };
  }
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
  maxWaitMs = 15 * 60 * 1000,
  intervalMs = 12000,
  onTick = null,
} = {}) {
  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < maxWaitMs) {
    attempt += 1;
    if (onTick) onTick(attempt, Math.round((Date.now() - started) / 1000));
    await new Promise(r => setTimeout(r, intervalMs));
    try {
      const [info, latest] = await Promise.all([
        loadJSON('./data/build_info.json', { fresh: true }).catch(() => null),
        loadJSON('./data/latest.json', { fresh: true }).catch(() => null),
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

function dashboardMatchesPublishedRevision(result) {
  if (!result) return true;
  if (result.updateTime && dashboardState.lastUpdateTime !== result.updateTime) return false;
  if (!result.updateTime && result.buildTime && dashboardState.lastBuildTime !== result.buildTime) return false;
  return true;
}

async function syncDashboardToPublishedRevision(result, { maxAttempts = 4, intervalMs = 1500 } = {}) {
  const attempts = result ? maxAttempts : 1;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    await refreshDashboard({ forceFull: true });
    if (dashboardMatchesPublishedRevision(result)) return true;
    if (attempt < attempts) await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  return false;
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
        const info = await loadJSON('./data/build_info.json', { fresh: true });
        beforeBuildTime = info?.build_time || null;
      } catch (_) { /* ignore */ }
      try {
        const latest = await loadJSON('./data/latest.json', { fresh: true });
        beforeUpdateTime = latest?.update_time || null;
      } catch (_) { /* ignore */ }

      paintStaticPagesPlaneMeta(null, { busy: true, note: `触发 ${label} Actions…` });
      const dispatch = await dispatchGithubActionsWorkflow(mode, {
        baselineTime: mode === 'full' ? beforeBuildTime : beforeUpdateTime,
      });
      paintStaticPagesPlaneMeta(null, {
        busy: true,
        note: dispatch.reused
          ? `${label}已有任务运行 · 正在跟踪本次发布…`
          : `${label} 已排队 · 等待发布…`,
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

      const synced = await syncDashboardToPublishedRevision(result);
      if (result) {
        const renderedLatest = dashboardState.latest || result.latest || {};
        paintStaticPagesPlaneMeta({
          risk_temperature: renderedLatest.risk_temperature
            ?? document.getElementById('riskTemperature')?.textContent,
          trade_date: renderedLatest.trade_date
            ?? document.getElementById('tradeDate')?.textContent,
          temperature_mode_cn: renderedLatest.temperature_mode_cn
            || renderedLatest.temperature_mode
            || document.getElementById('quality')?.textContent,
        }, {
          note: synced
            ? `${label} 完成 · 页面已同步最新数据`
            : `${label} 已发布 · 页面数据仍在同步，请稍候`,
        });
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
    let refreshStatus = payload?.status || null;
    if (mode === 'full' && res.status === 202) {
      for (let i = 0; i < 90; i++) {
        await new Promise(r => setTimeout(r, 2000));
        refreshStatus = await fetchDataPlaneStatus();
        if (refreshStatus && !refreshStatus.refresh_running) break;
      }
      if (refreshStatus?.refresh_running) throw new Error('日更任务超时，仍在后台运行');
    } else if (!res.ok && !payload.ok) {
      throw new Error(payload.error || payload.detail || ('refresh failed ' + res.status));
    }
    const targetRevision = {
      updateTime: refreshStatus?.latest?.update_time || null,
      buildTime: refreshStatus?.build_info?.build_time || null,
    };
    const synced = await syncDashboardToPublishedRevision(targetRevision);
    const finalStatus = await fetchDataPlaneStatus();
    if (!synced && metaEl) metaEl.textContent = '刷新完成，但页面数据同步超时';
    if (finalStatus?.last_error) throw new Error(finalStatus.last_error);
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
