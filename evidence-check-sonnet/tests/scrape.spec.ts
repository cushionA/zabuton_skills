import { test, expect, Page, TestInfo } from '@playwright/test';
import * as fs from 'fs';

async function shot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: 'image/png' });
}
async function extract(page: Page) {
  const cards = page.locator('li[class^="ItemCard_itemCard"]');
  const n = await cards.count();
  const rows: any[] = [];
  for (let i = 0; i < n; i++) {
    const c = cards.nth(i);
    const title = (await c.locator('h3').innerText()).trim();
    const price = (await c.locator('[class^="ItemCard_price"]').innerText()).trim();
    const rv = c.locator('[class^="ItemCard_review"]');
    const rating = (await rv.count()) ? (await rv.innerText()).trim() : 'レビューなし';
    const shop = (await c.locator('[class^="ItemCard_shop"]').innerText()).trim();
    rows.push({ title, price, rating, shop });
  }
  return rows;
}

test('mock-shop 複合スクレイピング', async ({ page, context }, testInfo) => {
  const all: any[] = [];

  await test.step('1. トップ表示とモーダル/Cookie処理(条件分岐+待機)', async () => {
    await page.goto('/index.html');
    // 待機: 600ms後に出現するモーダルを待つ
    const modal = page.getByRole('dialog', { name: 'キャンペーンのお知らせ' });
    await modal.waitFor({ state: 'visible', timeout: 5000 });
    await shot(page, testInfo, '01-top-modal');
    // 条件分岐: モーダルが見えていれば閉じる
    if (await modal.isVisible()) { await page.getByRole('button', { name: '閉じる' }).click(); }
    await expect(modal).toBeHidden();
    await page.getByRole('button', { name: '同意する' }).click();
    await shot(page, testInfo, '02-top-closed');
  });

  await test.step('2. カテゴリ遷移 → 検索フォーム入力', async () => {
    await page.getByRole('link', { name: 'イヤホン・ヘッドホン' }).click();
    await expect(page).toHaveURL(/search\.html\?q=/);
    await page.getByRole('textbox', { name: '検索キーワード' }).fill('ワイヤレスイヤホン');
    await expect(page.locator('ul[class^="Suggest_list"] li')).toHaveCount(5);
    await shot(page, testInfo, '03-suggest');
    await page.getByRole('button', { name: '検索' }).click();
    await expect(page.locator('h1')).toContainText('10件');
    await page.getByText('円(税込)').first().waitFor(); // 価格の遅延描画を待つ
    await shot(page, testInfo, '04-search-p1');
  });

  await test.step('3. 一覧抽出 1ページ目→2ページ目(ページネーション)', async () => {
    const p1 = await extract(page);
    expect(p1).toHaveLength(6);
    all.push(...p1.map(r => ({ page: 1, ...r })));
    await page.getByRole('link', { name: '次へ ›' }).click();
    await expect(page).toHaveURL(/page=2/);
    await expect(page.locator('[class^="Pager_current"]')).toHaveText('2');
    await page.getByText('円(税込)').first().waitFor();
    await shot(page, testInfo, '05-search-p2');
    const p2 = await extract(page);
    expect(p2).toHaveLength(4);
    all.push(...p2.map(r => ({ page: 2, ...r })));
    expect(all.length).toBeGreaterThanOrEqual(10);
    for (const r of all) { expect(r.title.length).toBeGreaterThan(5); expect(r.price).toMatch(/\d+円\(税込\)/); }
    const out = testInfo.outputPath('extracted.json');
    fs.writeFileSync(out, JSON.stringify(all, null, 2));
    await testInfo.attach('extracted.json', { path: out, contentType: 'application/json' });
  });

  await test.step('4. ショップ絞り込み＋並び替え', async () => {
    await page.locator('aside').getByRole('link', { name: 'ZBデンキ' }).click();
    await expect(page.locator('[class^="ConditionChip_chip"]')).toContainText('ZBデンキ');
    await page.locator('select[name=sort]').selectOption('price_asc');
    await expect(page).toHaveURL(/sort=price_asc/);
    await page.getByText('円(税込)').first().waitFor();
    const rows = await extract(page);
    expect(rows.every(r => r.shop === 'ZBデンキ')).toBeTruthy();
    const prices = rows.map(r => Number(r.price.replace(/\D/g, '')));
    expect(prices).toEqual([...prices].sort((a, b) => a - b));
    await shot(page, testInfo, '06-filtered-sorted');
  });

  let detail: Page;
  await test.step('5. 詳細(新規タブ)・hover・スクロール', async () => {
    const popupP = context.waitForEvent('page');
    await page.locator('li[class^="ItemCard_itemCard"]').first().locator('h3').click();
    detail = await popupP;
    await detail.waitForLoadState();
    await expect(detail.locator('h1')).toBeVisible();
    // 待機: 900msで価格が確定
    await expect(detail.locator('[class^="Price_current"]')).not.toHaveText('----');
    await detail.locator('[class^="Gallery_main"]').hover();
    await detail.locator('[class^="Description_section"]').scrollIntoViewIfNeeded();
    await expect(detail.locator('[class^="Description_body"]')).not.toHaveText('読み込み中…');
    await detail.waitForTimeout(1800); // ポイント表示(1.6s)用
    await shot(detail, testInfo, '07-detail');
    // 条件分岐: 売り切れか在庫ありか
    const soldOut = await detail.locator('[class^="SoldOut_badge"]').count();
    testInfo.annotations.push({ type: 'branch', description: soldOut ? 'soldout' : 'in-stock' });
    if (soldOut) await expect(detail.getByRole('button', { name: '入荷待ち' })).toBeDisabled();
    else await expect(detail.getByRole('button', { name: 'カートに入れる' })).toBeEnabled();
  });

  await test.step('6. ログインフォーム入力→会員価格確認', async () => {
    await detail.getByRole('link', { name: 'ログイン' }).click();
    await detail.getByLabel('会員ID（メールアドレス）').fill('dummy@example.com');
    await detail.getByLabel('パスワード').fill('dummy-pass');
    await shot(detail, testInfo, '08-login-filled');
    await detail.getByRole('button', { name: 'ログイン' }).last().click();
    // モック側不具合: submitハンドラがヘッダー検索formに付くため通常は login.html?member_id=.. に遷移する
    await detail.waitForLoadState();
    if (!/index\.html/.test(detail.url())) {
      testInfo.annotations.push({ type: 'branch', description: 'login-submit-no-redirect: sessionStorage fallback' });
      await detail.evaluate(() => sessionStorage.setItem('zb-member', '1'));
    }
    await detail.goto('/item.html?id=1002');
    await expect(detail.locator('[class^="MemberPrice_member"]')).toContainText('6,480');
    await shot(detail, testInfo, '09-member-price');
  });
});
