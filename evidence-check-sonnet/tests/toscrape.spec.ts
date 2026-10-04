import { test, expect, Page, TestInfo } from '@playwright/test';
import * as fs from 'fs';

async function shot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: 'image/png' });
}
async function extractQuotes(page: Page) {
  const cards = page.locator('div.quote');
  const n = await cards.count();
  const rows: { text: string; author: string; tags: string[] }[] = [];
  for (let i = 0; i < n; i++) {
    const c = cards.nth(i);
    const text = (await c.locator('span.text').innerText()).trim();
    const author = (await c.locator('small.author').innerText()).trim();
    const tags = (await c.locator('a.tag').allInnerTexts()).map(t => t.trim());
    rows.push({ text, author, tags });
  }
  return rows;
}
async function extractBooks(page: Page) {
  const cards = page.locator('article.product_pod');
  const n = await cards.count();
  const rows: { title: string; price: string }[] = [];
  for (let i = 0; i < n; i++) {
    const c = cards.nth(i);
    const title = ((await c.locator('h3 a').getAttribute('title')) ?? '').trim();
    const price = (await c.locator('.price_color').innerText()).trim();
    rows.push({ title, price });
  }
  return rows;
}

test('toscrape 複合スクレイピング(quotes + books)', async ({ page, context }, testInfo) => {
  const quotes: any[] = [];
  const books: any[] = [];

  await test.step('1. quotes トップ表示と引用の描画待ち', async () => {
    await page.goto('https://quotes.toscrape.com/');
    await expect(page.locator('div.quote').first()).toBeVisible();
    await expect(page.locator('div.quote')).toHaveCount(10);
    await shot(page, testInfo, '01-quotes-top');
  });

  await test.step('2. Top Ten tags の love 条件分岐', async () => {
    const love = page.locator('.tags-box a.tag', { hasText: /^love$/ });
    if (await love.count()) {
      testInfo.annotations.push({ type: 'branch', description: 'tag-love: clicked from Top Ten tags' });
      await love.first().click();
    } else {
      testInfo.annotations.push({ type: 'branch', description: 'tag-love: not in Top Ten, goto /tag/love/' });
      await page.goto('https://quotes.toscrape.com/tag/love/');
    }
    await expect(page).toHaveURL(/\/tag\/love\//);
    await expect(page.locator('h3')).toContainText('love');
    await shot(page, testInfo, '02-tag-love-p1');
  });

  await test.step('3. タグ絞り込みの2ページ抽出(10件→Next→4件)', async () => {
    const p1 = await extractQuotes(page);
    expect(p1).toHaveLength(10);
    quotes.push(...p1.map(r => ({ page: 1, ...r })));
    await page.locator('li.next a').click();
    await expect(page).toHaveURL(/\/tag\/love\/page\/2\//);
    await expect(page.locator('div.quote').first()).toBeVisible();
    await shot(page, testInfo, '03-tag-love-p2');
    const p2 = await extractQuotes(page);
    expect(p2).toHaveLength(4);
    quotes.push(...p2.map(r => ({ page: 2, ...r })));
    expect(quotes).toHaveLength(14);
    expect(quotes.length).toBeGreaterThanOrEqual(10);
    for (const r of quotes) {
      expect(r.text.startsWith('“')).toBeTruthy();
      expect(r.author.length).toBeGreaterThan(0);
      expect(r.tags.length).toBeGreaterThanOrEqual(1);
    }
    const out = testInfo.outputPath('extracted-quotes.json');
    fs.writeFileSync(out, JSON.stringify(quotes, null, 2));
    await testInfo.attach('extracted-quotes.json', { path: out, contentType: 'application/json' });
  });

  await test.step('4. ログインフォーム入力と結果の条件分岐', async () => {
    await page.goto('https://quotes.toscrape.com/login');
    await page.locator('#username').fill('dummy_user');
    await page.locator('#password').fill('dummy-pass');
    await shot(page, testInfo, '04-login-filled');
    await page.getByRole('button', { name: 'Login' }).click();
    await page.waitForLoadState();
    const logout = page.getByRole('link', { name: 'Logout' });
    if (await logout.count()) {
      testInfo.annotations.push({ type: 'branch', description: 'login: logged-in' });
      await expect(logout).toBeVisible();
    } else {
      testInfo.annotations.push({ type: 'branch', description: 'login: failed' });
      await expect(page.getByRole('link', { name: 'Login' })).toBeVisible();
    }
    await shot(page, testInfo, '05-after-login');
  });

  await test.step('5. 作者(about)を Ctrl+クリックで新規タブ表示', async () => {
    await page.goto('https://quotes.toscrape.com/');
    await expect(page.locator('div.quote').first()).toBeVisible();
    const about = page.locator('div.quote').first().getByRole('link', { name: '(about)' });
    const popupP = context.waitForEvent('page', { timeout: 5000 }).catch(() => null);
    await about.click({ modifiers: ['Control'] });
    let author = await popupP;
    if (author) {
      testInfo.annotations.push({ type: 'branch', description: 'author-tab: opened by Ctrl+click' });
      await author.waitForLoadState();
    } else {
      testInfo.annotations.push({ type: 'branch', description: 'author-tab: fallback newPage+goto' });
      author = await context.newPage();
      await author.goto('https://quotes.toscrape.com/author/Albert-Einstein');
    }
    await expect(author.locator('h3.author-title')).toBeVisible();
    await expect(author.locator('.author-born-date')).toBeVisible();
    await author.waitForTimeout(1500); // 動画で局面を確認しやすくするための待機
    await shot(author, testInfo, '06-author-tab');
  });

  await test.step('6. books トップ → Mystery カテゴリ', async () => {
    await page.goto('https://books.toscrape.com/');
    await expect(page.locator('article.product_pod').first()).toBeVisible();
    await shot(page, testInfo, '07-books-top');
    await page.locator('.side_categories').getByRole('link', { name: 'Mystery' }).click();
    await expect(page.locator('h1')).toHaveText('Mystery');
    await expect(page.locator('article.product_pod').first()).toBeVisible();
    await shot(page, testInfo, '08-mystery-p1');
  });

  await test.step('7. Mystery 2ページ抽出(20件→next→12件)', async () => {
    const p1 = await extractBooks(page);
    expect(p1).toHaveLength(20);
    books.push(...p1.map(r => ({ page: 1, ...r })));
    await page.getByRole('link', { name: 'next' }).click();
    await expect(page).toHaveURL(/page-2\.html/);
    await expect(page.locator('li.current')).toContainText('Page 2 of 2');
    await page.locator('article.product_pod').last().scrollIntoViewIfNeeded();
    await shot(page, testInfo, '09-mystery-p2');
    const p2 = await extractBooks(page);
    expect(p2).toHaveLength(12);
    books.push(...p2.map(r => ({ page: 2, ...r })));
    expect(books).toHaveLength(32);
    for (const r of books) {
      expect(r.title.length).toBeGreaterThan(0);
      expect(r.price).toMatch(/£\d+\.\d\d/);
    }
    const out = testInfo.outputPath('extracted-books.json');
    fs.writeFileSync(out, JSON.stringify(books, null, 2));
    await testInfo.attach('extracted-books.json', { path: out, contentType: 'application/json' });
  });

  await test.step('8. 最初の本の詳細と価格確定後の最終スクショ', async () => {
    await page.locator('article.product_pod').first().locator('h3 a').click();
    await expect(page.locator('.product_main h1')).toBeVisible();
    await expect(page.locator('.product_main .price_color')).toHaveText(/£\d/);
    await shot(page, testInfo, '10-book-detail-final');
  });
});
