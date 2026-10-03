import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BEFORE_ACTIONS = {"fill", "type", "select", "check", "uncheck", "hover", "focus"}

RECT_JS = """(el, fit) => {
  let rects = [];
  if (fit === 'text') {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!n.textContent.trim()) continue;
      const cs = n.parentElement && getComputedStyle(n.parentElement);
      if (cs && (cs.visibility === 'hidden' || cs.display === 'none')) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) rects.push(r);
    }
  }
  if (!rects.length) rects = [el.getBoundingClientRect()];
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
  for (const r of rects) {
    x1 = Math.min(x1, r.left); y1 = Math.min(y1, r.top);
    x2 = Math.max(x2, r.right); y2 = Math.max(y2, r.bottom);
  }
  return [x1 + window.scrollX, y1 + window.scrollY, x2 - x1, y2 - y1];
}"""


def locate(page, target):
    if isinstance(target, str):
        return page.locator(target)
    t = dict(target)
    base = locate(page, t.pop("within")) if "within" in t else page
    exact = t.get("exact")
    if "role" in t:
        loc = base.get_by_role(t["role"], name=t.get("name"), exact=exact)
    elif "text" in t:
        loc = base.get_by_text(t["text"], exact=exact)
    elif "label" in t:
        loc = base.get_by_label(t["label"], exact=exact)
    elif "placeholder" in t:
        loc = base.get_by_placeholder(t["placeholder"], exact=exact)
    elif "alt" in t:
        loc = base.get_by_alt_text(t["alt"], exact=exact)
    elif "title" in t:
        loc = base.get_by_title(t["title"], exact=exact)
    elif "test_id" in t:
        loc = base.get_by_test_id(t["test_id"])
    elif "css" in t:
        loc = base.locator(t["css"])
    else:
        raise SystemExit(f"target の書き方が不正です: {target}")
    if "has_text" in t:
        loc = loc.filter(has_text=t["has_text"])
    if "nth" in t:
        loc = loc.nth(t["nth"])
    return loc


def norm_action(action):
    if action is None:
        return None
    if isinstance(action, str):
        return {"type": action}
    if "type" in action and len(action) > 1:
        return dict(action)
    (kind, value), = action.items()
    return {"type": kind, "value": value}


def timing_of(step, action):
    return step.get("timing") or ("before" if action["type"] in BEFORE_ACTIONS else "after")


def settle(page, ms):
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(ms)


def perform(page, ctx, action, target, timeout, settle_ms):
    kind = action["type"]
    value = action.get("value")
    pages_before = len(ctx.pages)
    if kind == "wait":
        page.wait_for_timeout(int(value or 1000))
        return page
    if kind == "goto":
        page.goto(value, wait_until="domcontentloaded", timeout=60000)
        settle(page, settle_ms)
        return page
    if kind == "press" and not target:
        page.keyboard.press(value)
        settle(page, settle_ms)
        return page
    if kind == "hide":
        locate(page, target).evaluate_all(
            "els => els.forEach(e => e.style.setProperty('visibility', 'hidden', 'important'))")
        return page
    loc = locate(page, target).first
    loc.wait_for(state="visible", timeout=timeout)
    if kind == "click":
        loc.click(timeout=timeout)
    elif kind == "fill":
        loc.fill(str(value), timeout=timeout)
    elif kind == "type":
        loc.press_sequentially(str(value), delay=60)
    elif kind == "press":
        loc.press(value)
    elif kind == "select":
        loc.select_option(value)
    elif kind == "check":
        loc.check()
    elif kind == "uncheck":
        loc.uncheck()
    elif kind == "hover":
        loc.hover()
    elif kind == "focus":
        loc.focus()
    elif kind == "scroll":
        loc.scroll_into_view_if_needed()
    else:
        raise SystemExit(f"未対応の action です: {kind}")
    if kind in ("click", "press"):
        page.wait_for_timeout(800)
        if len(ctx.pages) > pages_before:
            page = ctx.pages[-1]
            page.wait_for_load_state("domcontentloaded")
        settle(page, settle_ms)
    return page


def measure(page, target, timeout, fit):
    targets = target if isinstance(target, list) else [target]
    rects = []
    for t in targets:
        loc = locate(page, t).first
        loc.wait_for(state="visible", timeout=timeout)
        rects.append(loc.evaluate(RECT_JS, fit))
    x1 = min(r[0] for r in rects)
    y1 = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def stable(page, fn, tries=6):
    # 非同期で読み込まれる値（ポイント等）で位置がずれるため、座標が落ち着くまで測り直す
    prev = [[round(v) for v in r] for r in fn()]
    for _ in range(tries):
        page.wait_for_timeout(400)
        cur = [[round(v) for v in r] for r in fn()]
        if cur == prev:
            break
        prev = cur
    return prev


