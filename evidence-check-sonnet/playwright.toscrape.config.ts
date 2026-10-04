import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testMatch: 'toscrape.spec.ts',
  workers: 1,
  timeout: 120_000,
  outputDir: './test-results-toscrape',
  reporter: [['list'], ['json', { outputFile: 'results-toscrape/results.json' }], ['html', { outputFolder: 'playwright-report-toscrape', open: 'never' }]],
  use: {
    video: 'on', trace: 'on', screenshot: 'on',
    viewport: { width: 1280, height: 800 },
    launchOptions: { executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' },
  },
});
