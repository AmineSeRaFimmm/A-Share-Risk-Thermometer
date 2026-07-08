function axisText() {
  return { color: '#667085', fontSize: 11 };
}

function isNarrow() {
  return window.innerWidth < 680;
}

function numericOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function paddedAxisRange(values, padding = 0.06) {
  const nums = values.map(numericOrNull).filter(Number.isFinite);
  if (!nums.length) return {};
  const low = Math.min(...nums);
  const high = Math.max(...nums);
  const span = Math.max(high - low, high * 0.02, 1);
  return {
    min: Math.floor(low - span * padding),
    max: Math.ceil(high + span * padding),
  };
}

function positiveOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function fmt(value, digits = 2) {
  if (Array.isArray(value)) {
    return fmt(value[value.length - 1], digits);
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '--';
}

function tooltipLine(marker, name, value) {
  return `${marker}${name}: <strong>${fmt(value)}</strong>`;
}

function sharedTooltip(params) {
  const items = Array.isArray(params) ? params : [params];
  const title = items[0]?.axisValueLabel || items[0]?.name || '';
  const lines = items
    .filter(item => item.value !== null && item.value !== undefined && item.value !== '-')
    .map(item => tooltipLine(item.marker, item.seriesName, item.value));
  return [title, ...lines].join('<br>');
}

function strategyMarks(strategy, history, valueKey, eventType) {
  if (!strategy || !history?.length) return [];
  const valueByDate = new Map(history.map(row => [row.date, numericOrNull(row[valueKey])]));
  const rows = eventType === 'buy' ? strategy.recent_buy || [] : strategy.recent_sell || [];
  const color = eventType === 'buy' ? '#15956b' : '#c2413b';
  return rows
    .filter(row => row.s3_s4_buy || row.s3_s4_sell)
    .map(row => {
      const value = valueByDate.get(row.trade_date);
      if (!Number.isFinite(value)) return null;
      return {
        name: eventType === 'buy' ? 'S3/S4 BUY' : 'S3/S4 SELL',
        coord: [row.trade_date, value],
        value: eventType === 'buy' ? 'BUY' : 'SELL',
        itemStyle: { color },
        label: { formatter: eventType === 'buy' ? 'B' : 'S' },
      };
    })
    .filter(Boolean);
}

function latestPoint(history, valueKey, label) {
  const last = [...history].reverse().find(row => Number.isFinite(Number(row[valueKey])));
  if (!last) return [];
  return [{
    name: label,
    coord: [last.date, Number(last[valueKey])],
    value: label,
    itemStyle: { color: '#111827' },
    label: { formatter: label },
  }];
}

function recentHighPoint(history) {
  if (!history?.length) return [];
  const rows = history.filter(row => Number.isFinite(Number(row.risk_temperature)));
  if (!rows.length) return [];
  const high = rows.reduce((best, row) => Number(row.risk_temperature) > Number(best.risk_temperature) ? row : best, rows[0]);
  return [{
    name: '最近高点',
    coord: [high.date, Number(high.risk_temperature)],
    value: fmt(high.risk_temperature, 1),
    itemStyle: { color: '#8f1d22' },
    label: { formatter: '高点' },
  }];
}

function renderComponentsChart(payload) {
  const el = document.getElementById('componentsChart');
  const chart = echarts.init(el);
  const items = payload.components || [];
  chart.setOption({
    tooltip: {
      confine: true,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const item = Array.isArray(params) ? params[0] : params;
        const source = items.slice().reverse()[item.dataIndex] || {};
        return `${source.name}<br>贡献: <strong>${fmt(source.contribution)}</strong><br>分数: ${fmt(source.score)} / 权重: ${fmt(Number(source.weight) * 100)}%`;
      },
    },
    grid: { left: isNarrow() ? 86 : 118, right: isNarrow() ? 8 : 24, top: 8, bottom: 34 },
    xAxis: { type: 'value', max: 30, axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } },
    yAxis: { type: 'category', data: items.map(d => d.name).reverse(), axisLabel: axisText(), axisTick: { show: false } },
    series: [{
      type: 'bar',
      data: items.map(d => Number(d.contribution || 0).toFixed(2)).reverse(),
      itemStyle: { color: '#c2413b', borderRadius: [0, 4, 4, 0] },
      label: { show: !isNarrow(), position: 'right', color: '#344054', formatter: '{c}' }
    }]
  });
  return chart;
}

