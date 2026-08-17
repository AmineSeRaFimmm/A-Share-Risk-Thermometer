const { test, expect } = require('@playwright/test');
const snapshot = require('../../docs/data/flex_snapshot.json');

const strategyAsOf = snapshot.stage_playbook.as_of;
const sessions = snapshot.trade_calendar.dates;
const sessionDate = sessions.find(day => day > strategyAsOf) || strategyAsOf;
const fixedNow = new Date(`${sessionDate}T08:00:00+08:00`).getTime();

async function seedFlex(page, { book = 'real', ledger = null } = {}) {
  await page.addInitScript(({ selectedBook, seededLedger, nowMs }) => {
    const NativeDate = Date;
    class FixedDate extends NativeDate {
      constructor(...args) {
        super(...(args.length ? args : [nowMs]));
      }
      static now() { return nowMs; }
    }
    globalThis.Date = FixedDate;
    localStorage.setItem('ashare_flex_book_v1', selectedBook);
    if (seededLedger) {
      localStorage.setItem(
        selectedBook === 'sim' ? 'ashare_flex_exec_ledger_sim_v1' : 'ashare_flex_exec_ledger_v1',
        JSON.stringify(seededLedger),
      );
    }
  }, { selectedBook: book, seededLedger: ledger, nowMs: fixedNow });
}

test('Flex marks unmapped holdings as missing and exposes validation quality', async ({ page }) => {
  await seedFlex(page, {
    ledger: {
      version: 5,
      book: 'real',
      capital: 100000,
      cash: 53750,
      strategy_as_of: strategyAsOf,
      journal: [],
      risk_exits: {},
      pending_orders: {},
      positions: {
        core: {
          key: 'core', name: '沪深300', etf_code: '510300', sleeve: 'core',
          qty: 10000, avg_price: 4.6, cost_basis: 46000, last_price: 4.6,
          buy_date: strategyAsOf, hold_days: 5,
        },
        unmapped: {
          key: 'unmapped', name: '手工未映射', etf_code: '', sleeve: 'satellite',
          qty: 1000, avg_price: 0.25, cost_basis: 250, last_price: 0.25,
          buy_date: strategyAsOf, hold_days: 3,
        },
      },
    },
  });
  await page.goto('/');
  await page.locator('#dockFlex').click();
  await page.locator('#flexTabBook').click();

  const row = page.locator('.flex-row-book[data-pos-key="unmapped"]');
  await expect(row).toContainText('缺价');
  await expect(row).toContainText('缺ETF代码');
  await expect(row).not.toContainText('0.00%');
  await expect(page.locator('#flexMarkNote')).toContainText('风控共同EOD不完整');
  await expect(page.locator('#flexTrustLine')).toBeVisible();
  await expect(page.locator('#flexTrustLine')).toContainText('正式风险组件');
  await expect(page.locator('#flexValidationOos')).toContainText('回顾OOS 非独立');
  await expect(page.locator('#flexValidationWalkForward')).toContainText('Walk-forward固定策略');
});

test('Flex generic satellite close is not presented as take profit', async ({ page }) => {
  await seedFlex(page, { book: 'sim' });
  await page.goto('/');
  await page.locator('#dockFlex').click();
  const label = await page.evaluate(() => {
    const flex = dashboardState.flexActive;
    flex.close_list = [{
      name: '煤炭', sector: '煤炭', etf_code: '515220', sleeve: 'satellite',
      action: 'CLOSE', action_cn: '持有期满卖出', close_code: 'MAX_HOLD',
      signal_as_of: flex.as_of,
    }];
    renderFlexTradePanel({ as_of: flex.as_of, flex_panel: flex, data_quality: flex.data_quality });
    return document.querySelector('#flexSatStage')?.textContent;
  });
  expect(label).toBe('策略待平');
});

test('Flex buy ledger records the selected trading session', async ({ page }) => {
  await seedFlex(page, {
    ledger: {
      version: 5, book: 'real', capital: 100000, cash: 100000,
      positions: {}, journal: [], risk_exits: {}, pending_orders: {},
    },
  });
  await page.goto('/');
  await page.locator('#dockFlex').click();
  await page.evaluate(({ asOf }) => {
    openFlexTradeModal({
      mode: 'buy', title: '买入记账', key: 'core:510300', name: '沪深300',
      etf_code: '510300', etf_name: '沪深300ETF', sleeve: 'core',
      signal_as_of: asOf, hold_days: 5, execution_mode: 'T_PLUS_1_OPEN',
      defaultAmount: 10000, defaultPrice: 4,
    });
  }, { asOf: strategyAsOf });

  await expect(page.locator('#flexModalTradeDate')).toHaveValue(sessionDate);
  await expect(page.locator('#flexModalTradeDateHint')).toContainText(`计划 ${sessionDate}`);
  await page.locator('#flexModalConfirmBtn').click();
  const recorded = await page.evaluate(() => JSON.parse(localStorage.getItem('ashare_flex_exec_ledger_v1')));
  expect(recorded.positions['core:510300'].buy_date).toBe(sessionDate);
  expect(recorded.journal[0].trade_date).toBe(sessionDate);
});

