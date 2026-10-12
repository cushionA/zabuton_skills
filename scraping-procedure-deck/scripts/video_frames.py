import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

THUMB = (96, 54)


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    # Playwright 同梱の ffmpeg は webm しか読めない
    roots = [Path.home() / "AppData/Local/ms-playwright", Path.home() / ".cache/ms-playwright",
             Path.home() / "Library/Caches/ms-playwright"]
    for root in roots:
        for p in sorted(root.glob("ffmpeg-*/ffmpeg-*"), reverse=True):
            if p.is_file() and p.suffix in ("", ".exe"):
                return str(p)
    return None


def run_ffmpeg(ffmpeg, args):
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg が失敗しました: {r.stderr.strip()[-400:]}\n"
                 "mp4 などを読むには、PATH 上の ffmpeg か `pip install imageio-ffmpeg` が必要です")


def font(size):
    for name in ("meiryo.ttc", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def thumb_of(path):
    return Image.open(path).convert("L").resize(THUMB)


def diff(a, b):
    pa, pb = a.getdata(), b.getdata()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / (len(pa) * 255)


def pick_keyframes(samples, change, settle, limit):
    # 画面が大きく変わるたびに区間を区切り、一定時間続いた区間の最後のフレームを代表にする
    thumbs = [thumb_of(p) for p in samples]
    segments, start = [], 0
    for i in range(1, len(thumbs)):
        if diff(thumbs[i - 1], thumbs[i]) >= change:
            segments.append((start, i - 1))
            start = i
    segments.append((start, len(thumbs) - 1))
    picks = [end for first, end in segments if end - first + 1 >= settle]
    if not picks:
        picks = [len(thumbs) - 1]
    if len(picks) > limit:
        step = len(picks) / limit
        picks = [picks[int(n * step)] for n in range(limit)]
    return picks


def contact_sheet(items, out, cols=4, width=480):
    f = font(18)
    tiles = []
    for label, path in items:
        im = Image.open(path).convert("RGB")
        im = im.resize((width, int(im.height * width / im.width)))
        dr = ImageDraw.Draw(im)
        dr.rectangle([0, 0, 150, 26], fill=(0, 0, 0))
        dr.text((6, 2), label, fill=(255, 255, 255), font=f)
        tiles.append(im)
    th = max(t.height for t in tiles)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (width + 6) + 6, rows * (th + 6) + 6), (60, 60, 60))
    for n, t in enumerate(tiles):
        sheet.paste(t, (6 + (n % cols) * (width + 6), 6 + (n // cols) * (th + 6)))
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description="操作録画から代表フレームと一覧画像を作る")
    ap.add_argument("video")
    ap.add_argument("--out", required=True, help="出力ディレクトリ（新規）")
    ap.add_argument("--fps", type=float, default=2.0, help="変化を調べる間隔（既定 2 回/秒）")
    ap.add_argument("--change", type=float, default=0.02, help="画面が変わったと見なす差の割合")
    ap.add_argument("--settle", type=int, default=2, help="代表フレームにする最短の継続サンプル数（遷移中の画面を除く）")
    ap.add_argument("--max-frames", type=int, default=30)
    ap.add_argument("--at", help="秒をカンマ区切りで指定すると、その時刻のフレームだけを取り出す")
    args = ap.parse_args()

    video = Path(args.video)
    out = Path(args.out)
    if not video.is_file():
        sys.exit(f"動画が見つかりません: {video}")
    if out.exists():
        sys.exit(f"出力先が既にあります: {out}")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        sys.exit("ffmpeg が見つかりません。ffmpeg を PATH に入れるか `pip install imageio-ffmpeg` を実行してください")
    out.mkdir(parents=True)

    if args.at:
        items = []
        for sec in (float(s) for s in args.at.split(",")):
            dest = out / f"at_{sec:07.2f}s.png"
            run_ffmpeg(ffmpeg, ["-ss", str(sec), "-i", str(video), "-frames:v", "1", str(dest)])
            items.append((f"{sec:.1f}s", dest))
        print("\n".join(str(d) for _, d in items))
        return

    with tempfile.TemporaryDirectory() as tmp:
        run_ffmpeg(ffmpeg, ["-i", str(video), "-r", str(args.fps), str(Path(tmp) / "s_%05d.png")])
        samples = sorted(Path(tmp).glob("s_*.png"))
        if not samples:
            sys.exit("フレームを取り出せませんでした")
        picks = pick_keyframes(samples, args.change, args.settle, args.max_frames)
        frames = []
        for n, idx in enumerate(picks, 1):
            sec = idx / args.fps
            dest = out / f"{n:02d}_{sec:07.2f}s.png"
            shutil.copy(samples[idx], dest)
            frames.append({"no": n, "time_sec": round(sec, 2), "file": dest.name})
        im = Image.open(out / frames[0]["file"])
        contact_sheet([(f"{f['no']:02d}  {f['time_sec']:.1f}s", out / f["file"]) for f in frames], out / "contact_sheet.png")
        meta = {"video": video.name, "size": list(im.size), "sample_fps": args.fps, "frames": frames}
        (out / "frames.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(frames)} フレーム -> {out}")
    print(out / "contact_sheet.png")


if __name__ == "__main__":
    main()
