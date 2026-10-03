const { chromium } = require('playwright');
const fs = require('fs');

const USER = 'zabuton-test';
const PASSWORD = process.env.QUOTES_PASSWORD ?? 'Tsk-2026-demo!';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto('https://quotes.toscrape.com/login');
  await page.getByLabel('Username').fill(USER);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Login' }).click();

  // JavaScript で 10 秒遅れて描画されるページ
  await page.goto('https://quotes.toscrape.com/js-delayed/');
  const rows = [];
  while (true) {
    await page.locator('.quote').first().waitFor({ timeout: 20000 });
    for (const quote of await page.locator('.quote').all()) {
      rows.push({
        crawledAt: new Date().toISOString(),
        pageUrl: page.url(),
        text: await quote.locator('.text').innerText(),
        author: await quote.locator('.author').innerText(),
        tags: (await quote.locator('.tags .tag').allInnerTexts()).join(','),
      });
    }
    const next = page.getByRole('link', { name: 'Next' });
    if (await next.count() === 0) break;
    await next.click();
  }

  fs.writeFileSync('quotes.json', JSON.stringify(rows, null, 2));
  await browser.close();
})();
