const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.waitForFunction(() => dashboardState.flexActive != null);
  await page.evaluate(() => {
    clearInterval(dashboardState.flexRealtimeQuoteTimer);
    dashboardState.flexBook = 'sim';
    dashboardState.dailyFlexBrief = null;
    const days = ['2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-10', '2026-08-11'];
    dashboardState.flexTradeCalendar = { dates: days };
    const bars = Object.fromEntries(days.map(day => [day, {
      open: day === '2026-08-06' ? 1.05 : 1.2, close: 1.2,
    }]));
    dashboardState.etfMarks = { as_of: days.at(-1), complete_as_of: days.at(-1),
      by_code: { '515880': { bars }, '510300': { bars: structuredClone(bars) } } };
    const position = {
      key: 'old-key', name: '通信', etf_code: '515880', sleeve: 'satellite',
      signal_as_of: '2026-08-03', buy_date: '2026-08-04', qty: 1000,
      cost_basis: 1000.1, avg_price: 1.0001, last_price: 1, target_weight: 1,
    };
    const model = { status: 'open', entry_signal_date: position.signal_as_of,
      entry_date: position.buy_date, names: ['通信'], weights: { 通信: 1 } };
    const event = { id: 'sat:cycle1:exit', event_id: 'sat:cycle1:exit', sleeve: 'satellite',
      event_type: 'TAKE_PROFIT', close_code: 'LOCAL_TAKE_PROFIT',
      trigger_date: '2026-08-05', execution_date: '2026-08-06', execution_status: 'EXECUTED',
      position: structuredClone(model), members: [{ name: '通信', etf_code: '515880' }] };
    const flex = { as_of: '2026-08-07', mode: 'aggressive', allocation: { w_core: 0, w_sat: 0 },
      core: {}, satellite: {}, hold_list: [{ name: '通信', etf_code: '515880' }],
      position_state: { core: { status: 'flat' }, satellite: { status: 'flat' } },
      execution_events: [event] };
    const journal = Array.from({ length: 240 }, (_, i) => ({
      id: `old-${i}`, type: 'OPEN', name: '历史', price: 1, qty: 100,
      amount: 100.01, fee: 0.01, trade_date: '2026-08-04',
    }));
    const ledger = { version: 6, book: 'sim', capital: 10000, cash: 8999.9,
      positions: { 'old-key': position }, journal, strategy_as_of: '2026-08-05',
      risk_exits: {}, pending_orders: {} };
    const real = JSON.stringify({ version: 5, book: 'real', capital: 30000,
      cash: 28999.9, positions: { manual: position }, journal: [{ id: 'actual-fill' }] });
    localStorage.setItem('ashare_flex_exec_ledger_v1', real);
    localStorage.setItem('ashare_flex_exec_ledger_sim_v1', JSON.stringify(ledger));
    globalThis.fx = { flex, event, ledger, model, real, journal };
  });
});

test('persistent exits fill exact execution open once and preserve all historical fills', async ({ page }) => {
  const result = await page.evaluate(() => {
    const first = rebuildSimLedgerFromStrategy(fx.flex);
    dashboardState.etfMarks.by_code['515880'].bars['2026-08-06'].open = 9;
    const second = rebuildSimLedgerFromStrategy(fx.flex);
    return { first, second, journal: fx.journal,
      label: flexSimSleeveLabel(fx.flex, second, 'satellite'),
      realUnchanged: localStorage.getItem('ashare_flex_exec_ledger_v1') === fx.real };
  });
  expect(result.first.positions).toEqual({});
  expect(result.first.cash).toBeCloseTo(10049.795, 6);
  expect(result.first.journal[0]).toMatchObject({ price: 1.05, trade_date: '2026-08-06', event_id: 'sat:cycle1:exit' });
  expect(result.first.journal.slice(1)).toEqual(result.journal);
  expect(result.second.journal).toEqual(result.first.journal);
  expect(result.second.cash).toEqual(result.first.cash);
  expect(result.label).toBe('模拟已平仓');
  expect(result.realUnchanged).toBe(true);
  await page.reload();
  const persisted = await page.evaluate(() => loadRawFlexLedgerForBook('sim'));
  expect(persisted.journal).toEqual(result.second.journal);
});

