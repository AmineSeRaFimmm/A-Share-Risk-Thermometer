/**
 * Expo config for TestFlight / production.
 *
 * Default data plane = GitHub Pages (Actions updates data every trading day).
 * Override at build time:
 *   EXPO_PUBLIC_WEB_URL=https://other.example.com eas build -p ios
 */
const appJson = require('./app.json');

const DEFAULT_PAGES =
  'https://amineserafimmm.github.io/A-Share-Risk-Thermometer/';

const webUrl = (process.env.EXPO_PUBLIC_WEB_URL || '').trim() || DEFAULT_PAGES;

module.exports = {
  ...appJson,
  expo: {
    ...appJson.expo,
    extra: {
      ...(appJson.expo.extra || {}),
      webUrl,
      dataPlane: 'github_pages',
      allowGithub: false,
    },
  },
};
