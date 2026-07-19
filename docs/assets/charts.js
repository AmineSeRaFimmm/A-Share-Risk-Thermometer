function axisText() {
  return { color: '#6e655a', fontSize: 11, fontFamily: 'Source Sans 3, Noto Sans SC, sans-serif' };
}

const SERIES_LABELS = {
  avixClean: 'AVIX收盘复刻',
  qvixReal: '真实QVIX',
  qvixReplica: 'QVIX模型复刻',
  hs300: '沪深300收盘',
  riskTemperature: '风险温度',
};

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

function setChartA11y(chart, label, summary) {
  const dom = chart.getDom();
  dom.setAttribute('role', 'img');
  dom.setAttribute('aria-label', `${label}。${summary}`);
}

function legendOption() {
  return {
    top: 0,
    type: isNarrow() ? 'scroll' : 'plain',
    itemWidth: isNarrow() ? 12 : 18,
    itemHeight: isNarrow() ? 8 : 10,
    itemGap: isNarrow() ? 6 : 10,
    textStyle: axisText(),
    pageIconSize: 9,
    pageTextStyle: axisText(),
  };
}

function qvixMissingAreas(history) {
  const areas = [];
  let start = null;
  history.forEach((row, index) => {
    const missing = positiveOrNull(row.qvix) === null;
    if (missing && start === null) start = row.date;
    const isLast = index === history.length - 1;
    if ((!missing || isLast) && start !== null) {
      const end = missing && isLast ? row.date : history[index - 1]?.date;
      if (end) {
        areas.push([
          { name: '真实QVIX缺失', xAxis: start },
          { xAxis: end },
        ]);
      }
      start = null;
    }
  });
  return areas;
}

function latestFinite(history, key) {
  const row = [...history].reverse().find(item => Number.isFinite(Number(item[key])));
  return row ? { date: row.date, value: Number(row[key]) } : null;
}

function strategyMarks(strategy, history, valueKey, eventType) {
  if (!strategy || !history?.length) return [];
  const valueByDate = new Map(history.map(row => [row.date, numericOrNull(row[valueKey])]));
  const rows = eventType === 'buy' ? strategy.recent_buy || [] : strategy.recent_sell || [];
  const color = eventType === 'buy' ? '#1e5c42' : '#8f2a2a';
  return rows
    .filter(row => row.s3_s4_buy || row.s3_s4_sell)
    .map(row => {
      const value = valueByDate.get(row.trade_date);
      if (!Number.isFinite(value)) return null;
      return {
        name: eventType === 'buy' ? 'S3/S4买入' : 'S3/S4卖出',
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
    itemStyle: { color: '#1a1714' },
    label: { formatter: label },
  }];
}

function latestEstimatedPoint(history) {
  const last = [...history].reverse().find(row => Number.isFinite(Number(row.risk_temperature_estimated)));
  if (!last) return [];
  return [{
    name: '估算',
    coord: [last.date, Number(last.risk_temperature_estimated)],
    value: '估算',
    itemStyle: { color: '#9a4f24' },
    label: { formatter: '估' },
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
    itemStyle: { color: '#5c1414' },
    label: { formatter: '高点' },
  }];
}

function renderComponentsChart(payload) {
  const el = document.getElementById('componentsChart');
  const chart = echarts.init(el);
  const items = payload.components || [];
  const sorted = [...items].sort((a, b) => Number(b.contribution || 0) - Number(a.contribution || 0));
  const topNames = new Set(sorted.slice(0, 3).map(item => item.name));
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: '组件贡献条形图，显示当前风险温度由八个因子按权重贡献组成。' },
    },
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
    xAxis: { type: 'value', max: 30, axisLabel: axisText(), splitLine: { lineStyle: { color: '#ebe4d7' } } },
    yAxis: { type: 'category', data: items.map(d => d.name).reverse(), axisLabel: axisText(), axisTick: { show: false } },
    series: [{
      type: 'bar',
      data: items.map(d => Number(d.contribution || 0).toFixed(2)).reverse(),
      itemStyle: {
        color: params => topNames.has(items.slice().reverse()[params.dataIndex]?.name) ? '#8f2a2a' : '#c98986',
        borderRadius: [0, 4, 4, 0],
      },
      label: { show: !isNarrow(), position: 'right', color: '#3a342e', formatter: '{c}' }
    }]
  });
  setChartA11y(chart, '组件贡献', `最大贡献因子是${sorted[0]?.name || '未知'}，贡献${fmt(sorted[0]?.contribution || 0)}。`);
  return chart;
}

