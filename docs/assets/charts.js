function axisText() {
  return { color: '#667085', fontSize: 11 };
}

function isNarrow() {
  return window.innerWidth < 680;
}

function renderComponentsChart(payload) {
  const el = document.getElementById('componentsChart');
  const chart = echarts.init(el);
  const items = payload.components || [];
  chart.setOption({
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

function renderHistoryChart(history) {
  const chart = echarts.init(document.getElementById('historyChart'));
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: 22, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText() },
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
      data: history.map(d => d.risk_temperature)
    }]
  });
  return chart;
}

function renderAvixQvixChart(history) {
  const chart = echarts.init(document.getElementById('avixQvixChart'));
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: axisText() },
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: 36, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText() },
    yAxis: { type: 'value', axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } },
    series: [
      { name: 'AVIX_CLEAN_CLOSE', type: 'line', symbol: 'none', smooth: true, data: history.map(d => d.avix_clean), lineStyle: { color: '#c2413b', width: 2 } },
      { name: 'QVIX_300INDEX', type: 'line', symbol: 'none', smooth: true, data: history.map(d => d.qvix), lineStyle: { color: '#2563eb', width: 2 } }
    ]
  });
  return chart;
}

function renderHs300Chart(history) {
  const chart = echarts.init(document.getElementById('hs300Chart'));
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: axisText() },
    grid: [{ left: isNarrow() ? 38 : 48, right: isNarrow() ? 12 : 48, top: 36, height: '38%' }, { left: isNarrow() ? 38 : 48, right: isNarrow() ? 12 : 48, bottom: 34, height: '32%' }],
    xAxis: [{ type: 'category', data: history.map(d => d.date), axisLabel: { show: false } }, { type: 'category', gridIndex: 1, data: history.map(d => d.date), axisLabel: axisText() }],
    yAxis: [{ type: 'value', axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } }, { type: 'value', gridIndex: 1, min: 0, max: 100, axisLabel: axisText(), splitLine: { lineStyle: { color: '#edf0f5' } } }],
    series: [
      { name: 'HS300 Close', type: 'line', symbol: 'none', data: history.map(d => d.hs300_close), lineStyle: { color: '#111827', width: 2 } },
      { name: 'Risk Temperature', type: 'line', xAxisIndex: 1, yAxisIndex: 1, symbol: 'none', data: history.map(d => d.risk_temperature), lineStyle: { color: '#c2413b', width: 2 } }
    ]
  });
  return chart;
}