def measure_steps(page, steps, timeout):
    return [measure(page, s["target"], timeout, s.get("fit") or ("text" if s.get("kind") == "data" else "box"))
            for s in steps]


def capture_region(rects, vw, vh, doc_h):
    top = min(r[1] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    y1 = max(0, top - vh * 0.6)
    y2 = min(doc_h, bottom + vh * 0.6)
    if y2 - y1 < vh:
        y2 = min(doc_h, y1 + vh)
        y1 = max(0, y2 - vh)
    return [0, int(y1), vw, int(y2 - y1)]


def load_lazy_content(page, rects, vh):
    top = min(r[1] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    if bottom <= vh:
        return
    y = max(0, top - vh * 0.6)
    while y < bottom + vh * 0.6:
        page.evaluate("y => window.scrollTo(0, y)", y)
        page.wait_for_timeout(300)
        y += vh * 0.7
    # 固定ヘッダーが切り出し範囲の途中に描画されないよう、撮影は必ず最上部スクロール状態で行う
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(700)


def doc_height(page):
    return page.evaluate("() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")


def capture_peek(ctx, peek, path, dsf, vh, timeout, settle_ms):
    page = ctx.new_page()
    try:
        page.goto(peek["goto"], wait_until="domcontentloaded", timeout=60000)
        settle(page, peek.get("wait_ms", settle_ms))
        rect = measure(page, peek["target"], timeout, "box")
        load_lazy_content(page, [rect], vh)
        rect = stable(page, lambda: [measure(page, peek["target"], timeout, "box")])[0]
        pad = peek.get("pad", 8)
        x, y = max(0, rect[0] - pad), max(0, rect[1] - pad)
        clip = [x, y, rect[0] + rect[2] + pad - x, min(rect[1] + rect[3] + pad, doc_height(page)) - y]
        page.screenshot(path=str(path), clip={"x": clip[0], "y": clip[1], "width": clip[2], "height": clip[3]},
                        full_page=True, animations="disabled")
        peek["image"] = path.name
        peek["image_size"] = [int(clip[2] * dsf), int(clip[3] * dsf)]
        peek["scale"] = dsf
        peek["captured_url"] = page.url
        if peek.get("mark"):
            m = measure(page, peek["mark"], timeout, "box")
            peek["mark_box"] = [round((m[0] - clip[0]) * dsf, 1), round((m[1] - clip[1]) * dsf, 1),
                                round(m[2] * dsf, 1), round(m[3] * dsf, 1)]
    finally:
        page.close()


def shots_to_run(shots, only):
    if not only:
        return set(range(len(shots)))
    ids = {s.get("id") for s in shots}
    missing = only - ids
    if missing:
        raise SystemExit(f"scenario に無い shot id です: {', '.join(sorted(missing))}")
    run = set()
    for i, s in enumerate(shots):
        if s.get("id") not in only:
            continue
        j = i
        # goto の無い画面は前の画面からの操作で辿り着くので、goto のある画面まで遡って実行する
        while j > 0 and not shots[j].get("goto") and not shots[j].get("image"):
            j -= 1
        run.update(range(j, i + 1))
    return run


def carry_over(deck, previous, only):
    by_id = {s.get("id"): s for s in previous.get("shots", [])}
    for index, shot in enumerate(deck["shots"]):
        old = by_id.get(shot.get("id"))
        if not old or shot.get("image") or shot.get("id") in only:
            continue
        deck["shots"][index] = old


def run(scenario, out_dir, headed, only):
    browser_cfg = scenario.get("browser", {})
    vw, vh = browser_cfg.get("viewport", [1280, 800])
    dsf = browser_cfg.get("device_scale_factor", 2)
    timeout = browser_cfg.get("timeout_ms", 15000)
    settle_ms = browser_cfg.get("settle_ms", 1500)
    out_dir.mkdir(parents=True, exist_ok=True)
    deck = json.loads(json.dumps(scenario))
    deck_path = out_dir / "deck.json"
    targets = shots_to_run(deck["shots"], only)
    previous = json.loads(deck_path.read_text(encoding="utf-8")) if only and deck_path.exists() else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        user_agent = browser_cfg.get("user_agent")
        if not user_agent:
            probe = browser.new_page()
            # ヘッドレス特有のUAだとAPI応答を変えるサイトがある（例: ポイント表示が出ない）ため通常のChrome表記に揃える
            user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome/", "Chrome/")
            probe.close()
        ctx = browser.new_context(
            viewport={"width": vw, "height": vh},
            device_scale_factor=dsf,
            locale=browser_cfg.get("locale", "ja-JP"),
            timezone_id=browser_cfg.get("timezone", "Asia/Tokyo"),
            user_agent=user_agent,
        )
        page = ctx.new_page()

        for index, shot in enumerate(deck["shots"], 1):
            shot_id = shot.get("id", f"shot{index}")
            if shot.get("image") or index - 1 not in targets:
                continue
            try:
                if shot.get("goto"):
                    page.goto(shot["goto"], wait_until="domcontentloaded", timeout=60000)
                settle(page, shot.get("wait_ms", settle_ms))
                for item in shot.get("setup", []):
                    page = perform(page, ctx, norm_action(item["action"]), item.get("target"), timeout, settle_ms)
                if shot.get("wait_for"):
                    locate(page, shot["wait_for"]).first.wait_for(state="visible", timeout=timeout)

                steps = shot.get("steps", [])
                prepared = False
                for step in steps:
                    action = norm_action(step.get("action"))
                    if action and timing_of(step, action) == "before":
                        page = perform(page, ctx, action, step.get("target"), timeout, settle_ms)
                        prepared = True
                if prepared and shot.get("blur", True):
                    # 入力直後のサジェスト等が画面を覆うので、フォーカスを外して閉じてから撮る
                    page.evaluate("() => document.activeElement && document.activeElement.blur()")
                    page.wait_for_timeout(600)

                if not only or shot_id in only:
                    framed = [s for s in steps if s.get("target")]
                    if framed:
                        rects = measure_steps(page, framed, timeout)
                        load_lazy_content(page, rects, vh)
                        rects = stable(page, lambda: measure_steps(page, framed, timeout))
                        clip = capture_region(rects, vw, vh, doc_height(page))
                    else:
                        rects = []
                        clip = [0, 0, vw, vh]

                    image_name = f"{index:02d}_{shot_id}.png"
                    page.screenshot(
                        path=str(out_dir / image_name),
                        clip={"x": clip[0], "y": clip[1], "width": clip[2], "height": clip[3]},
                        full_page=True,
                        animations="disabled",
                    )
                    for step, r in zip(framed, rects):
                        step["box"] = [round((r[0] - clip[0]) * dsf, 1), round((r[1] - clip[1]) * dsf, 1),
                                       round(r[2] * dsf, 1), round(r[3] * dsf, 1)]
                    shot["image"] = image_name
                    shot["image_size"] = [int(clip[2] * dsf), int(clip[3] * dsf)]
                    shot["scale"] = dsf
                    shot["page_top"] = clip[1] == 0
                    shot["captured_url"] = page.url
                    shot["captured_at"] = datetime.now().isoformat(timespec="seconds")
                    print(f"[{index:02d}] {shot_id}: {image_name}  枠{len(framed)}件  {page.url}")

                    for n, peek in enumerate(shot.get("peeks", []), 1):
                        if peek.get("goto") and peek.get("target"):
                            path = out_dir / f"{index:02d}_{shot_id}_peek{n}.png"
                            capture_peek(ctx, peek, path, dsf, vh, timeout, shot.get("wait_ms", settle_ms))
                            print(f"      差分{n}: {path.name}  {peek['captured_url']}")

                for step in steps:
                    action = norm_action(step.get("action"))
                    if action and timing_of(step, action) == "after":
                        page = perform(page, ctx, action, step.get("target"), timeout, settle_ms)
            except PlaywrightError as e:
                err = out_dir / f"_error_{shot_id}.png"
                try:
                    page.screenshot(path=str(err))
                except PlaywrightError:
                    err = None
                print(f"[{index:02d}] {shot_id}: 失敗 ({page.url})\n{e}", file=sys.stderr)
                if err:
                    print(f"失敗時の画面: {err}", file=sys.stderr)
                browser.close()
                return 1
        browser.close()

    if previous is not None:
        carry_over(deck, previous, only)
    deck_path.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"deck: {deck_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="シナリオJSONに従ってブラウザを操作し、画面キャプチャと赤枠座標(deck.json)を出力する")
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--out", type=Path, required=True, help="キャプチャとdeck.jsonの出力先ディレクトリ")
    ap.add_argument("--only", help="指定した shot id だけ撮り直す（カンマ区切り）。前提の画面は自動で辿る")
    ap.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    args = ap.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    only = set(args.only.split(",")) if args.only else None
    sys.exit(run(scenario, args.out, args.headed, only))


if __name__ == "__main__":
    main()