function renderHistoryChart(history, strategy) {
  const chart = echarts.init(document.getElementById('historyChart'));
  const estimatedData = history.map(d => numericOrNull(d.risk_temperature_estimated));
  const hasEstimated = estimatedData.some(v => v !== null);
  const series = [{
    name: '温度',
    type: 'line',
    smooth: false,
    symbol: 'none',
    lineStyle: { width: 2.4 },
    itemStyle: { color: '#2a4058' },
    areaStyle: { opacity: 0.06 },
    markArea: {
      silent: true,
      itemStyle: { opacity: 0.06 },
      data: [[{ yAxis: 60 }, { yAxis: 75 }], [{ yAxis: 75 }, { yAxis: 90 }], [{ yAxis: 90 }, { yAxis: 100 }]],
    },
    markLine: {
      silent: true,
      symbol: 'none',
      lineStyle: { color: '#9a9084', type: 'dashed', width: 1 },
      data: [{ yAxis: 60 }, { yAxis: 75 }, { yAxis: 90 }],
    },
    // No text markPoints (当前/高点/估) — keep magazine-clean
    data: history.map(d => numericOrNull(d.risk_temperature)),
  }];
  // Only mount estimate series when there is real data (avoid empty legend ghost)
  if (hasEstimated) {
    series.push({
      name: '估算',
      type: 'line',
      smooth: false,
      symbol: 'none',
      connectNulls: false,
      lineStyle: { color: '#9a4f24', width: 1.8, type: 'dashed' },
      itemStyle: { color: '#9a4f24' },
      data: estimatedData,
    });
  }
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: '风险温度历史曲线。' },
    },
    tooltip: {
      confine: true,
      trigger: 'axis',
      axisPointer: { type: 'line' },
      formatter: params => {
        const items = Array.isArray(params) ? params : [params];
        const title = items[0]?.axisValueLabel || items[0]?.name || '';
        const lines = items
          .filter(item => item.value !== null && item.value !== undefined && item.value !== '-')
          .map(item => tooltipLine(item.marker, item.seriesName, item.value));
        return [title, ...lines].join('<br>');
      },
    },
    legend: hasEstimated ? legendOption() : { show: false },
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: isNarrow() ? 28 : 24, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText(), boundaryGap: false },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: axisText(), splitLine: { lineStyle: { color: '#ebe4d7' } } },
    visualMap: {
      show: false,
      seriesIndex: 0,
      pieces: [
        { gt: 0, lte: 60, color: '#2a4058' },
        { gt: 60, lte: 75, color: '#9a4f24' },
        { gt: 75, lte: 90, color: '#8f2a2a' },
        { gt: 90, lte: 100, color: '#5c1414' },
      ],
    },
    series,
  });
  const latest = latestFinite(history, 'risk_temperature');
  setChartA11y(
    chart,
    '温度历史',
    latest ? `最新温度 ${fmt(latest.value, 1)}（${latest.date}）` : '风险温度历史曲线'
  );
  return chart;
}

function renderAvixQvixChart(history, strategy) {
  const chart = echarts.init(document.getElementById('avixQvixChart'));
  const missingAreas = qvixMissingAreas(history);
  const realCount = history.filter(d => positiveOrNull(d.qvix) !== null).length;
  // No S3/S4 buy/sell marks here — keep pure vol comparison
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: 'AVIX 与 QVIX 对比。真实 QVIX 缺失保留断点，灰区为缺失，虚线为模型复刻。' },
    },
    tooltip: { confine: true, trigger: 'axis', axisPointer: { type: 'line' }, formatter: sharedTooltip },
    legend: legendOption(),
    grid: { left: isNarrow() ? 36 : 46, right: isNarrow() ? 12 : 24, top: isNarrow() ? 44 : 36, bottom: 34 },
    xAxis: { type: 'category', data: history.map(d => d.date), axisLabel: axisText(), boundaryGap: false },
    yAxis: { type: 'value', axisLabel: axisText(), splitLine: { lineStyle: { color: '#ebe4d7' } } },
    series: [
      {
        name: SERIES_LABELS.avixClean,
        type: 'line',
        symbol: 'none',
        smooth: false,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.avix_clean)),
        lineStyle: { color: '#8f2a2a', width: 2 },
        itemStyle: { color: '#8f2a2a' },
      },
      {
        name: SERIES_LABELS.qvixReal,
        type: 'line',
        symbol: 'none',
        smooth: false,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.qvix)),
        lineStyle: { color: '#2a4058', width: 2.2 },
        itemStyle: { color: '#2a4058' },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(102, 112, 133, 0.08)' },
          label: { show: false },
          data: missingAreas,
        },
      },
      {
        name: SERIES_LABELS.qvixReplica,
        type: 'line',
        symbol: 'none',
        smooth: false,
        connectNulls: false,
        data: history.map(d => positiveOrNull(d.qvix_replica)),
        lineStyle: { color: '#1e5c42', width: 1.8, type: 'dashed' },
        itemStyle: { color: '#1e5c42' },
      },
    ],
  });
  setChartA11y(
    chart,
    'AVIX与QVIX',
    `共${history.length}日，真实QVIX ${realCount} 点，缺失 ${history.length - realCount} 点`
  );
  return chart;
}

