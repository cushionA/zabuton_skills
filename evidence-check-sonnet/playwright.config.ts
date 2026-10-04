import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 90_000,
  outputDir: './test-results',
  reporter: [['list'], ['json', { outputFile: 'results/results.json' }], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  webServer: { command: 'node serve.js', url: 'http://127.0.0.1:8123/index.html', reuseExistingServer: true },
  use: {
    baseURL: 'http://127.0.0.1:8123',
    video: 'on', trace: 'on', screenshot: 'on',
    viewport: { width: 1280, height: 800 },
    launchOptions: { executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' },
  },
});