test('missing execution open stays pending across snapshots until exact price arrives', async ({ page }) => {
  const result = await page.evaluate(() => {
    delete dashboardState.etfMarks.by_code['515880'].bars['2026-08-06'];
    const missing = rebuildSimLedgerFromStrategy(fx.flex);
    const label = flexSimSleeveLabel(fx.flex, missing, 'satellite');
    fx.flex.execution_events = [];
    const stillMissing = rebuildSimLedgerFromStrategy(fx.flex);
    dashboardState.etfMarks.by_code['515880'].bars['2026-08-06'] = { open: 1.05, close: 1.05 };
    const filled = rebuildSimLedgerFromStrategy(fx.flex);
    return { missing, stillMissing, filled, label };
  });
  expect(result.missing.journal).toHaveLength(240);
  expect(result.stillMissing.journal).toHaveLength(240);
  expect(result.missing.cash).toBe(8999.9);
  expect(Object.values(result.missing.positions)[0].pending_close_date).toBe('2026-08-06');
  expect(result.label).toBe('模拟待平仓');
  expect(result.filled.journal[0].trade_date).toBe('2026-08-06');
  expect(result.filled.positions).toEqual({});
});

test('same ETF in a new cycle never inherits old fills or old exit status', async ({ page }) => {
  const result = await page.evaluate(() => {
    fx.flex.as_of = '2026-08-10';
    fx.flex.allocation.w_sat = 1;
    fx.flex.position_state.satellite = { ...fx.model, entry_signal_date: '2026-08-10', entry_date: '2026-08-11' };
    rebuildSimLedgerFromStrategy(fx.flex);
    fx.flex.as_of = '2026-08-11';
    const first = rebuildSimLedgerFromStrategy(fx.flex);
    const second = rebuildSimLedgerFromStrategy(fx.flex);
    return { first, second, label: flexSimSleeveLabel(fx.flex, second, 'satellite') };
  });
  expect(Object.values(result.first.positions)).toHaveLength(1);
  expect(Object.values(result.first.positions)[0]).toMatchObject({ buy_date: '2026-08-11', signal_as_of: '2026-08-10' });
  expect(Object.keys(result.first.positions)[0]).toContain('2026-08-10:2026-08-11');
  expect(result.first.journal[0]).toMatchObject({ type: 'OPEN', trade_date: '2026-08-11', price: 1.2 });
  expect(result.first.journal[1].type).toBe('CLOSE');
  expect(result.second.journal).toEqual(result.first.journal);
  expect(result.label).toBe('模拟持有');
});

test('old snapshot and missing authority cannot invent or roll back a fill', async ({ page }) => {
  const result = await page.evaluate(() => {
    fx.flex.execution_events = [];
    const noAuthority = rebuildSimLedgerFromStrategy(fx.flex);
    fx.flex.execution_events = [fx.event];
    fx.flex.as_of = '2026-08-04';
    const stale = rebuildSimLedgerFromStrategy(fx.flex);
    return { noAuthority, stale };
  });
  expect(result.noAuthority.journal).toHaveLength(240);
  expect(result.noAuthority.cash).toBe(8999.9);
  expect(result.stale.journal).toEqual(result.noAuthority.journal);
  expect(result.stale.strategy_as_of).toBe('2026-08-07');
});

test('model execution without a local holding never creates an actual or simulated trade', async ({ page }) => {
  const result = await page.evaluate(() => {
    fx.ledger.positions = {};
    fx.ledger.cash = 10000;
    localStorage.setItem('ashare_flex_exec_ledger_sim_v1', JSON.stringify(fx.ledger));
    const ledger = rebuildSimLedgerFromStrategy(fx.flex);
    return { ledger, label: flexSimSleeveLabel(fx.flex, ledger, 'satellite'),
      timing: dailyFlexEventMeta(fx.event),
      realUnchanged: localStorage.getItem('ashare_flex_exec_ledger_v1') === fx.real };
  });
  expect(result.ledger.journal).toHaveLength(240);
  expect(result.label).toBe('模型已退出·本机未成交');
  expect(result.timing).toContain('非真实成交');
  expect(result.realUnchanged).toBe(true);
});

test('legacy simulation is archived verbatim with an explicit non-reconstruction marker', async ({ page }) => {
  const result = await page.evaluate(() => {
    fx.ledger.version = 5;
    localStorage.removeItem('ashare_flex_exec_ledger_sim_v1:archive:v5');
    localStorage.setItem('ashare_flex_exec_ledger_sim_v1', JSON.stringify(fx.ledger));
    const first = rebuildSimLedgerFromStrategy(fx.flex);
    const second = rebuildSimLedgerFromStrategy(fx.flex);
    return { first, second, original: fx.ledger,
      archive: JSON.parse(localStorage.getItem(first.migration.archive_key)),
      realUnchanged: localStorage.getItem('ashare_flex_exec_ledger_v1') === fx.real };
  });
  expect(result.archive).toEqual(result.original);
  expect(result.first.migration.history_status).toBe('ARCHIVED_NOT_RECONSTRUCTED');
  expect(result.first.journal).toHaveLength(1);
  expect(result.first.journal[0].type).toBe('MIGRATE');
  expect(result.first.journal[0].note).toContain('不代表历史回测');
  expect(result.second.journal).toEqual(result.first.journal);
  expect(result.realUnchanged).toBe(true);
});

