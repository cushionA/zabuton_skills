import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(size):
    for name in ("meiryob.ttc", "meiryo.ttc", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def grid(image, out, step):
    im = Image.open(image).convert("RGB")
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)
    f = font(max(12, step // 6))
    for x in range(0, im.width, step):
        major = x % (step * 5) == 0
        dr.line([(x, 0), (x, im.height)], fill=(0, 120, 255, 150 if major else 70), width=2 if major else 1)
        dr.text((x + 3, 3), str(x), fill=(0, 90, 220, 255), font=f)
    for y in range(0, im.height, step):
        major = y % (step * 5) == 0
        dr.line([(0, y), (im.width, y)], fill=(0, 120, 255, 150 if major else 70), width=2 if major else 1)
        dr.text((3, y + 3), str(y), fill=(0, 90, 220, 255), font=f)
    Image.alpha_composite(im.convert("RGBA"), over).convert("RGB").save(out)
    print(out)


def boxes(deck_path, out_dir):
    deck = json.loads(deck_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    op_no = 0
    for i, shot in enumerate(deck["shots"], 1):
        steps = shot.get("steps", [])
        marks = 0
        for st in steps:
            if st["kind"] == "op":
                op_no += 1
                st["_no"] = st.get("no", op_no)
            elif st["kind"] == "mark":
                marks += 1
                st["_no"] = f"※{marks}"
            else:
                st["_no"] = st["item"]
        if not shot.get("image"):
            continue
        im = Image.open(deck_path.parent / shot["image"]).convert("RGB")
        dr = ImageDraw.Draw(im)
        w = max(3, im.width // 500)
        f = font(max(16, im.width // 60))
        for st in steps:
            if not st.get("box"):
                continue
            x, y, bw, bh = st["box"]
            dr.rectangle([x, y, x + bw, y + bh], outline=(255, 0, 0), width=w)
            label = ("No." if st["kind"] == "data" else "") + str(st["_no"])
            tx, ty = x, max(0, y - f.size - 6)
            dr.rectangle(dr.textbbox((tx, ty), label, font=f), fill=(255, 255, 255))
            dr.text((tx, ty), label, fill=(255, 0, 0), font=f)
        path = out_dir / f"{i:02d}_{shot.get('id', i)}_boxes.png"
        im.save(path)
        print(path)


def main():
    # Windowsのパイプ出力は既定でcp932になり、日本語のファイル名が文字化けするため
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="赤枠座標の確認用画像を作る（PPTXには使わない）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grid", help="座標を読み取るための目盛り付き画像を作る（手持ち画像の枠座標を決めるとき）")
    g.add_argument("image", type=Path)
    g.add_argument("-o", "--out", type=Path)
    g.add_argument("--step", type=int, default=50)
    b = sub.add_parser("boxes", help="scenario/deck の box をそのまま描いて確認する")
    b.add_argument("deck", type=Path)
    b.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()
    if args.cmd == "grid":
        grid(args.image, args.out or args.image.with_name(args.image.stem + "_grid.png"), args.step)
    else:
        boxes(args.deck, args.out or args.deck.parent / "overlay")


if __name__ == "__main__":
    main()