function renderHistoryChart(history, strategy) {
  const chart = echarts.init(document.getElementById('historyChart'));
  chart.setOption({
    tooltip: { confine: true, trigger: 'axis', axisPointer: { type: 'cross' }, formatter: sharedTooltip },
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: 22, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText(), boundaryGap: false },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } },
    visualMap: { show: false, pieces: [
      { gt: 0, lte: 60, color: '#2563eb' },
      { gt: 60, lte: 75, color: '#c05621' },
      { gt: 75, lte: 90, color: '#c2413b' },
      { gt: 90, lte: 100, color: '#8f1d22' }
    ]},
    series: [{
      name: 'Risk Temperature',
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 3 },
      areaStyle: { opacity: 0.08 },
      markArea: { silent: true, itemStyle: { opacity: 0.08 }, data: [[{ yAxis: 60 }, { yAxis: 75 }], [{ yAxis: 75 }, { yAxis: 90 }], [{ yAxis: 90 }, { yAxis: 100 }]] },
      markLine: { silent: true, symbol: 'none', lineStyle: { color: '#98a2b3', type: 'dashed' }, data: [{ yAxis: 60 }, { yAxis: 75 }, { yAxis: 90 }] },
      markPoint: { symbolSize: 42, data: [...latestPoint(history, 'risk_temperature', '当前'), ...recentHighPoint(history)] },
      data: history.map(d => d.risk_temperature)
    }, {
      name: 'S3/S4 BUY',
      type: 'scatter',
      symbol: 'triangle',
      symbolSize: 12,
      itemStyle: { color: '#15956b' },
      data: strategyMarks(strategy, history, 'risk_temperature', 'buy').map(mark => mark.coord),
    }, {
      name: 'S3/S4 SELL',
      type: 'scatter',
      symbol: 'diamond',
      symbolSize: 11,
      itemStyle: { color: '#c2413b' },
      data: strategyMarks(strategy, history, 'risk_temperature', 'sell').map(mark => mark.coord),
    }]
  });
  return chart;
}

function renderAvixQvixChart(history, strategy) {
  const chart = echarts.init(document.getElementById('avixQvixChart'));
  chart.setOption({
    tooltip: { confine: true, trigger: 'axis', axisPointer: { type: 'cross' }, formatter: sharedTooltip },
    legend: { top: 0, textStyle: axisText() },
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: 36, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText(), boundaryGap: false },
    yAxis: { type: 'value', axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } },
    series: [
      {
        name: 'AVIX_CLEAN_CLOSE',
        type: 'line',
        symbol: 'none',
        smooth: true,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.avix_clean)),
        lineStyle: { color: '#c2413b', width: 2 },
        markPoint: { symbolSize: 42, data: [...strategyMarks(strategy, history, 'avix_clean', 'buy'), ...strategyMarks(strategy, history, 'avix_clean', 'sell')] },
      },
      {
        name: 'QVIX_300INDEX',
        type: 'line',
        symbol: 'none',
        smooth: true,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.qvix)),
        lineStyle: { color: '#2563eb', width: 2 }
      },
      {
        name: 'QVIX_REPLICA_MODEL',
        type: 'line',
        symbol: 'none',
        smooth: true,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.qvix_replica)),
        lineStyle: { color: '#15956b', width: 2, type: 'dashed' }
      }
    ]
  });
  return chart;
}

function renderHs300Chart(history) {
  const chart = echarts.init(document.getElementById('hs300Chart'));
  const hs300Values = history.map(d => numericOrNull(d.hs300_close));
  const hs300Axis = paddedAxisRange(hs300Values);
  chart.setOption({
    tooltip: { confine: true, trigger: 'axis', axisPointer: { type: 'cross' }, formatter: sharedTooltip },
    legend: { top: 0, textStyle: axisText() },
    grid: { left: isNarrow() ? 42 : 54, right: isNarrow() ? 42 : 56, top: 36, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText(), boundaryGap: false },
    yAxis: [
      { type: 'value', name: '沪深300', min: hs300Axis.min, max: hs300Axis.max, scale: true, axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } },
      { type: 'value', name: '温度', min: 0, max: 100, axisLabel: axisText(), splitLine: { show: false } }
    ],
    series: [
      { name: 'HS300 Close', type: 'line', symbol: 'none', smooth: true, connectNulls: false, data: hs300Values, lineStyle: { color: '#111827', width: 2.4 } },
      {
        name: 'Risk Temperature',
        type: 'line',
        yAxisIndex: 1,
        symbol: 'none',
        smooth: true,
        connectNulls: false,
        data: history.map(d => numericOrNull(d.risk_temperature)),
        lineStyle: { color: '#c2413b', width: 2 },
        markArea: { silent: true, itemStyle: { opacity: 0.08 }, data: [[{ yAxis: 60 }, { yAxis: 75 }], [{ yAxis: 75 }, { yAxis: 90 }], [{ yAxis: 90 }, { yAxis: 100 }]] },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#98a2b3', type: 'dashed' }, data: [{ yAxis: 60 }, { yAxis: 75 }, { yAxis: 90 }] },
      }
    ]
  });
  return chart;
}