test('Flex v6 recovers fixed-basket take profit from the first EOD crossing', async ({ page }) => {
  await seedFlex(page, {
    book: 'sim',
    ledger: {
      version: 5, book: 'sim', capital: 60000, cash: 12000,
      positions: {
        corrupted: {
          key: 'corrupted', name: '煤炭', etf_code: '515220', sleeve: 'satellite',
          qty: 1000, avg_price: 1, cost_basis: 1000, buy_date: '2026-08-05',
        },
      },
      journal: [], risk_exits: {}, pending_orders: {},
    },
  });
  await page.goto('/');
  const result = await page.evaluate(() => {
    // App startup may already migrate the seeded v5 ledger. Reset the fixture
    // immediately before exercising the migration so this test is isolated.
    localStorage.setItem('ashare_flex_exec_ledger_sim_v1', JSON.stringify({
      version: 5, book: 'sim', capital: 60000, cash: 12000,
      positions: {
        corrupted: {
          key: 'corrupted', name: '煤炭', etf_code: '515220', sleeve: 'satellite',
          qty: 1000, avg_price: 1, cost_basis: 1000, buy_date: '2026-08-05',
        },
      },
      journal: [], risk_exits: {}, pending_orders: {},
    }));
    const bars = {
      '2026-07-30': { open: 1.0, close: 1.0, high: 1.0, low: 1.0 },
      '2026-07-31': { open: 1.0, close: 1.0, high: 1.0, low: 1.0 },
      '2026-08-03': { open: 1.0, close: 1.0, high: 1.0, low: 1.0 },
      '2026-08-04': { open: 1.0, close: 1.03, high: 1.03, low: 1.0 },
      '2026-08-05': { open: 1.03, close: 1.05, high: 1.05, low: 1.03 },
      '2026-08-06': { open: 1.04, close: 1.04, high: 1.04, low: 1.04 },
    };
    dashboardState.etfMarks = {
      as_of: '2026-08-06', complete_as_of: '2026-08-06', quality: 'OK', missing_codes: [],
      by_code: {
        '515880': { etf_code: '515880', bars },
        '513180': { etf_code: '513180', bars },
      },
    };
    dashboardState.latest = { official_close: { trade_date: '2026-08-06' } };
    dashboardState.flexTradeDates = Object.keys(bars);
    const flex = {
      as_of: '2026-08-06', mode: 'aggressive',
      market_state: { trade_date: '2026-08-06' },
      modes: { aggressive: { core_when_signal: 0.6, sat_when_signal: 0.4 } },
      allocation: { w_core: 0, w_sat: 1 },
      hold_days_sat: 8,
      satellite_risk_rule: { stop_loss: -0.03, take_profit: 0.04 },
      hold_list: [
        { name: '通信', etf_code: '515880', etf_name: '通信ETF' },
        { name: '恒生科技', etf_code: '513180', etf_name: '恒生科技ETF' },
      ],
      position_state: {
        core: { status: 'flat' },
        satellite: {
          status: 'open', entry_signal_date: '2026-07-29', entry_date: '2026-07-30',
          names: ['通信', '恒生科技'], weights: { 通信: 0.5, 恒生科技: 0.5 },
        },
      },
    };
    const ledger = rebuildSimLedgerFromStrategy(flex);
    return {
      version: ledger.version,
      basis: ledger.satellite_risk_basis,
      risk: ledger.risk_exits['2026-07-29'],
      satelliteCount: Object.values(ledger.positions).filter(pos => pos.sleeve === 'satellite').length,
      signal: ledger.journal.find(row => row.type === 'SIGNAL' && row.name === '卫星组合'),
    };
  });

  expect(result.version).toBe(6);
  expect(result.basis.entry_date).toBe('2026-07-30');
  expect(result.risk.status).toBe('EXECUTED');
  expect(result.risk.signal_date).toBe('2026-08-05');
  expect(result.risk.execution_date).toBe('2026-08-06');
  expect(result.satelliteCount).toBe(0);
  expect(result.signal.trade_date).toBe('2026-08-05');
});
