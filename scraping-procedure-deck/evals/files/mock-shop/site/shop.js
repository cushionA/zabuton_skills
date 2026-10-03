(function () {
  const REV = window.SITE_REV || 1;

  // CSS Modules 風のハッシュ付きクラス名。改修（REV）でサフィックスが変わる
  function cls(name) {
    let h = 7;
    for (const ch of name + ":" + REV) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return name + "__" + h.toString(36).slice(0, 5);
  }

  const SHOPS = { "zb-denki": "ZBデンキ", "zabuton-kaden": "ザブトン家電", "test-store": "テストストア" };
  const P = [
    { id: 1001, shop: "zb-denki", name: "ZB サウンドポッド Lite ワイヤレスイヤホン（ノイズキャンセリング／Bluetooth 5.3）ブラック ZB-SP100", price: 12800, rate: 1, reviews: 8, rating: 4.3, maker: "ZBオーディオ", model: "ZB-SP100-BK", jan: "4900000000017", release: "2025年4月1日", member: 11500 },
    { id: 1002, shop: "zb-denki", name: "ZB サウンドポッド Mini ワイヤレスイヤホン ホワイト ZB-SP050", price: 6980, rate: 1, reviews: 0, rating: 0, maker: "ZBオーディオ", model: "ZB-SP050-WH", jan: "4900000000024", release: "2025年6月20日", member: 6480 },
    { id: 1003, shop: "zb-denki", name: "ZB ノイズキャンセリング ワイヤレスヘッドホン Pro ZB-HP900", price: 19800, list: 39800, rate: 2, reviews: 23, rating: 4.6, maker: "ZBオーディオ", model: "ZB-HP900", jan: "4900000000031", release: "2024年11月8日", member: 18800 },
    { id: 1004, shop: "zb-denki", name: "ZB 骨伝導 ワイヤレスイヤホン Run ZB-BC200", price: 8980, rate: 1, reviews: 3, rating: 3.7, soldout: true, maker: "ZBオーディオ", model: "ZB-BC200", jan: "4900000000048", release: "2023年9月1日", member: 8480 },
    { id: 1005, shop: "zabuton-kaden", name: "ざぶとん ワイヤレスイヤホン 完全ワイヤレス 防水 IPX5 ネイビー", price: 3980, rate: 1, reviews: 41, rating: 4.0, maker: "ざぶとん電機", model: "ZT-E10-NV", jan: "4900000000055", release: "2025年1月15日", member: 3780 },
    { id: 1006, shop: "test-store", name: "テスト ワイヤレスイヤホン スポーツ Fit 耳かけ型", price: 5480, rate: 1, reviews: 12, rating: 4.1, maker: "テスト音響", model: "TS-FIT2", jan: "4900000000062", release: "2025年3月3日", member: 5180 },
    { id: 1007, shop: "zb-denki", name: "ZB サウンドポッド Lite ワイヤレスイヤホン ホワイト ZB-SP100", price: 12800, rate: 1, reviews: 5, rating: 4.4, maker: "ZBオーディオ", model: "ZB-SP100-WH", jan: "4900000000079", release: "2025年4月1日", member: 11500 },
    { id: 1008, shop: "zabuton-kaden", name: "ざぶとん ワイヤレスイヤホン 片耳 ビジネス 通話用", price: 2980, rate: 1, reviews: 7, rating: 3.9, maker: "ざぶとん電機", model: "ZT-B01", jan: "4900000000086", release: "2024年8月8日", member: 2880 },
    { id: 1009, shop: "test-store", name: "テスト ワイヤレスイヤホン キッズ 音量制限", price: 4280, rate: 1, reviews: 2, rating: 4.5, maker: "テスト音響", model: "TS-KID1", jan: "4900000000093", release: "2025年5月5日", member: 4080 },
    { id: 1010, shop: "zb-denki", name: "ZB ワイヤレスイヤホン 充電ケース ZB-CC10", price: 2480, rate: 1, reviews: 9, rating: 4.2, maker: "ZBオーディオ", model: "ZB-CC10", jan: "4900000000109", release: "2025年4月1日", member: 2380 },
  ];

  const $ = (sel, root = document) => root.querySelector(sel);
  const yen = (n) => n.toLocaleString("ja-JP");
  const qs = new URLSearchParams(location.search);
  const member = () => sessionStorage.getItem("zb-member") === "1";
  const stars = (r) => "★".repeat(Math.round(r)) + "☆".repeat(5 - Math.round(r));

  function thumb(p, size) {
    const hue = (p.id * 47) % 360;
    return `<svg width="${size}" height="${size}" viewBox="0 0 100 100" role="img" aria-label="${p.model}">
      <rect width="100" height="100" fill="hsl(${hue},35%,92%)"/>
      <circle cx="38" cy="50" r="16" fill="hsl(${hue},30%,35%)"/><circle cx="64" cy="50" r="16" fill="hsl(${hue},30%,45%)"/>
      <text x="50" y="90" font-size="9" text-anchor="middle" fill="#555">${p.model}</text></svg>`;
  }

  function header() {
    const q = qs.get("q") || "";
    return `<header class="${cls("Header_header")}"><div class="wrap ${cls("Header_inner")}">
      <a class="${cls("Header_logo")}" href="index.html">ZBマート<small>検証用モック</small></a>
      <form class="${cls("Search_form")}" action="search.html" autocomplete="off">
        <input type="text" name="q" value="${q}" placeholder="キーワードを入力して検索" aria-label="検索キーワード">
        <button type="submit">検索</button>
        <ul class="${cls("Suggest_list")}" hidden></ul>
      </form>
      <nav class="${cls("Header_nav")}"><a href="ranking.html">ランキング</a><a href="login.html">ログイン</a><a href="#">カート</a></nav>
    </div></header>`;
  }

  function cookieBanner() {
    if (localStorage.getItem("zb-cookie") === "ok") return "";
    return `<div class="${cls("CookieBanner_banner")}" role="dialog" aria-label="Cookieの利用について">
      <p>当サイトでは、利便性の向上と広告配信のためにCookieを使用しています。サイトの利用を続けることで、Cookieの使用に同意したものとみなされます。</p>
      <button type="button" data-cookie-ok>同意する</button></div>`;
  }

  function mount(body) {
    document.body.innerHTML = header() + `<main class="wrap">${body}</main><footer class="${cls("Footer_footer")}">© ZBマート（スクレイピング手順書スキルの検証用モックサイト）</footer>` + cookieBanner();
    const ok = $("[data-cookie-ok]");
    if (ok) ok.addEventListener("click", () => { localStorage.setItem("zb-cookie", "ok"); ok.parentElement.remove(); });
    const input = $("form input[name=q]");
    const list = $(`[class^="Suggest_list"]`);
    input.addEventListener("input", () => {
      const v = input.value.trim();
      if (!v) { list.hidden = true; return; }
      list.innerHTML = ["", " ノイズキャンセリング", " 防水", " 片耳", " 安い"].map((s) => `<li>${v}${s}</li>`).join("");
      list.hidden = false;
    });
    input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
  }

  function card(p) {
    const review = p.reviews ? `<span class="${cls("ItemCard_review")}">${stars(p.rating)} (${p.reviews})</span>` : "";
    return `<li class="${cls("ItemCard_itemCard")}">
      <a href="item.html?id=${p.id}" target="_blank" rel="noopener" class="${cls("ItemCard_thumb")}">${thumb(p, 120)}</a>
      <div class="${cls("ItemCard_body")}">
        <a href="item.html?id=${p.id}" target="_blank" rel="noopener"><h3 class="${cls("ItemCard_title")}">${p.name}</h3></a>
        <p class="${cls("ItemCard_price")}" data-price="${p.price}">価格を読み込み中…</p>
        ${review}<p class="${cls("ItemCard_shop")}">${SHOPS[p.shop]}</p>
      </div></li>`;
  }

  const pages = {
    index() {
      mount(`<section class="${cls("Hero_hero")}"><h1>秋のオーディオ祭り</h1><p>ワイヤレスイヤホンが最大50%OFF（10/31まで）</p></section>
        <section class="${cls("Category_list")}"><h2>カテゴリから探す</h2><ul>
          ${["イヤホン・ヘッドホン", "スピーカー", "スマホアクセサリー", "カメラ", "生活家電", "キッチン家電"].map((c) => `<li><a href="search.html?q=${encodeURIComponent(c)}">${c}</a></li>`).join("")}
        </ul></section>`);
      setTimeout(() => {
        document.body.insertAdjacentHTML("beforeend", `<div class="${cls("Modal_overlay")}"><div class="${cls("Modal_box")}" role="dialog" aria-label="キャンペーンのお知らせ">
          <button type="button" class="${cls("Modal_close")}" aria-label="閉じる">×</button>
          <p class="${cls("Modal_title")}">新規会員登録で 500ポイント プレゼント</p><p>今なら登録するだけで、すぐに使えるポイントを進呈中です。</p>
          <a href="login.html" class="${cls("Modal_cta")}">会員登録へ進む</a></div></div>`);
        $(`[class^="Modal_close"]`).addEventListener("click", (e) => e.target.closest(`[class^="Modal_overlay"]`).remove());
      }, 600);
    },

    search() {
      const q = qs.get("q") || "";
      const shop = qs.get("shop");
      const sort = qs.get("sort") || "recommend";
      const page = Number(qs.get("page") || 1);
      let items = P.filter((p) => !shop || p.shop === shop);
      if (sort === "price_asc") items = [...items].sort((a, b) => a.price - b.price);
      if (sort === "price_desc") items = [...items].sort((a, b) => b.price - a.price);
      if (sort === "reviews") items = [...items].sort((a, b) => b.reviews - a.reviews);
      const per = 6;
      const pagesN = Math.max(1, Math.ceil(items.length / per));
      const shown = items.slice((page - 1) * per, page * per);
      const link = (extra) => "search.html?" + new URLSearchParams({ q, ...(shop ? { shop } : {}), ...(sort !== "recommend" ? { sort } : {}), ...extra });
      const counts = Object.keys(SHOPS).map((k) => [k, P.filter((p) => p.shop === k).length]);
      const chip = shop ? `<div class="${cls("Condition_list")}">絞り込み条件：<a class="${cls("ConditionChip_chip")} ${cls("ConditionChip_active")}" href="${"search.html?" + new URLSearchParams({ q })}">ショップ：${SHOPS[shop]} ×</a></div>` : "";
      const pager = Array.from({ length: pagesN }, (_, i) => i + 1).map((n) => n === page ? `<span class="${cls("Pager_current")}">${n}</span>` : `<a href="${link({ page: n })}">${n}</a>`).join("") +
        (page < pagesN ? `<a class="${cls("Pager_next")}" href="${link({ page: page + 1 })}">次へ ›</a>` : "");
      mount(`<ol class="${cls("Breadcrumb_list")}"><li><a href="index.html">ホーム</a></li><li>「${q}」の検索結果</li></ol>
        <div class="${cls("Search_layout")}">
          <aside class="${cls("Filter_side")}">
            <section><h4>ショップで絞り込み</h4><ul>${counts.map(([k, n]) => `<li><a href="${"search.html?" + new URLSearchParams({ q, shop: k })}">${SHOPS[k]}</a> <span>(${n})</span></li>`).join("")}</ul></section>
            <section><h4>価格帯で絞り込み</h4><ul><li><a href="#">〜3,000円</a></li><li><a href="#">3,000〜10,000円</a></li><li><a href="#">10,000円〜</a></li></ul></section>
          </aside>
          <div class="${cls("Search_main")}">
            <div class="${cls("Search_head")}"><h1>「${q}」の検索結果 <span>${items.length}件</span></h1>
              <label>並び替え <select name="sort" aria-label="並び替え">
                ${[["recommend", "おすすめ順"], ["price_asc", "価格の安い順"], ["price_desc", "価格の高い順"], ["reviews", "レビュー件数順"]].map(([v, t]) => `<option value="${v}" ${v === sort ? "selected" : ""}>${t}</option>`).join("")}
              </select></label></div>
            ${chip}
            <ul class="${cls("ItemList_list")}">${shown.map(card).join("")}</ul>
            <nav class="${cls("Pager_pager")}" aria-label="ページ送り">${pager}</nav>
          </div>
        </div>`);
      $("select[name=sort]").addEventListener("change", (e) => { location.href = link({ sort: e.target.value }); });
      setTimeout(() => document.querySelectorAll("[data-price]").forEach((el) => { el.textContent = yen(Number(el.dataset.price)) + "円(税込)"; }), 700);
    },

    item() {
      const p = P.find((x) => x.id === Number(qs.get("id"))) || P[0];
      const review = p.reviews ? `<div class="${cls("Review_review")}"><span class="${cls("Review_stars")}" aria-label="評価${p.rating}">${stars(p.rating)}</span>
        <span class="${cls("Review_score")}">${p.rating.toFixed(1)}</span><a class="${cls("Review_count")}" href="#reviews">(${p.reviews}件)</a></div>` : "";
      const suggested = p.list ? `<div class="${cls("SuggestedPrice_suggested")}">メーカー希望小売価格 <s>${yen(p.list)}円</s>
        <span class="${cls("OffBadge_badge")}">${Math.round((1 - p.price / p.list) * 100)}%OFF</span></div>` : "";
      const memberRow = member() ? `<div class="${cls("MemberPrice_member")}">会員価格 <strong>${yen(p.member)}</strong>円(税込)</div>`
        : `<div class="${cls("MemberPrice_guest")}">ログインすると会員価格が表示されます</div>`;
      const stock = p.soldout ? `<span class="${cls("SoldOut_badge")}">売り切れ</span>` : `<span class="${cls("Stock_status")}">在庫あり</span>`;
      const pointSlot = `<div data-slot="point"></div>`;
      const spec = [["メーカー", p.maker], ["型番", p.model], ["JANコード", p.jan], ["発売日", p.release], ["保証期間", "メーカー保証 1年"], ["接続方式", "Bluetooth 5.3"]];
      const recommend = P.filter((x) => x.id !== p.id).slice(0, 8);
      mount(`<ol class="${cls("Breadcrumb_list")}"><li><a href="index.html">ホーム</a></li><li><a href="search.html?q=${encodeURIComponent("イヤホン")}">イヤホン・ヘッドホン</a></li><li>${p.name}</li></ol>
        <div class="${cls("ItemDetail_layout")}">
          <div class="${cls("Gallery_main")}">${thumb(p, 420)}</div>
          <div class="${cls("ItemDetail_info")}">
            <a class="${cls("ShopName_link")}" href="search.html?q=&shop=${p.shop}">${SHOPS[p.shop]}</a>
            <h1 class="${cls("ItemTitle_itemTitle")}">${p.name}</h1>
            ${review}${suggested}
            <div class="${cls("Price_price")}"><strong class="${cls("Price_current")}">----</strong><span>円(税込)</span></div>
            ${REV === 1 ? pointSlot : ""}
            ${memberRow}
            <div class="${cls("Stock_row")}">在庫状況：${stock}</div>
            ${REV === 1 ? "" : pointSlot}
            <button type="button" class="${cls("Cart_button")}" ${p.soldout ? "disabled" : ""}>${p.soldout ? "入荷待ち" : "カートに入れる"}</button>
            <p><a class="${cls("StoreStock_link")}" href="store.html?id=${p.id}">店舗在庫を確認する</a></p>
            <div class="${cls("Delivery_box")}"><p>お届け予定：ご注文から2〜4日</p><p>3,980円以上のご注文で送料無料</p></div>
          </div>
        </div>
        <section class="${cls("Spec_section")}"><h2>商品仕様</h2><table class="${cls("SpecTable_table")}">
          ${spec.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</table></section>
        <section class="${cls("Recommend_section")}"><h2>このショップのおすすめ商品</h2><ul>
          ${recommend.map((x) => `<li><a href="item.html?id=${x.id}">${thumb(x, 150)}<span>${x.name}</span></a><b>${yen(x.price)}円</b></li>`).join("")}</ul></section>
        <section class="${cls("Description_section")}" id="description"><h2>商品説明</h2><div class="${cls("Description_body")}">読み込み中…</div></section>`);
      setTimeout(() => { $(`[class^="Price_current"]`).textContent = yen(p.price); }, 900);
      setTimeout(() => {
        if (p.soldout) return;
        const name = REV === 1 ? "Point_point" : "Reward_reward";
        $("[data-slot=point]").outerHTML = `<div class="${cls(name)}">${yen(Math.floor(p.price * p.rate / 100))}ポイント（${p.rate}%）獲得</div>`;
      }, 1600);
      const desc = $(`[class^="Description_body"]`);
      new IntersectionObserver((entries, ob) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        desc.textContent = `${p.name}は、${p.maker}のオーディオ製品です。通勤・通学からリモート会議まで、毎日使いやすいように軽さと装着感にこだわりました。専用アプリでイコライザーを調整できます。`;
        ob.disconnect();
      }).observe(desc);
      let compact = false;
      addEventListener("scroll", () => {
        const next = scrollY > 160;
        if (next !== compact) { compact = next; document.body.classList.toggle("is-compact", compact); }
      });
    },

    ranking() {
      if (navigator.webdriver) { location.replace("captcha.html?return=ranking.html"); return; }
      const top = [...P].sort((a, b) => b.reviews - a.reviews).slice(0, 5);
      mount(`<h1 class="${cls("Ranking_title")}">ワイヤレスイヤホン 売れ筋ランキング</h1><ol class="${cls("Ranking_list")}">
        ${top.map((p, i) => `<li><span class="${cls("Ranking_rank")}">${i + 1}位</span>${thumb(p, 80)}<a href="item.html?id=${p.id}">${p.name}</a><b>${yen(p.price)}円</b></li>`).join("")}</ol>`);
    },

    captcha() {
      mount(`<div class="${cls("Challenge_box")}"><h1>アクセスを確認しています</h1>
        <p>短時間に多数のアクセスがあったため、ロボットによるアクセスではないことを確認しています。</p>
        <label class="${cls("Challenge_check")}"><input type="checkbox"> 私はロボットではありません</label>
        <p class="${cls("Challenge_note")}">確認が完了するとページが表示されます。</p></div>`);
      $("input[type=checkbox]").addEventListener("change", (e) => {
        e.target.checked = false;
        $(`[class^="Challenge_note"]`).textContent = "確認できませんでした。時間をおいて、通常のブラウザからアクセスしてください。";
      });
    },

    login() {
      mount(`<div class="${cls("Login_box")}"><h1>ログイン</h1>
        <form><label>会員ID（メールアドレス）<input type="text" name="member_id" autocomplete="username"></label>
        <label>パスワード<input type="password" name="password" autocomplete="current-password"></label>
        <button type="submit">ログイン</button></form><p><a href="#">パスワードを忘れた方</a> ／ <a href="#">新規会員登録</a></p></div>`);
      $("form").addEventListener("submit", (e) => {
        e.preventDefault();
        if (!e.target.member_id.value || !e.target.password.value) return;
        sessionStorage.setItem("zb-member", "1");
        location.href = qs.get("return") || "index.html";
      });
    },

    store() {
      const p = P.find((x) => x.id === Number(qs.get("id"))) || P[0];
      mount(`<h1 class="${cls("Store_title")}">店舗在庫</h1><p class="${cls("Store_item")}">${p.name}</p>
        <iframe class="${cls("Store_frame")}" src="stock.html?id=${p.id}" title="店舗ごとの在庫" width="760" height="260"></iframe>
        <p>※在庫は1時間ごとに更新されます。</p>`);
    },

    stock() {
      const p = P.find((x) => x.id === Number(qs.get("id"))) || P[0];
      const rows = [["渋谷店", p.soldout ? 0 : 5], ["新宿店", p.soldout ? 0 : 1], ["池袋店", 0]];
      document.body.innerHTML = `<table class="${cls("StockTable_table")}"><thead><tr><th>店舗</th><th>在庫</th><th>更新</th></tr></thead><tbody>
        ${rows.map(([s, n]) => `<tr><td>${s}</td><td class="${cls("StockTable_count")}">${n ? (n > 2 ? `在庫あり（${n}点）` : `残りわずか（${n}点）`) : "在庫なし"}</td><td>10:00</td></tr>`).join("")}
        </tbody></table>`;
    },
  };

  document.addEventListener("DOMContentLoaded", () => pages[document.body.dataset.page]());
})();
