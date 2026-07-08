async function loadJSON(path) {
  const res = await fetch(path + '?v=' + Date.now());
  if (!res.ok) throw new Error('Failed to load ' + path);
  return await res.json();
}

const AUTO_REFRESH_MS = 60 * 1000;
const STALE_THRESHOLD_MS = 15 * 60 * 1000;
const dashboardState = {
  activeRange: '1Y',
  componentChart: null,
  sectorChart: null,
  timeCharts: [],
  history: [],
  strategy: {},
  refreshInFlight: false,
};

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
  const realtimeUsable = avix?.avix_realtime_usable;
  setText('realtimeAvix', formatRealtimeAvix(realtimeMid));
  setText('realtimeAvixQuality', realtimeQuality ? `${realtimeQuality}${realtimeUsable === false ? ' / NOT USABLE' : ''}` : '--');
  const qualityEl = document.getElementById('realtimeAvixQuality');
  if (qualityEl) qualityEl.title = [avix?.avix_realtime_note, avix?.avix_realtime_source].filter(Boolean).join(' | ');
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
  if (latest?.is_final === false) {
    note.textContent = '盘中估算仅实时替换 AVIX 相关因子；指数、宽度、回撤、成交等非 AVIX 因子沿用最近正式收盘。';
    return;
  }
  note.textContent = '当前为收盘正式口径；盘中更新可用时会切换为实时 AVIX 驱动的盘中估算。';
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
    breadth: '全A宽度',
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

function appendNowcastHistory(history, latest) {
  const rows = [...(history || [])];
  const lastDate = rows.length ? rows[rows.length - 1].date : null;
  if (!latest?.trade_date || latest.is_final !== false || latest.trade_date <= lastDate) {
    return rows;
  }
  rows.push({
    date: latest.trade_date,
    risk_temperature: latest.risk_temperature,
    regime: latest.regime,
    avix_clean: null,
    qvix: null,
    hs300_close: null,
    drawdown_pressure: null,
    breadth_pressure: null,
    is_nowcast: true,
  });
  return rows;
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

async function loadDashboardData() {
  const [latest, history, components, audit, strategy, sector] = await Promise.all([
    loadJSON('./data/latest.json'),
    loadJSON('./data/history.json'),
    loadJSON('./data/components.json'),
    loadJSON('./data/audit.json'),
    loadJSON('./data/strategy.json').catch(() => ({ status: 'missing' })),
    loadJSON('./data/sector_correlation.json').catch(() => ({ status: 'missing' }))
  ]);
  return { latest, history, components, audit, strategy, sector };
}

function renderDashboard({ latest, history, components, audit, strategy, sector }) {
  renderLatest(latest);
  renderAudit(audit);
  renderStrategy(strategy);
  renderSectorCorrelation(sector);
  setText('componentsMode', `${components.temperature_mode || '--'} / ${components.trade_date || '--'}`);
  const componentDom = document.getElementById('componentsChart');
  const oldComponentChart = echarts.getInstanceByDom(componentDom);
  if (oldComponentChart) oldComponentChart.dispose();
  dashboardState.componentChart = renderComponentsChart(components);
  const activeHistory = appendNowcastHistory(history, latest);
  dashboardState.history = activeHistory;
  dashboardState.strategy = strategy;
  dashboardState.timeCharts = renderTimeCharts(activeHistory, strategy, dashboardState.activeRange);
}

async function refreshDashboard() {
  if (dashboardState.refreshInFlight) return;
  dashboardState.refreshInFlight = true;
  try {
    renderDashboard(await loadDashboardData());
    updateRefreshStatus('ok');
  } catch (err) {
    console.error(err);
    updateRefreshStatus('error', err.message || String(err));
  } finally {
    dashboardState.refreshInFlight = false;
  }
}

async function main() {
  renderDashboard(await loadDashboardData());
  updateRefreshStatus('ok', '初始数据加载完成；页面每 60 秒自动检查最新数据');
  bindRangeControls(range => {
    dashboardState.activeRange = range;
    dashboardState.timeCharts = renderTimeCharts(dashboardState.history, dashboardState.strategy, range);
  });
  window.addEventListener('resize', () => {
    dashboardState.componentChart?.resize();
    dashboardState.sectorChart?.resize();
    dashboardState.timeCharts.forEach(chart => chart.resize());
  });
}

main().catch(err => {
  document.body.classList.add('error');
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