function renderHs300Chart(history) {
  const chart = echarts.init(document.getElementById('hs300Chart'));
  const hs300Values = history.map(d => numericOrNull(d.hs300_close));
  const hs300Axis = paddedAxisRange(hs300Values);
  const dates = history.map(d => d.date);
  const narrow = isNarrow();
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: '沪深300与风险温度上下同步时间图，上方是沪深300收盘价，下方是风险温度，避免双轴数值误读。' },
    },
    tooltip: { confine: true, trigger: 'axis', axisPointer: { type: 'line' }, formatter: sharedTooltip },
    legend: legendOption(),
    grid: [
      { left: narrow ? 42 : 54, right: narrow ? 12 : 24, top: narrow ? 44 : 36, height: narrow ? 82 : 96 },
      { left: narrow ? 42 : 54, right: narrow ? 12 : 24, top: narrow ? 160 : 168, bottom: 34 },
    ],
    xAxis: [
      { type: 'category', gridIndex: 0, data: dates, axisLabel: { show: false }, axisTick: { show: false }, boundaryGap: false },
      { type: 'category', gridIndex: 1, data: dates, axisLabel: axisText(), boundaryGap: false },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, name: '沪深300', min: hs300Axis.min, max: hs300Axis.max, scale: true, axisLabel: axisText(), splitLine: { lineStyle: { color: '#ebe4d7' } } },
      { type: 'value', gridIndex: 1, name: '温度', min: 0, max: 100, axisLabel: axisText(), splitLine: { lineStyle: { color: '#ebe4d7' } } },
    ],
    series: [
      { name: SERIES_LABELS.hs300, type: 'line', xAxisIndex: 0, yAxisIndex: 0, symbol: 'none', smooth: false, connectNulls: false, data: hs300Values, lineStyle: { color: '#1a1714', width: 2.2 }, itemStyle: { color: '#1a1714' } },
      {
        name: SERIES_LABELS.riskTemperature,
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        symbol: 'none',
        smooth: false,
        connectNulls: false,
        data: history.map(d => numericOrNull(d.risk_temperature)),
        lineStyle: { color: '#8f2a2a', width: 2 },
        itemStyle: { color: '#8f2a2a' },
        markArea: { silent: true, itemStyle: { opacity: 0.08 }, data: [[{ yAxis: 60 }, { yAxis: 75 }], [{ yAxis: 75 }, { yAxis: 90 }], [{ yAxis: 90 }, { yAxis: 100 }]] },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9a9084', type: 'dashed' }, data: [{ yAxis: 60 }, { yAxis: 75 }, { yAxis: 90 }] },
      }
    ]
  });
  const latestHs300 = latestFinite(history, 'hs300_close');
  const latestRisk = latestFinite(history, 'risk_temperature');
  setChartA11y(chart, '沪深300与风险温度', latestHs300 && latestRisk ? `最新沪深300为${fmt(latestHs300.value, 1)}，最新风险温度为${fmt(latestRisk.value, 1)}。两条线分上下两格显示，不共用数值轴。` : '显示沪深300和风险温度的同步时间变化。');
  return chart;
}

