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
