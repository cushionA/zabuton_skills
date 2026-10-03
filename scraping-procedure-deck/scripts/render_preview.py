import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PS_SCRIPT = r"""
param([string]$Pptx, [string]$OutDir, [int]$Width)
$ErrorActionPreference = 'Stop'
$wasRunning = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($Pptx, -1, 0, 0)
try {
  $height = [int]($Width * $pres.PageSetup.SlideHeight / $pres.PageSetup.SlideWidth)
  foreach ($s in $pres.Slides) {
    $s.Export((Join-Path $OutDir ('slide-{0:D2}.png' -f $s.SlideIndex)), 'PNG', $Width, $height)
  }
} finally {
  $pres.Close()
  if (-not $wasRunning -and $app.Presentations.Count -eq 0) { $app.Quit() }
}
"""


def with_powerpoint(pptx, out_dir, width):
    with tempfile.TemporaryDirectory() as tmp:
        ps1 = Path(tmp) / "export.ps1"
        ps1.write_text(PS_SCRIPT, encoding="utf-8-sig")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                            "-Pptx", str(pptx), "-OutDir", str(out_dir), "-Width", str(width)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr or r.stdout, file=sys.stderr)
        return r.returncode == 0


def with_libreoffice(pptx, out_dir, width):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    pdftoppm = shutil.which("pdftoppm")
    if not soffice or not pdftoppm:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(pptx)], check=True,
                       capture_output=True)
        pdf = Path(tmp) / (pptx.stem + ".pdf")
        subprocess.run([pdftoppm, "-png", "-scale-to-x", str(width), "-scale-to-y", "-1", str(pdf),
                        str(out_dir / "slide")], check=True)
    return True


def main():
    # Windowsのパイプ出力は既定でcp932になり、出力先パスやエラーの日本語が文字化けするため
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="PPTXを1スライド1枚のPNGに書き出す（目視QA用）")
    ap.add_argument("pptx", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=1600)
    args = ap.parse_args()
    pptx = args.pptx.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("slide-*.png"):
        old.unlink()
    ok = (sys.platform == "win32" and with_powerpoint(pptx, out, args.width)) or with_libreoffice(pptx, out, args.width)
    if not ok:
        sys.exit("PowerPoint（Windows）か LibreOffice + pdftoppm が必要です")
    for p in sorted(out.glob("slide-*.png")):
        print(p)


if __name__ == "__main__":
    main()
