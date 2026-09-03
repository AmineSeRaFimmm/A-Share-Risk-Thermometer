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

test('refresh completion requires the publication revision for its own mode', async ({ page }) => {
  await page.goto('/');
  const checks = await page.evaluate(() => {
    dashboardState.lastBuildTime = 'build-old';
    dashboardState.lastUpdateTime = 'rt-new';
    dashboardState.flexSnapshotRevision = 'flex-old';
    const dailyRejectsRealtimeOnly = dashboardMatchesPublishedRevision({
      mode: 'full', buildTime: 'build-new', updateTime: 'rt-new', flexRevision: 'flex-new',
    });
    dashboardState.lastBuildTime = 'build-new';
    const dailyRejectsStaleFlex = dashboardMatchesPublishedRevision({
      mode: 'full', buildTime: 'build-new', updateTime: 'rt-new', flexRevision: 'flex-new',
    });
    dashboardState.flexSnapshotRevision = 'flex-new';
    const dailyAcceptsCoherentBuild = dashboardMatchesPublishedRevision({
      mode: 'full', buildTime: 'build-new', updateTime: 'rt-new', flexRevision: 'flex-new',
    });
    return { dailyRejectsRealtimeOnly, dailyRejectsStaleFlex, dailyAcceptsCoherentBuild };
  });

  expect(checks).toEqual({
    dailyRejectsRealtimeOnly: false,
    dailyRejectsStaleFlex: false,
    dailyAcceptsCoherentBuild: true,
  });
});

test('stale strategy is explicitly pending instead of reported as no action', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => {
    renderDailyFlexBrief({
      schema_version: 2,
      strategy_id: 'FLEX_AGGRESSIVE',
      as_of: '2026-09-03',
      marks_as_of: '2026-09-02',
      status: 'OFFICIAL_PENDING',
      headline_cn: '2026-09-03 正式 Flex 策略待生成',
      items: [],
      satellite_risk_event: { status: 'CLEAR' },
      visibility_policy: { label_cn: '信号交易日至执行交易日收盘' },
      data_quality: {
        strategy_as_of: '2026-09-02',
        marks_quality: 'OK',
        official_strategy_pending: true,
        strategy_publication_status: 'OFFICIAL_PENDING',
      },
    });
  });

  await expect(page.locator('#dailyFlexBriefSummary')).toContainText('正式 Flex 策略待生成');
  await expect(page.locator('#dailyFlexBriefItems')).toContainText('尚未生成');
  await expect(page.locator('#dailyFlexBriefQuality')).toContainText('今日正式策略待生成');
});
