async function loadJSON(path) {
  const res = await fetch(path + '?v=' + Date.now());
  if (!res.ok) throw new Error('Failed to load ' + path);
  return await res.json();
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
  appendMetaItem('实时AVIX', 'realtimeAvix');
  appendMetaItem('实时质量', 'realtimeAvixQuality');
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

function renderLatest(latest) {
  setText('riskTemperature', latest.risk_temperature);
  setText('regime', latest.regime_cn);
  const modeLabel = latest.temperature_mode_cn || (latest.is_final === false ? '盘中估算' : '收盘正式');
  const qualityEl = document.getElementById('quality');
  qualityEl.textContent = modeLabel;
  qualityEl.title = latest.quality || modeLabel;
  setText('temperatureMode', modeLabel);
  setText('tradeDate', latest.trade_date);
  const update = latest.update_time ? new Date(latest.update_time).toLocaleString('zh-CN', { hour12: false }) : '--';
  setText('updateTime', update);
  renderRealtimeAvix(latest.avix || {});
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
}

async function main() {
  const [latest, history, components, audit] = await Promise.all([
    loadJSON('./data/latest.json'),
    loadJSON('./data/history.json'),
    loadJSON('./data/components.json'),
    loadJSON('./data/audit.json')
  ]);
  renderLatest(latest);
  renderAudit(audit);
  const charts = [
    renderComponentsChart(components),
    renderHistoryChart(history),
    renderAvixQvixChart(history),
    renderHs300Chart(history)
  ];
  window.addEventListener('resize', () => charts.forEach(chart => chart.resize()));
}

main().catch(err => {
  document.body.classList.add('error');
  console.error(err);
});

const AUTO_REFRESH_MS = 30 * 60 * 1000;

setInterval(() => {
  if (!document.hidden) {
    window.location.reload();
  }
}, AUTO_REFRESH_MS);
