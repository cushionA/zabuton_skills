import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from time import monotonic

from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BEFORE_ACTIONS = {"fill", "type", "select", "check", "uncheck", "hover", "focus"}

AREA_IMAGE_JS = """el => {
  const map = el.closest('map');
  return map && [...document.querySelectorAll('img[usemap]')].find(
    img => img.getAttribute('usemap').split('#').pop() === (map.name || map.id));
}"""

RECT_JS = """(el, fit) => {
  let reference = el;
  let rects = [];
  if (el.tagName === 'AREA') {
    const map = el.closest('map');
    reference = map && [...document.querySelectorAll('img[usemap]')].find(
      img => img.getAttribute('usemap').split('#').pop() === (map.name || map.id));
    if (!reference) throw new Error('area に対応する img[usemap] がありません');
    const r = reference.getBoundingClientRect();
    const cs = getComputedStyle(reference);
    const sx = r.width / reference.offsetWidth, sy = r.height / reference.offsetHeight;
    const left = r.left + (reference.clientLeft + parseFloat(cs.paddingLeft)) * sx;
    const top = r.top + (reference.clientTop + parseFloat(cs.paddingTop)) * sy;
    const coords = el.coords.trim().split(/[\\s,]+/).map(Number);
    const shape = (el.shape || 'rect').toLowerCase();
    let x1, y1, x2, y2;
    if (shape === 'default') {
      x1 = y1 = 0;
      x2 = reference.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      y2 = reference.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    } else if (shape === 'rect' && coords.length === 4) {
      x1 = Math.min(coords[0], coords[2]); y1 = Math.min(coords[1], coords[3]);
      x2 = Math.max(coords[0], coords[2]); y2 = Math.max(coords[1], coords[3]);
    } else if (shape === 'circle' && coords.length === 3) {
      x1 = coords[0] - coords[2]; y1 = coords[1] - coords[2];
      x2 = coords[0] + coords[2]; y2 = coords[1] + coords[2];
    } else if (shape === 'poly' && coords.length >= 6 && coords.length % 2 === 0) {
      const xs = coords.filter((_, i) => i % 2 === 0), ys = coords.filter((_, i) => i % 2 === 1);
      x1 = Math.min(...xs); y1 = Math.min(...ys); x2 = Math.max(...xs); y2 = Math.max(...ys);
    } else throw new Error('area の shape / coords が不正です');
    if (!coords.every(Number.isFinite)) throw new Error('area の coords が不正です');
    rects = [{left: left + x1 * sx, top: top + y1 * sy,
              right: left + x2 * sx, bottom: top + y2 * sy}];
  } else if (fit === 'text') {
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
  const r = reference.getBoundingClientRect();
  return {rect: [x1, y1, x2 - x1, y2 - y1], element: [r.left, r.top, r.width, r.height]};
}"""


def locate(page, target):
    if isinstance(target, str):
        return page.locator(target)
    t = dict(target)
    frames = t.pop("frame", [])
    for selector in frames if isinstance(frames, list) else [frames]:
        page = page.frame_locator(selector)
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
            page.set_default_timeout(timeout)
            page.wait_for_load_state("domcontentloaded")
        settle(page, settle_ms)
    return page


def measure(page, target, timeout, fit):
    targets = target if isinstance(target, list) else [target]
    rects = []
    deadline = monotonic() + timeout / 1000

    def remaining():
        return max(1, int((deadline - monotonic()) * 1000))

    for t in targets:
        loc = locate(page, t).first
        loc.wait_for(state="attached", timeout=remaining())
        is_area = loc.evaluate("el => el.tagName === 'AREA'", timeout=remaining())
        handle = None
        try:
            if is_area:
                handle = loc.evaluate_handle(AREA_IMAGE_JS, timeout=remaining())
                reference = handle.as_element()
                if reference is None:
                    raise PlaywrightError("area に対応する img[usemap] がありません")
                reference.wait_for_element_state("visible", timeout=remaining())
            else:
                loc.wait_for(state="visible", timeout=remaining())
                reference = loc
            geometry = loc.evaluate(RECT_JS, fit, timeout=remaining())
            box = reference.bounding_box() if is_area else reference.bounding_box(timeout=remaining())
            if box is None or not geometry["element"][2] or not geometry["element"][3]:
                raise PlaywrightError(f"target の表示座標を測定できません: {t}")
            r, element = geometry["rect"], geometry["element"]
            sx, sy = box["width"] / element[2], box["height"] / element[3]
            scroll_x, scroll_y = page.evaluate("() => [window.scrollX, window.scrollY]")
            rects.append([box["x"] + (r[0] - element[0]) * sx + scroll_x,
                          box["y"] + (r[1] - element[1]) * sy + scroll_y, r[2] * sx, r[3] * sy])
        finally:
            if handle is not None:
                handle.dispose()
    x1 = min(r[0] for r in rects)
    y1 = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    return [x1, y1, x2 - x1, y2 - y1]


