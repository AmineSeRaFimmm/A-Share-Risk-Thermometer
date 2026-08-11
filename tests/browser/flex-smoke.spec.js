const { test, expect } = require('@playwright/test');

test('Flex renders one coherent snapshot without browser errors', async ({ page }) => {
  const errors = [];
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/');
  await expect(page.locator('#riskTemperature')).not.toHaveText('—');
  await page.locator('#dockFlex').click();
  await expect(page.locator('#viewFlex')).toBeVisible();
  await expect(page.locator('#flexAsOf')).not.toHaveText('');
  await expect(page.locator('#flexSignalSections')).toBeVisible();

  const snapshot = await page.evaluate(async () => {
    const response = await fetch('./data/flex_snapshot.json', { cache: 'no-store' });
    return { ok: response.ok, payload: await response.json() };
  });
  expect(snapshot.ok).toBe(true);
  expect(snapshot.payload.schema_version).toBe(1);
  expect(snapshot.payload.revision).toMatch(/^[a-f0-9]{64}$/);
  expect(snapshot.payload.stage_playbook.flex_panel).toBeTruthy();
  expect(snapshot.payload.etf_daily_marks.by_code).toBeTruthy();
  expect(snapshot.payload.trade_calendar.dates.length).toBeGreaterThan(0);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});
