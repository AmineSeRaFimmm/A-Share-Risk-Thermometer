const AUTO_REFRESH_MS = 60 * 1000;
const STALE_THRESHOLD_MS = 15 * 60 * 1000;
const dashboardState = {
  activeRange: '1Y',
  componentChart: null,
  sectorChart: null,
  lowPositionChart: null,
  timeCharts: [],
  history: [],
  nowcastHistory: {},
  strategy: {},
  refreshInFlight: false,
  cacheBust: null,
  lastUpdateTime: null,
  lastTradeDate: null,
  heavyLoaded: false,
  flexMode: 'conservative',
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

function formatCorr(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(3) : '--';
}

function formatSignedPct(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  const pct = numeric * 100;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function sectorRow(row, direction) {
  const tone = direction === 'positive' ? 'positive' : 'negative';
  return `<div class="sector-item" data-tone="${tone}">
    <div>
      <strong>${row.name || '--'}</strong>
      <span>${row.symbol || '--'} / ${row.strength || '--'} / ${row.stability || '--'} / 样本 ${row.sample_size || '--'}</span>
    </div>
    <div class="sector-metrics">
      <b>${formatCorr(row.corr_temp_fwd_excess)}</b>
      <em>高风险 ${formatSignedPct(row.high_risk_avg_excess)} / n=${row.high_risk_sample ?? '--'}</em>
    </div>
  </div>`;
}

function renderSectorCorrelation(sector) {
  const panel = document.querySelector('.sector-panel');
  if (!panel) return;
  if (!sector?.rankings) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  setText('sectorAsOf', `申万一级 / ${sector.as_of || '--'} / 近一年5日超额收益`);
  const positive = sector.rankings.positive || [];
  const negative = sector.rankings.negative || [];
  const positiveBox = document.getElementById('sectorPositiveList');
  const negativeBox = document.getElementById('sectorNegativeList');
  if (positiveBox) {
    positiveBox.innerHTML = positive.slice(0, 5).map(row => sectorRow(row, 'positive')).join('');
  }
  if (negativeBox) {
    negativeBox.innerHTML = negative.slice(0, 5).map(row => sectorRow(row, 'negative')).join('');
  }
  const oldSectorChart = echarts.getInstanceByDom(document.getElementById('sectorCorrelationChart'));
  if (oldSectorChart) oldSectorChart.dispose();
  dashboardState.sectorChart = renderSectorCorrelationChart(sector);
}

function formatScore(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(1) : '--';
}

function formatPercentile(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : '--';
}

function lowPositionCard(row) {
  return `<div class="low-position-card">
    <div>
      <strong>${row.name || '--'}</strong>
      <span>${row.symbol || '--'} / ${row.relationship_type || '--'}</span>
    </div>
    <dl>
      <div><dt>低位分</dt><dd>${formatScore(row.low_position_score)}</dd></div>
      <div><dt>5Y分位</dt><dd>${formatPercentile(row.price_percentile_5y)}</dd></div>
      <div><dt>5Y回撤</dt><dd>${formatSignedPct(row.drawdown_5y)}</dd></div>
      <div><dt>PB</dt><dd>${row.pb ?? '--'}</dd></div>
    </dl>
  </div>`;
}

function renderLowPositionSectorStudy(study) {
  const panel = document.querySelector('.low-position-panel');
  if (!panel) return;
  if (!study?.selected_sectors?.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  setText('lowPositionAsOf', `申万一级 / ${study.as_of || '--'} / 近1Y-2Y关系`);
  const cards = document.getElementById('lowPositionCards');
  if (cards) cards.innerHTML = study.selected_sectors.map(lowPositionCard).join('');

  const signalRows = [];
  const selectedBySymbol = new Map(study.selected_sectors.map(row => [row.symbol, row]));
  (study.signals || []).forEach(row => {
    if (row.window === '1Y' && row.horizon === '20D' && row.signal === 'risk_pullback_after_high') {
      signalRows.push(row);
    }
  });
  const tbody = document.getElementById('lowPositionSignals');
  if (tbody) {
    tbody.innerHTML = signalRows.map(row => {
      const selected = selectedBySymbol.get(row.symbol) || {};
      return `<tr>
        <td>${row.name || '--'}</td>
        <td>${selected.relationship_type || '--'}</td>
        <td>${formatSignedPct(row.avg_fwd_excess)}</td>
        <td>${row.sample_size ?? '--'}</td>
        <td>${formatPercentile(row.win_rate)}</td>
      </tr>`;
    }).join('');
  }

  const lowPositionDom = document.getElementById('lowPositionChart');
  const oldLowPositionChart = echarts.getInstanceByDom(lowPositionDom);
  if (oldLowPositionChart) oldLowPositionChart.dispose();
  dashboardState.lowPositionChart = renderLowPositionSectorChart(study);
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
  document.querySelectorAll('.flex-mode-btn').forEach(button => {
    button.addEventListener('click', () => {
      const mode = button.dataset.flexMode || 'conservative';
      dashboardState.flexMode = mode;
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
  const [strategy, sector, lowPosition, rtTactical, stagePlaybook] = await Promise.all([
    loadJSON('./data/strategy.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/sector_correlation.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/low_position_sector_study.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/rt_tactical.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/stage_playbook.json').catch(() => ({ status: 'missing' })),
  ]);
  dashboardState.heavyLoaded = true;
  return { strategy, sector, lowPosition, rtTactical, stagePlaybook };
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

function renderHeavyDashboard({ strategy, sector, lowPosition, rtTactical, stagePlaybook }) {
  dashboardState.strategy = strategy || {};
  renderFlexTradePanel(stagePlaybook);
  renderStrategy(strategy);
  renderSectorCorrelation(sector);
  renderLowPositionSectorStudy(lowPosition);
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
  OPEN: { text: '开', cls: 'buy' },
  OVERWEIGHT: { text: '超', cls: 'buy' },
  BUY: { text: '买', cls: 'buy' },
  HOLD: { text: '持', cls: 'hold' },
  CLOSE: { text: '平', cls: 'sell' },
  AVOID: { text: '避', cls: 'avoid' },
  FLAT: { text: '空', cls: 'wait' },
  SELL: { text: '卖', cls: 'sell' },
  OVERWEIGHT_RELATIVE: { text: '超', cls: 'buy' },
  UNDERWEIGHT_RELATIVE: { text: '低', cls: 'avoid' },
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
  if (existing && Number(existing.qty) > 0) {
    const newQty = Number(existing.qty) + qty;
    const newCost = Number(existing.cost_basis) + amount;
    existing.qty = newQty;
    existing.cost_basis = newCost;
    existing.avg_price = newCost / newQty;
    existing.last_price = price;
    existing.updated_at = new Date().toISOString();
    if (draft.signal_as_of) existing.signal_as_of = draft.signal_as_of;
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
  const equity = flexEquity(ledger);
  const capital = Number(ledger.capital) || 0;
  const exposureBase = equity > 0 ? equity : capital;
  const exposure = exposureBase > 0 ? flexMarkValue(ledger) / exposureBase : null;
  setText('flexExecDeployed', deployed > 0 || cash > 0 || capital > 0 ? formatMoney(deployed) : '—');
  setText('flexExecCash', capital > 0 || cash > 0 ? formatMoney(cash) : '—');
  setText('flexExecExposure', exposure != null ? pctLabel(exposure) : '—');
  setText('flexExecCount', String(flexOpenPositions(ledger).length));
  const cashEl = document.getElementById('flexExecCash');
  if (cashEl) cashEl.title = equity > 0 ? `权益 ${formatMoney(equity)}` : '';
}

function renderFlexHoldings() {
  const el = document.getElementById('flexHoldingsList');
  if (!el) return;
  const ledger = loadFlexLedger();
  const positions = flexOpenPositions(ledger);
  const capital = Number(ledger.capital) || 0;
  if (!positions.length) {
    el.innerHTML = '<div class="flex-order-empty">—</div>';
    return;
  }
  positions.sort((a, b) => (Number(b.cost_basis) || 0) - (Number(a.cost_basis) || 0));
  el.innerHTML = positions.map(pos => {
    const weight = capital > 0 ? (Number(pos.cost_basis) / capital) : null;
    const code = pos.etf_code || '—';
    const name = pos.name || '—';
    const mark = Number(pos.last_price);
    const mtm = Number.isFinite(mark) ? Number(pos.qty) * mark : null;
    const pnl = mtm != null ? mtm - Number(pos.cost_basis) : null;
    const pnlCls = pnl == null ? '' : pnl >= 0 ? 'up' : 'down';
    const pnlTxt = pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${formatMoney(pnl)}`;
    return `<div class="flex-row" data-pos-key="${escapeHtml(pos.key)}">
      <span class="flex-row-code">${escapeHtml(code)}</span>
      <span class="flex-row-name">${escapeHtml(name)}</span>
      <span class="flex-row-num">${formatMoney(pos.cost_basis)}</span>
      <span class="flex-row-num">${formatPrice(pos.avg_price)}</span>
      <span class="flex-row-num">${weight != null ? pctLabel(weight) : '—'}</span>
      <span class="flex-row-num flex-holding-pnl ${pnlCls}">${pnlTxt}</span>
      <span class="flex-row-acts">
        <button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(pos.key)}">+</button>
        <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(pos.key)}">−</button>
        <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(pos.key)}">×</button>
      </span>
    </div>`;
  }).join('');
}

function renderFlexJournal() {
  const el = document.getElementById('flexJournalList');
  if (!el) return;
  const ledger = loadFlexLedger();
  const rows = (ledger.journal || []).slice(0, 20);
  if (!rows.length) {
    el.innerHTML = '<div class="flex-order-empty">—</div>';
    return;
  }
  el.innerHTML = rows.map(row => {
    const when = row.ts
      ? new Date(row.ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      : '—';
    const label = row.type_cn || row.type || '—';
    const code = row.etf_code || row.name || '—';
    const pnl = row.pnl != null && Number.isFinite(Number(row.pnl))
      ? `${Number(row.pnl) >= 0 ? '+' : ''}${formatMoney(row.pnl)}`
      : '';
    return `<div class="flex-row flex-row-log">
      <span class="flex-row-tag">${escapeHtml(label)}</span>
      <span class="flex-row-code">${escapeHtml(code)}</span>
      <span class="flex-row-num">${formatMoney(row.amount)}</span>
      <span class="flex-row-num">${formatPrice(row.price)}</span>
      <span class="flex-row-num ${Number(row.pnl) >= 0 ? 'up' : Number(row.pnl) < 0 ? 'down' : ''}">${pnl || '—'}</span>
      <span class="flex-row-time">${when}</span>
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

  if (state.mode === 'buy' || state.mode === 'add') {
    if (amount > 0 && price > 0) {
      const qty = amount / price;
      const w = capital > 0 ? pctLabel(amount / capital) : '—';
      preview.textContent = `${formatShares(qty)} · ${w}`;
    } else {
      preview.textContent = '';
    }
    return;
  }
  if (state.mode === 'reduce') {
    const pos = loadFlexLedger().positions[state.key];
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
    preview.textContent = sellQty > 0
      ? `−${formatShares(sellQty)} / ${formatMoney(sellAmt)} → ${formatShares(Math.max(0, Number(pos.qty) - sellQty))}`
      : '';
    return;
  }
  if (state.mode === 'close') {
    const pos = loadFlexLedger().positions[state.key];
    if (!pos) {
      preview.textContent = '—';
      return;
    }
    if (price > 0) {
      const amt = Number(pos.qty) * price;
      const pnl = amt - Number(pos.cost_basis);
      preview.textContent = `${formatMoney(amt)} · ${pnl >= 0 ? '+' : ''}${formatMoney(pnl)}`;
    } else {
      preview.textContent = '';
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

  if (spec.mode === 'buy' || spec.mode === 'add') {
    if (amountField) amountField.hidden = false;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = true;
    if (amountLabel) amountLabel.textContent = '金额';
    if (amountEl) amountEl.value = spec.defaultAmount != null ? String(spec.defaultAmount) : '';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  } else if (spec.mode === 'reduce') {
    if (amountField) amountField.hidden = false;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = false;
    if (amountLabel) amountLabel.textContent = '金额';
    if (amountEl) amountEl.value = '';
    if (pctEl) pctEl.value = '50';
    if (priceEl) priceEl.value = spec.defaultPrice != null ? String(spec.defaultPrice) : '';
  } else if (spec.mode === 'close') {
    if (amountField) amountField.hidden = true;
    if (priceField) priceField.hidden = false;
    if (pctField) pctField.hidden = true;
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

function flexSignalItemTitle(item) {
  const etfCode = item.etf_code || '';
  if (etfCode) return `${item.name || '—'} ${etfCode}`.trim();
  return item.name || item.instrument_display || '—';
}

function renderFlexOrderList(el, items, emptyText, options = {}) {
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = `<div class="flex-order-empty">${emptyText || '—'}</div>`;
    return;
  }
  const ledger = loadFlexLedger();
  const capital = Number(ledger.capital) || 0;
  const interactive = options.interactive !== false;
  const signalAsOf = options.signalAsOf || '';

  el.innerHTML = items.map(item => {
    const action = (item.action || item.side || '').toUpperCase();
    const badgeInfo = FLEX_ACTION_BADGE[action] || { text: action || '—', cls: 'wait' };
    const key = flexPositionKey(item);
    const held = ledger.positions[key] && Number(ledger.positions[key].qty) > 0;
    const suggested = flexSuggestedAmount(item, capital);
    const etfCode = item.etf_code || '';
    const name = item.name || '—';
    const w = item.weight_hint || (item.weight_target != null ? pctLabel(item.weight_target) : '—');
    const amt = suggested != null ? formatMoney(suggested) : '—';
    const left = item.days_remaining != null ? `${item.days_remaining}d` : '';

    let acts = '';
    if (interactive) {
      if (FLEX_BUY_ACTIONS.has(action) || (action === 'HOLD' && !held)) {
        acts = `<button type="button" class="flex-chip primary"
          data-flex-act="buy"
          data-pos-key="${escapeHtml(key)}"
          data-name="${escapeHtml(item.name || '')}"
          data-etf-code="${escapeHtml(etfCode)}"
          data-etf-name="${escapeHtml(item.etf_name || '')}"
          data-sleeve="${escapeHtml(item.sleeve || '')}"
          data-suggested="${suggested != null ? suggested : ''}"
          data-signal-as-of="${escapeHtml(signalAsOf)}"
        >${held ? '+' : '买'}</button>`;
      } else if (FLEX_CLOSE_ACTIONS.has(action)) {
        acts = held
          ? `<button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">−</button>
             <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">×</button>`
          : `<span class="flex-row-muted">—</span>`;
      } else if (action === 'HOLD' && held) {
        acts = `<button type="button" class="flex-chip" data-flex-act="add" data-pos-key="${escapeHtml(key)}">+</button>
          <button type="button" class="flex-chip" data-flex-act="reduce" data-pos-key="${escapeHtml(key)}">−</button>
          <button type="button" class="flex-chip danger" data-flex-act="close" data-pos-key="${escapeHtml(key)}">×</button>`;
      }
    }

    return `<div class="flex-row ${badgeInfo.cls}${held ? ' is-held' : ''}" title="${escapeHtml(flexSignalItemTitle(item))}">
      <span class="badge">${badgeInfo.text}</span>
      <span class="flex-row-code">${escapeHtml(etfCode || '—')}</span>
      <span class="flex-row-name">${escapeHtml(name)}</span>
      <span class="flex-row-num">${escapeHtml(w)}</span>
      <span class="flex-row-num">${amt}</span>
      <span class="flex-row-num flex-row-muted">${left}</span>
      <span class="flex-row-acts">${acts}</span>
    </div>`;
  }).join('');
}

function bindFlexTabs() {
  const panel = document.getElementById('flexTradePanel');
  if (!panel || panel.dataset.tabsBound === '1') return;
  panel.dataset.tabsBound = '1';
  panel.querySelectorAll('.flex-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const id = tab.dataset.flexTab;
      panel.querySelectorAll('.flex-tab').forEach(t => t.classList.toggle('active', t === tab));
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

  document.getElementById('flexModalCloseBtn')?.addEventListener('click', closeFlexTradeModal);
  document.getElementById('flexModalCancelBtn')?.addEventListener('click', closeFlexTradeModal);
  document.getElementById('flexModalConfirmBtn')?.addEventListener('click', confirmFlexTradeModal);
  document.getElementById('flexTradeModal')?.addEventListener('click', (ev) => {
    if (ev.target?.id === 'flexTradeModal') closeFlexTradeModal();
  });
  ['flexModalAmount', 'flexModalPrice', 'flexModalPct'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', updateFlexModalPreview);
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
      openFlexTradeModal({
        mode: ledger.positions[key] && Number(ledger.positions[key].qty) > 0 ? 'add' : 'buy',
        title: ledger.positions[key] && Number(ledger.positions[key].qty) > 0 ? '+' : '买',
        subtitle: `${btn.dataset.etfCode || btn.dataset.name || ''}`.trim(),
        key,
        name: btn.dataset.name || '',
        etf_code: btn.dataset.etfCode || '',
        etf_name: btn.dataset.etfName || '',
        sleeve: btn.dataset.sleeve || '',
        signal_as_of: btn.dataset.signalAsOf || '',
        defaultAmount: suggested,
        defaultPrice: ledger.positions[key]?.last_price || ledger.positions[key]?.avg_price || null,
      });
      return;
    }

    if (act === 'add' || act === 'reduce' || act === 'close') {
      const pos = ledger.positions[key];
      if (!pos || !(Number(pos.qty) > 0)) return;
      if (act === 'add') {
        openFlexTradeModal({
          mode: 'add',
          title: '+',
          subtitle: pos.etf_code || pos.name || '',
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
          title: '−',
          subtitle: pos.etf_code || pos.name || '',
          key,
          defaultPrice: pos.last_price || pos.avg_price,
        });
      } else {
        openFlexTradeModal({
          mode: 'close',
          title: '×',
          subtitle: pos.etf_code || pos.name || '',
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
  const coreOn = !!(flex.core && (flex.core.active || ['OPEN', 'HOLD'].includes(flex.core.action)));
  const satOn = !!(flex.satellite && flex.satellite.active);
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
    renderFlexOrderList(document.getElementById('flexMinimalList'), [], '—');
    renderFlexOrderList(document.getElementById('flexBuyList'), [], '—');
    renderFlexOrderList(document.getElementById('flexHoldList'), [], '—');
    renderFlexOrderList(document.getElementById('flexSellList'), [], '—');
    renderFlexOrderList(document.getElementById('flexAvoidList'), [], '—');
    dashboardState.flexActive = null;
    renderFlexExecUi();
    return;
  }

  dashboardState.flexPlaybook = playbook;
  const mode = dashboardState.flexMode || flex.mode || 'conservative';
  flex = applyFlexModeOverlay(flex, mode);
  dashboardState.flexActive = flex;

  const stats = flexModeStats(flex, mode);
  const { full } = stats;
  const asOf = flex.as_of || playbook?.as_of || '';
  setText('flexStatus', flex.status || '—');
  const asOfEl = document.getElementById('flexAsOf');
  if (asOfEl) {
    asOfEl.hidden = !asOf;
    asOfEl.textContent = asOf ? asOf.slice(5) : '';
    asOfEl.title = asOf;
  }
  setText('flexHeadline', flex.headline || '');
  setText('flexAlloc', flex.allocation_cn || '');
  setText('flexExec', flex.execution_cn || 'T+1');
  setText('flexHold', String(flex.hold_days || 5));
  setText('flexStats', '');
  setText('flexStatsShort', pctLabel(full.win_rate));
  setText('flexDisclaimer', '');
  setText('flexMergeNote', '');
  setText('flexCompare', '');

  const risk = flex.risk_dashboard || {};
  const alloc = flex.allocation || {};
  setText('flexBeta', risk.estimated_beta != null ? String(risk.estimated_beta) : '—');
  setText('flexVol', '');
  setText('flexExposure', alloc.total_exposure != null ? pctLabel(alloc.total_exposure) : (risk.total_exposure != null ? pctLabel(risk.total_exposure) : '—'));
  setText('flexCorr', '');
  const wCore = alloc.w_core;
  const wSat = alloc.w_sat;
  setText(
    'flexAllocShort',
    wCore != null || wSat != null
      ? `${Math.round((wCore || 0) * 100)}/${Math.round((wSat || 0) * 100)}`
      : '—'
  );

  const core = flex.core || {};
  const sat = flex.satellite || {};
  const coreEl = document.getElementById('flexCoreSleeve');
  const satEl = document.getElementById('flexSatSleeve');
  if (coreEl) coreEl.dataset.tone = core.tone || (core.active ? 'buy' : 'wait');
  if (satEl) satEl.dataset.tone = sat.tone || (sat.active ? 'buy' : 'wait');
  setText('flexCoreAction', core.action || core.action_cn || '—');
  setText('flexCoreWeight', wCore != null ? pctLabel(wCore) : (core.etf_code || '—'));
  setText('flexCoreDetail', '');
  setText('flexCoreRule', '');
  setText('flexSatStage', sat.active ? (sat.action || sat.status_cn || 'ON') : 'OFF');
  setText('flexSatWeight', wSat != null ? pctLabel(wSat) : '—');
  setText('flexSatDetail', '');
  if (coreEl) coreEl.title = [core.action_cn, core.etf_code, core.detail].filter(Boolean).join(' · ');
  if (satEl) satEl.title = [sat.status_cn, sat.stage_cn, sat.detail].filter(Boolean).join(' · ');

  document.querySelectorAll('.flex-mode-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.flexMode === mode);
  });

  const listOpts = { interactive: true, signalAsOf: asOf };
  renderFlexOrderList(document.getElementById('flexMinimalList'), flex.minimal_actions || [], '—', listOpts);
  renderFlexOrderList(document.getElementById('flexBuyList'), flex.buy_list || [], '—', listOpts);
  renderFlexOrderList(document.getElementById('flexHoldList'), flex.hold_list || [], '—', listOpts);
  renderFlexOrderList(
    document.getElementById('flexSellList'),
    flex.close_list || flex.sell_list || [],
    '—',
    listOpts
  );
  renderFlexOrderList(document.getElementById('flexAvoidList'), flex.avoid_list || [], '—', {
    interactive: false,
  });
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
    updateRefreshStatus('ok', '核心数据已加载；正在加载策略与板块研究…');
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
    dashboardState.sectorChart?.resize();
    dashboardState.lowPositionChart?.resize();
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
