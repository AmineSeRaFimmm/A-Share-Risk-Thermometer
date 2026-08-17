const { test, expect } = require('@playwright/test');

test('Flex renders one coherent snapshot without browser errors', async ({ page }) => {
  const errors = [];
  page.on('console', message => {
    if (message.type() !== 'error') return;
    // Chromium reports optional third-party font failures as an unscoped console
    // error. Same-origin failures are asserted separately with their URL.
    if (message.text().startsWith('Failed to load resource:')) return;
    errors.push(message.text());
  });
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => {
    const pageUrl = page.url();
    if (!pageUrl || pageUrl === 'about:blank') return;
    if (new URL(request.url()).origin !== new URL(pageUrl).origin) return;
    errors.push(`${request.failure()?.errorText || 'request failed'}: ${request.url()}`);
  });

  await page.goto('/');
  await expect(page.locator('#riskTemperature')).not.toHaveText('—');
  await expect(page.locator('#dailyFlexBrief')).toBeVisible();
  await expect(page.locator('#dailyFlexBriefMeta')).not.toHaveText('等待权威快照');
  await expect(page.locator('#qvixSource')).not.toHaveText('--');
  const snapshot = await page.evaluate(async () => {
    const response = await fetch('./data/flex_snapshot.json', { cache: 'no-store' });
    return { ok: response.ok, payload: await response.json() };
  });
  await expect(page.locator('#dailyFlexBriefItems .daily-flex-brief-item')).toHaveCount(
    snapshot.payload.daily_strategy_brief.items.length
  );
  await expect(page.locator('#dailyFlexBriefQuality')).toContainText('信号交易日至执行交易日收盘');
  await page.locator('#dockFlex').click();
  await expect(page.locator('#viewFlex')).toBeVisible();
  await expect(page.locator('#flexAsOf')).not.toHaveText('');
  await expect(page.locator('#flexSignalSections')).toBeVisible();

  expect(snapshot.ok).toBe(true);
  expect(snapshot.payload.schema_version).toBe(2);
  expect(snapshot.payload.revision).toMatch(/^[a-f0-9]{64}$/);
  expect(snapshot.payload.stage_playbook.flex_panel).toBeTruthy();
  expect(snapshot.payload.etf_daily_marks.by_code).toBeTruthy();
  expect(snapshot.payload.trade_calendar.dates.length).toBeGreaterThan(0);
  expect(snapshot.payload.daily_strategy_brief.strategy_id).toBe('FLEX_AGGRESSIVE');
  expect(snapshot.payload.daily_strategy_brief.provenance.browser_ledger_used).toBe(false);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});