function renderSectorCorrelationChart(payload) {
  const el = document.getElementById('sectorCorrelationChart');
  if (!el || !payload?.rankings) return null;
  const chart = echarts.init(el);
  const positive = payload.rankings.positive || [];
  const negative = payload.rankings.negative || [];
  const rows = [
    ...negative.slice(0, 8).reverse(),
    ...positive.slice(0, 8),
  ];
  const maxAbs = Math.max(0.2, ...rows.map(row => Math.abs(Number(row.corr_temp_fwd_excess) || 0)));
  const axisMax = Math.ceil(maxAbs * 10) / 10;
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: '风险温度与申万一级行业未来5日超额收益相关性排行。正值代表高温环境相对更强，负值代表高温环境相对更弱。' },
    },
    tooltip: {
      confine: true,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const item = Array.isArray(params) ? params[0] : params;
        const source = rows[item.dataIndex] || {};
        return [
          `<strong>${source.name}</strong>`,
          `1Y相关: ${fmt(source.corr_temp_fwd_excess, 3)}`,
          `2Y相关: ${fmt(source.corr_2y, 3)}`,
          `高风险区平均超额: ${fmt(Number(source.high_risk_avg_excess) * 100, 2)}% / 样本 ${source.high_risk_sample ?? '--'}`,
          `稳定性: ${source.stability || '--'}`,
          `样本: ${source.sample_size || '--'}`,
        ].join('<br>');
      },
    },
    grid: { left: isNarrow() ? 82 : 112, right: isNarrow() ? 16 : 28, top: 16, bottom: 36 },
    xAxis: {
      type: 'value',
      min: -axisMax,
      max: axisMax,
      axisLabel: axisText(),
      splitLine: { lineStyle: { color: '#ebe4d7' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map(row => row.name),
      axisLabel: axisText(),
      axisTick: { show: false },
    },
    series: [{
      name: '1Y 5日超额相关',
      type: 'bar',
      data: rows.map(row => row.corr_temp_fwd_excess),
      itemStyle: {
        color: params => Number(params.value) >= 0 ? '#1e5c42' : '#8f2a2a',
        borderRadius: params => Number(params.value) >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
      },
      label: {
        show: !isNarrow(),
        position: params => Number(params.value) >= 0 ? 'right' : 'left',
        color: '#3a342e',
        formatter: params => fmt(params.value, 2),
      },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#9a9084' },
        data: [{ xAxis: 0 }],
      },
    }]
  });
  setChartA11y(chart, '风险温度与板块关系', `覆盖${payload.sector_count || 0}个申万一级行业，日期截至${payload.as_of || '--'}。`);
  return chart;
}

function renderLowPositionSectorChart(payload) {
  const el = document.getElementById('lowPositionChart');
  if (!el || !payload?.selected_sectors?.length) return null;
  const chart = echarts.init(el);
  const metricByKey = new Map((payload.metrics || []).map(row => [
    `${row.symbol}-${row.window}-${row.horizon}`,
    row,
  ]));
  const rows = payload.selected_sectors.slice().reverse();
  const oneYear = rows.map(row => metricByKey.get(`${row.symbol}-1Y-20D`)?.corr_temp_fwd_excess ?? null);
  const twoYear = rows.map(row => metricByKey.get(`${row.symbol}-2Y-20D`)?.corr_temp_fwd_excess ?? null);
  const maxAbs = Math.max(0.2, ...oneYear.concat(twoYear).map(value => Math.abs(Number(value) || 0)));
  const axisMax = Math.ceil(maxAbs * 10) / 10;
  chart.setOption({
    aria: {
      enabled: true,
      label: { description: '低位板块与风险温度关系图，比较近一年和近两年风险温度对未来20日板块超额收益的相关性。' },
    },
    tooltip: {
      confine: true,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const items = Array.isArray(params) ? params : [params];
        const source = rows[items[0]?.dataIndex] || {};
        const lines = [
          `<strong>${source.name}</strong>`,
          `低位分: ${fmt(source.low_position_score, 1)}`,
          `5Y分位: ${fmt(Number(source.price_percentile_5y) * 100, 1)}%`,
          `5Y回撤: ${fmt(Number(source.drawdown_5y) * 100, 1)}%`,
        ];
        items.forEach(item => {
          lines.push(`${item.marker}${item.seriesName}: ${fmt(item.value, 3)}`);
        });
        return lines.join('<br>');
      },
    },
    legend: legendOption(),
    grid: { left: isNarrow() ? 82 : 112, right: isNarrow() ? 16 : 28, top: isNarrow() ? 52 : 34, bottom: 36 },
    xAxis: {
      type: 'value',
      min: -axisMax,
      max: axisMax,
      axisLabel: axisText(),
      splitLine: { lineStyle: { color: '#ebe4d7' } },
    },
    yAxis: {
      type: 'category',
      data: rows.map(row => row.name),
      axisLabel: axisText(),
      axisTick: { show: false },
    },
    series: [
      {
        name: '1Y 20日超额相关',
        type: 'bar',
        data: oneYear,
        itemStyle: { color: params => Number(params.value) >= 0 ? '#1e5c42' : '#8f2a2a', borderRadius: 0 },
        label: { show: !isNarrow(), position: 'right', color: '#3a342e', formatter: params => fmt(params.value, 2) },
      },
      {
        name: '2Y 20日超额相关',
        type: 'bar',
        data: twoYear,
        itemStyle: { color: params => Number(params.value) >= 0 ? '#6fa88a' : '#c98986', borderRadius: 0 },
        label: { show: false },
      },
    ],
  });
  setChartA11y(chart, '低位板块与风险温度', `筛选${payload.selected_count || rows.length}个低位板块，日期截至${payload.as_of || '--'}。`);
  return chart;
}
