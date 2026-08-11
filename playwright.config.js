const { defineConfig, devices } = require('@playwright/test');

const localChromium = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const chromiumUse = {
  browserName: 'chromium',
  ...(localChromium ? { launchOptions: { executablePath: localChromium } } : {}),
};

module.exports = defineConfig({
  testDir: './tests/browser',
  timeout: 30_000,
  retries: 1,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'python3 tests/browser/static_server.py',
    url: 'http://127.0.0.1:8765',
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], ...chromiumUse } },
    { name: 'mobile', use: { ...devices['iPhone 13'], ...chromiumUse } },
  ],
});
