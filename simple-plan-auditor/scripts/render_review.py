import argparse
import html
import re
import sys
from pathlib import Path
from string import Template
from urllib.parse import unquote


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
ITEM = re.compile(r"^\s*([-+*]|\d+[.)])\s+(.+)$")


def inline(text, anchors=()):
    parts = re.split(r"(`[^`]+`|\*\*[^*]+\*\*|\[[^\]\n]+\]\(#[^\s)]+\))", text)
    output = []
    for part in parts:
        link = re.fullmatch(r"\[([^\]\n]+)\]\(#([^\s)]+)\)", part)
        if link and unquote(link.group(2)) in anchors:
            output.append('<a href="#' + html.escape(unquote(link.group(2)), quote=True) + '">' + inline(link.group(1)) + "</a>")
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            output.append("<code>" + html.escape(part[1:-1]) + "</code>")
        elif part.startswith("**") and part.endswith("**") and len(part) > 4:
            output.append("<strong>" + html.escape(part[2:-2]) + "</strong>")
        else:
            output.append(html.escape(part))
    return "".join(output)


def cells(line):
    parts = [""]
    escaped = False
    for char in line.strip():
        if char == "|" and not escaped:
            parts.append("")
        else:
            parts[-1] += char
        escaped = char == "\\" and not escaped
    if line.strip().startswith("|"):
        parts.pop(0)
    if parts and not parts[-1]:
        parts.pop()
    return [re.sub(r"\\([\\|])", r"\1", part.strip()) for part in parts]


def headings(lines):
    result = {}
    used = set()
    fence = None
    for index, line in enumerate(lines):
        if fence:
            if re.fullmatch(r"\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = None
            continue
        opening = FENCE.match(line)
        if opening:
            fence = opening.group(1)
            continue
        heading = HEADING.match(line)
        if not heading:
            continue
        label = heading.group(2)
        base = re.sub(r"[^\w\- ]", "", label.lower()).strip().replace(" ", "-") or "section"
        anchor = base
        suffix = 1
        while anchor in used:
            anchor = base + "-" + str(suffix)
            suffix += 1
        used.add(anchor)
        result[index] = (len(heading.group(1)), label, anchor)
    return result


def is_table(lines, index):
    if index + 1 >= len(lines) or "|" not in lines[index]:
        return False
    header = cells(lines[index])
    separator = cells(lines[index + 1])
    return len(header) == len(separator) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)


def render_markdown(markdown):
    lines = markdown.splitlines()
    heading_map = headings(lines)
    anchors = {heading[2] for heading in heading_map.values()}
    output = []
    toc = []
    levels = []
    title = None
    in_overview = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            code = []
            index += 1
            while index < len(lines) and not re.fullmatch(r"\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*", lines[index]):
                code.append(lines[index])
                index += 1
            output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            index += 1
            continue
        if index in heading_map:
            level, label, anchor = heading_map[index]
            if title is None:
                title = label
            while levels and levels[-1] >= level:
                output.append("</div></details>")
                levels.pop()
            toc.append('<li><a href="#' + anchor + '">' + inline(label) + "</a></li>")
            # 標準書式のL0（## 0. 全体概要 の下の 0.1〜0.4）は確認の起点なので、深い見出しでも開いておく
            if level <= 2:
                in_overview = level == 2 and re.fullmatch(r"0\.?\s*全体概要", label) is not None
            opened = in_overview or level <= 2 and (level > 1 or len(toc) == 1)
            output.append('<details id="' + anchor + '"' + (" open" if opened else "") + '><summary>' + inline(label) + '</summary><div class="section-content">')
            levels.append(level)
            index += 1
            continue
        if is_table(lines, index):
            header = cells(line)
            output.append('<div class="table-scroll" role="region" aria-label="計画の表" tabindex="0"><table><thead><tr>' + "".join('<th scope="col">' + inline(cell, anchors) + "</th>" for cell in header) + "</tr></thead><tbody>")
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = cells(lines[index])
                output.append("<tr>" + "".join("<td>" + inline(cell, anchors) + "</td>" for cell in row) + "</tr>")
                index += 1
            output.append("</tbody></table></div>")
            continue
        if ITEM.match(line):
            ordered = ITEM.match(line).group(1)[0].isdigit()
            tag = "ol" if ordered else "ul"
            output.append("<" + tag + ">")
            while index < len(lines):
                item = ITEM.match(lines[index])
                if not item or item.group(1)[0].isdigit() != ordered:
                    break
                value = ' value="' + item.group(1)[:-1] + '"' if ordered else ""
                output.append("<li" + value + ">" + inline(item.group(2), anchors) + "</li>")
                index += 1
            output.append("</" + tag + ">")
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not HEADING.match(lines[index]) and not FENCE.match(lines[index]) and not ITEM.match(lines[index]) and not is_table(lines, index):
            paragraph.append(lines[index])
            index += 1
        output.append("<p>" + "<br>".join(inline(part, anchors) for part in paragraph) + "</p>")
    output.extend("</div></details>" for _ in levels)
    return title, "\n".join(toc), "\n".join(output)


def render_review(markdown, source_name="計画レビュー"):
    title, toc, content = render_markdown(markdown)
    template_path = Path(__file__).resolve().parent.parent / "assets" / "review.html"
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(title=html.escape(title or source_name), toc=toc, content=content)


def main():
    # Windowsのパイプ出力は既定でcp932になり、エラーの日本語が文字化けするため
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Markdown計画を折り畳み可能な自己完結HTMLへ変換します。")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve() or (args.output.exists() and args.input.exists() and args.input.samefile(args.output)):
        parser.error("入力と出力には異なるパスを指定してください。")
    try:
        markdown = args.input.read_text(encoding="utf-8-sig")
        document = render_review(markdown, args.input.name)
        args.output.write_text(document, encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