test('schema v3 uses gross and net labels and blocks unverified prospective claims', async ({ page }) => {
  await page.evaluate(() => {
    const metric = { performance_valid: true, ann_return: 0.12, max_dd: -0.08,
      net_win_rate: 0.5, gross_win_rate: 0.6, trade_count: 10 };
    renderFlexValidation({ schemaVersion: 3, full: metric, oos: metric,
      walkForward: { aggregate: metric }, prospective: {
        status: 'BLOCKED_REQUIRES_POINT_IN_TIME_ARCHIVE', strict_prospective: false,
        independent_parameter_validation: true, sample_days: 123, start: '2026-08-12',
      } });
  });
  await expect(page.locator('#flexValidationFull')).toContainText('净胜率50.0%');
  await expect(page.locator('#flexValidationFull')).toContainText('毛胜率60.0%');
  await expect(page.locator('#flexValidationProspective')).toContainText('严格前瞻未验证');
  await expect(page.locator('#flexValidationProspective')).not.toContainText('123日');
  await expect(page.locator('#flexValidationProspective')).not.toContainText('前瞻独立样本');
  await page.evaluate(() => renderFlexValidation({ schemaVersion: 3,
    full: { performance_valid: false, ann_return: 9, max_dd: null } }));
  await expect(page.locator('#flexValidationFull')).toContainText('收益暂不展示');
  await expect(page.locator('#flexValidationFull')).not.toContainText('900');
  expect(await page.evaluate(() => flexBacktestPct(null))).toBe('—');
});

test('full satellite funds new CORE at one open and retries missing prices atomically', async ({ page }) => {
  const result = await page.evaluate(() => {
    const sat = { ...Object.values(fx.ledger.positions)[0], qty: 10000,
      cost_basis: 10001, target_weight: 1 };
    sat.key = flexSimPositionKey(sat);
    const ledger = { ...fx.ledger, cash: 0, journal: [], positions: { [sat.key]: sat },
      cash_watermark: '2026-08-05T15:00:00+08:00' };
    const targets = [{ ...sat, weight: .4 }, { name: '沪深300', etf_code: '510300',
      sleeve: 'core', signal_as_of: '2026-08-05', buy_date: '2026-08-06', weight: .6 }];
    flexSimEnsurePaperPositions(ledger, targets, '2026-08-05');
    delete dashboardState.etfMarks.by_code['510300'].bars['2026-08-06'];
    const blocked = structuredClone(flexSimEnsurePaperPositions(ledger, targets, '2026-08-06'));
    dashboardState.etfMarks.by_code['510300'].bars['2026-08-06'] = { open: 2, close: 50 };
    const filled = flexSimEnsurePaperPositions(ledger, targets, '2026-08-06');
    return { blocked, filled };
  });
  expect(result.blocked.journal).toEqual([]);
  expect(result.blocked.cash).toBe(0);
  expect(result.blocked.pending_allocation.blocked_reason).toBe('MISSING_EXECUTION_PRICE');
  expect(result.filled.pending_allocation.status).toBe('EXECUTED');
  expect(result.filled.journal.map(row => row.type).sort()).toEqual(['OPEN', 'REDUCE']);
  expect(result.filled.journal.every(row => row.trade_date === '2026-08-06' && row.execution_nav === 10500)).toBe(true);
  expect(result.filled.cash).toBeGreaterThanOrEqual(0);
});

test('historical funding blocks a missed entry but not a later newly recorded plan', async ({ page }) => {
  const result = await page.evaluate(() => {
    const ledger = { ...fx.ledger, cash: 10000, positions: {}, journal: [],
      cash_watermark: '2026-08-07T15:00:00+08:00' };
    const old = { name: '沪深300', etf_code: '510300', sleeve: 'core', weight: 1,
      signal_as_of: '2026-08-05', buy_date: '2026-08-06' };
    const blocked = structuredClone(flexSimEnsurePaperPositions(ledger, [old], '2026-08-07'));
    const next = { ...old, signal_as_of: '2026-08-10', buy_date: '2026-08-11' };
    flexSimEnsurePaperPositions(ledger, [next], '2026-08-10');
    return { blocked, filled: flexSimEnsurePaperPositions(ledger, [next], '2026-08-11') };
  });
  expect(result.blocked.positions).toEqual({});
  expect(result.blocked.pending_allocation.blocked_reason).toBe('HISTORICAL_FUNDING_UNVERIFIED');
  expect(result.filled.pending_allocation.status).toBe('EXECUTED');
  expect(result.filled.journal.some(row => row.type === 'ORDER_CANCEL')).toBe(true);
  expect(result.filled.journal.filter(row => row.type === 'OPEN')[0].trade_date).toBe('2026-08-11');
});