def stable(page, fn, timeout):
    # 再描画で一時的に消える要素も期限内で再測定する。操作やページアクセスは再実行しない。
    deadline = monotonic() + timeout / 1000
    prev, matches, last_error = None, 0, None
    while monotonic() < deadline:
        try:
            rects = fn(max(1, int((deadline - monotonic()) * 1000)))
            cur = [[round(v, 2) for v in r] for r in rects]
            matches = matches + 1 if cur == prev else 1
            prev = cur
            last_error = None
            if matches >= 3 and monotonic() < deadline:
                return rects
        except PlaywrightError as error:
            prev, matches, last_error = None, 0, error
        remaining = int((deadline - monotonic()) * 1000)
        if remaining > 0:
            page.wait_for_timeout(min(250, remaining))
    raise PlaywrightError(f"対象要素の座標が {timeout}ms 以内に安定しませんでした") from last_error


def image_size(path):
    with Image.open(path) as image:
        return list(image.size)


def measure_steps(page, steps, timeout):
    deadline = monotonic() + timeout / 1000
    return [measure(page, s["target"], max(1, int((deadline - monotonic()) * 1000)),
                    s.get("fit") or ("text" if s.get("kind") == "data" else "box")) for s in steps]


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
        timeout = peek.get("timeout_ms", timeout)
        page.set_default_timeout(timeout)
        page.goto(peek["goto"], wait_until="domcontentloaded", timeout=60000)
        settle(page, peek.get("wait_ms", settle_ms))
        if peek.get("wait_for"):
            locate(page, peek["wait_for"]).first.wait_for(state="visible", timeout=timeout)
        rect = stable(page, lambda ms: [measure(page, peek["target"], ms, "box")], timeout)[0]
        load_lazy_content(page, [rect], vh)
        targets = [peek["target"]] + ([peek["mark"]] if peek.get("mark") else [])
        measured = stable(page, lambda ms: measure_steps(page, [{"target": t} for t in targets], ms), timeout)
        rect = measured[0]
        pad = peek.get("pad", 8)
        x, y = max(0, rect[0] - pad), max(0, rect[1] - pad)
        clip = [x, y, rect[0] + rect[2] + pad - x, min(rect[1] + rect[3] + pad, doc_height(page)) - y]
        page.screenshot(path=str(path), clip={"x": clip[0], "y": clip[1], "width": clip[2], "height": clip[3]},
                        full_page=True, animations="disabled")
        peek["image"] = path.name
        peek["image_size"] = image_size(path)
        peek["scale"] = dsf
        peek["captured_url"] = page.url
        if peek.get("mark"):
            m = measured[1]
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
    default_timeout = browser_cfg.get("timeout_ms", 30000)
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
        ctx.set_default_timeout(default_timeout)
        page = ctx.new_page()

        for index, shot in enumerate(deck["shots"], 1):
            shot_id = shot.get("id", f"shot{index}")
            if shot.get("image") or index - 1 not in targets:
                continue
            try:
                timeout = shot.get("timeout_ms", default_timeout)
                page.set_default_timeout(timeout)
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
                        rects = stable(page, lambda ms: measure_steps(page, framed, ms), timeout)
                        load_lazy_content(page, rects, vh)
                        rects = stable(page, lambda ms: measure_steps(page, framed, ms), timeout)
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
                    shot["image_size"] = image_size(out_dir / image_name)
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
    # Windowsのパイプ出力は既定でcp932になり、進捗やエラーの日本語が文字化けするため
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
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
