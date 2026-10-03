import argparse
import base64
import binascii
import io
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import Image


class ImportError(ValueError):
    pass


def object_value(value, label):
    if not isinstance(value, dict):
        raise ImportError(f"{label} はオブジェクトにしてください")
    return value


def array_value(value, label):
    if not isinstance(value, list):
        raise ImportError(f"{label} は配列にしてください")
    return value


def text_value(value, label, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ImportError(f"{label} は文字列にしてください")
    return value


def integer_value(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise ImportError(f"{label} は {minimum} 以上の整数にしてください")
    return value


def number_value(value, label):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ImportError(f"{label} は有限の数値にしてください")
    return value


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImportError(f"JSON ファイルを読み込めません: {path.name}") from exc


def collect_candidates(report):
    object_value(report, "report")
    found = []

    def walk(suites, parents):
        for suite in array_value(suites, "suites"):
            object_value(suite, "suite")
            title = text_value(suite.get("title", ""), "suite.title", empty=True)
            names = parents + ([title] if title else [])
            for spec in array_value(suite.get("specs", []), "suite.specs"):
                object_value(spec, "spec")
                spec_title = text_value(spec.get("title"), "spec.title")
                for test in array_value(spec.get("tests", []), "spec.tests"):
                    object_value(test, "test")
                    project = text_value(test.get("projectName", ""), "test.projectName", empty=True)
                    project_id = text_value(test.get("projectId", ""), "test.projectId", empty=True)
                    for result in array_value(test.get("results", []), "test.results"):
                        object_value(result, "result")
                        status = text_value(result.get("status"), "result.status")
                        retry = integer_value(result.get("retry", 0), "result.retry", minimum=0)
                        for attachment in array_value(result.get("attachments", []), "result.attachments"):
                            object_value(attachment, "attachment")
                            if attachment.get("contentType") != "image/png":
                                continue
                            name = text_value(attachment.get("name"), "attachment.name", empty=True)
                            path = attachment.get("path")
                            if path is not None:
                                text_value(path, "attachment.path")
                            found.append({
                                "id": f"r{len(found) + 1:03d}",
                                "test": " / ".join(names + [spec_title]),
                                "project": project,
                                "project_id": project_id,
                                "retry": retry,
                                "status": status,
                                "name": name,
                                "path": path,
                                "_attachment": attachment,
                            })
            walk(suite.get("suites", []), names)

    walk(report.get("suites"), [])
    return found


def list_candidates(report):
    return {"attachments": [{key: value for key, value in candidate.items() if not key.startswith("_")}
                            for candidate in collect_candidates(report)]}


def source_path(value):
    if "\0" in value:
        raise ImportError("添付パスに NUL は使用できません")
    if PureWindowsPath(value).drive or "\\" in value:
        return PureWindowsPath(value)
    return PurePosixPath(value)


def project_output_dir(report, candidate):
    config = object_value(report.get("config", {}), "report.config")
    projects = array_value(config.get("projects", []), "config.projects")
    for project in projects:
        object_value(project, "config.project")
    if candidate["project_id"]:
        matches = [p for p in projects if p.get("id") == candidate["project_id"]]
    else:
        matches = [p for p in projects if p.get("name") == candidate["project"]]
    if len(matches) != 1:
        raise ImportError(f"{candidate['id']}: config.projects の出力先を一意に対応付けできません")
    return source_path(text_value(matches[0].get("outputDir"), "project.outputDir"))


def local_attachment_path(value, artifacts):
    path = Path(value)
    if path.is_absolute():
        resolved = path.resolve()
        if resolved.is_relative_to(artifacts):
            return resolved
    return None


def attachment_path(report, candidate, artifacts, remap):
    raw = source_path(candidate["path"])
    if ".." in raw.parts:
        raise ImportError(f"{candidate['id']}: 添付パスに .. は使用できません")
    if not raw.is_absolute() and (raw.drive or raw.root):
        raise ImportError(f"{candidate['id']}: 添付パスは絶対パスか相対パスにしてください")
    if raw.is_absolute():
        local = local_attachment_path(candidate["path"], artifacts)
        if local is not None:
            target = local
        elif remap:
            original_root = project_output_dir(report, candidate)
            if not original_root.is_absolute() or ".." in original_root.parts:
                raise ImportError(f"{candidate['id']}: project.outputDir が絶対パスではありません")
            try:
                relative = raw.relative_to(original_root)
            except ValueError as exc:
                raise ImportError(f"{candidate['id']}: 添付が元の project.outputDir の外にあります") from exc
            target = artifacts.joinpath(*relative.parts).resolve()
        else:
            raise ImportError(f"{candidate['id']}: 添付が artifacts の外にあります。移動した結果には --artifacts を指定してください")
    else:
        target = artifacts.joinpath(*raw.parts).resolve()
    if not target.is_relative_to(artifacts):
        raise ImportError(f"{candidate['id']}: 添付が artifacts の外を参照しています")
    return target


def read_png(report, candidate, artifacts, remap):
    attachment = candidate["_attachment"]
    try:
        if candidate["path"] is not None:
            data = attachment_path(report, candidate, artifacts, remap).read_bytes()
        else:
            body = text_value(attachment.get("body"), "attachment.body")
            try:
                data = base64.b64decode(body, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImportError(f"{candidate['id']}: attachment.body は base64 にしてください") from exc
    except (OSError, binascii.Error) as exc:
        raise ImportError(f"{candidate['id']}: 添付画像を読み込めません") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG":
                raise ImportError(f"{candidate['id']}: 添付は PNG ではありません")
            image.load()
            size = list(image.size)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, ImportError):
            raise
        raise ImportError(f"{candidate['id']}: PNG をデコードできません") from exc
    return data, size


def copy_text_fields(source, target, keys):
    for key in keys:
        if key in source:
            target[key] = text_value(source[key], key, empty=True)


def validate_layout(value):
    layout = []
    seen = set()
    for item in array_value(value, "data_layout"):
        object_value(item, "data_layout の項目")
        no = integer_value(item.get("no"), "data_layout.no")
        if no in seen:
            raise ImportError("data_layout.no が重複しています")
        seen.add(no)
        clean = {"no": no, "name": text_value(item.get("name"), "data_layout.name"),
                 "type": text_value(item.get("type"), "data_layout.type")}
        copy_text_fields(item, clean, ("note", "origin"))
        if "on_screen" in item:
            if type(item["on_screen"]) is not bool:
                raise ImportError("on_screen は true / false にしてください")
            clean["on_screen"] = item["on_screen"]
        layout.append(clean)
    return layout


def validate_steps(value, layout, size):
    steps = array_value(value, "steps")
    if not steps:
        raise ImportError("steps を 1 件以上指定してください")
    clean = []
    for step in steps:
        object_value(step, "step")
        kind = step.get("kind")
        if kind not in ("op", "data", "mark"):
            raise ImportError("step.kind は op / data / mark にしてください")
        current = {"kind": kind}
        if kind == "data":
            no = integer_value(step.get("item"), "step.item")
            if no not in layout:
                raise ImportError("step.item に対応する data_layout.no がありません")
            if not layout[no].get("on_screen", True):
                raise ImportError("画面外の項目に赤枠を付けることはできません")
            current["item"] = no
        else:
            current["text"] = text_value(step.get("text"), "step.text")
        box = array_value(step.get("box"), "step.box")
        if len(box) != 4:
            raise ImportError("step.box は [x, y, 幅, 高さ] にしてください")
        x, y, w, h = [number_value(n, "step.box") for n in box]
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > size[0] or y + h > size[1]:
            raise ImportError("step.box は画像内の正の大きさの矩形にしてください")
        current["box"] = list(box)
        copy_text_fields(step, current, ("detail",))
        clean.append(current)
    return clean


def select_candidates(report, ids, artifacts, remap):
    if not ids:
        raise ImportError("取り込む画像を 1 件以上選択してください")
    candidates = {c["id"]: c for c in collect_candidates(report)}
    selected, seen, roots = [], set(), set()
    for attachment_id in ids:
        text_value(attachment_id, "attachment")
        if attachment_id not in candidates:
            raise ImportError("attachment に対応する画像候補がありません")
        if attachment_id in seen:
            raise ImportError("attachment が重複しています")
        seen.add(attachment_id)
        candidate = candidates[attachment_id]
        if candidate["status"] != "passed":
            raise ImportError(f"{attachment_id}: passed 以外の実行結果は取り込めません。再撮影は行いません")
        if remap and candidate["path"] is not None:
            raw = source_path(candidate["path"])
            if raw.is_absolute() and local_attachment_path(candidate["path"], artifacts) is None:
                roots.add(project_output_dir(report, candidate))
        selected.append(candidate)
    if len(roots) > 1:
        raise ImportError("異なる project.outputDir の画像は分けて取り込んでください")
    return selected


def retry_warnings(selected):
    return [f"{c['id']}: retry={c['retry']} の成功結果を使用します" for c in selected if c["retry"]]


def prepare_import(report, plan, artifacts, remap=False):
    object_value(plan, "plan")
    deck = {"title": text_value(plan.get("title"), "title"),
            "data_layout": validate_layout(plan.get("data_layout")), "shots": []}
    copy_text_fields(plan, deck, ("subtitle", "site", "date"))
    layout = {item["no"]: item for item in deck["data_layout"]}
    shots = array_value(plan.get("shots"), "shots")
    for shot in shots:
        object_value(shot, "shot")
    selected = select_candidates(report, [s.get("attachment") for s in shots], artifacts, remap)
    images = {}
    for shot, candidate in zip(shots, selected):
        sid = text_value(shot.get("id"), "shot.id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sid) or len(sid) > 80:
            raise ImportError("shot.id は 80 文字以内の ASCII 英数字・_・- にしてください（先頭は英数字）")
        filename = f"images/shot_{sid}.png"
        if filename.lower() in images:
            raise ImportError("shot.id が重複しています（大文字小文字は区別しません）")
        scale = number_value(shot.get("scale", 1), "shot.scale")
        if scale <= 0:
            raise ImportError("shot.scale は正の数値にしてください")
        data, size = read_png(report, candidate, artifacts, remap)
        current = {"id": sid, "screen": text_value(shot.get("screen"), "shot.screen"),
                   "image": filename, "image_size": size, "scale": scale, "link": False,
                   "steps": validate_steps(shot.get("steps"), layout, size)}
        copy_text_fields(shot, current, ("title", "data_title"))
        if "notes" in shot:
            current["notes"] = [text_value(note, "notes の項目") for note in array_value(shot["notes"], "notes")]
        if "page_top" in shot:
            if type(shot["page_top"]) is not bool:
                raise ImportError("page_top は true / false にしてください")
            current["page_top"] = shot["page_top"]
        deck["shots"].append(current)
        images[filename.lower()] = (filename, data)
    return deck, images, retry_warnings(selected)


def validate_destination(report_path, out, artifacts):
    if out.exists() or out.is_symlink():
        raise ImportError("--out は存在しない新規フォルダを指定してください")
    root = (artifacts if artifacts is not None else report_path.parent).resolve()
    if not root.is_dir():
        raise ImportError("artifacts フォルダが存在しません")
    return root


def write_output(out, images, filename, content):
    out.mkdir(parents=True, exist_ok=False)
    for image_path, data in images.values():
        target = out / image_path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
    (out / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_results(report_path, plan_path, out, artifacts=None):
    root = validate_destination(report_path, out, artifacts)
    deck, images, warnings = prepare_import(read_json(report_path), read_json(plan_path), root,
                                           remap=artifacts is not None)
    write_output(out, images, "scenario.json", deck)
    return warnings


def extract_results(report_path, ids, out, artifacts=None):
    root = validate_destination(report_path, out, artifacts)
    report = read_json(report_path)
    selected = select_candidates(report, ids, root, artifacts is not None)
    images, index = {}, []
    for candidate in selected:
        data, size = read_png(report, candidate, root, artifacts is not None)
        filename = f"{candidate['id']}.png"
        images[filename] = (filename, data)
        item = {key: candidate[key] for key in ("id", "test", "project", "name", "retry")}
        item.update(image=filename, image_size=size)
        index.append(item)
    write_output(out, images, "index.json", {"images": index})
    return retry_warnings(selected)


def main():
    # Windowsのパイプ出力は既定でcp932になり、候補一覧やエラーの日本語が文字化けするため
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Playwright JSON reporter の選択済み PNG を資料用に取り込む")
    parser.add_argument("report", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="画像候補のみを JSON で表示する")
    mode.add_argument("--plan", type=Path, help="合意済みの画像・注釈を指定した JSON")
    mode.add_argument("--extract", help="目視用に選択 PNG だけを保存する（r001,r002）")
    parser.add_argument("--out", type=Path, help="新規出力フォルダ")
    parser.add_argument("--artifacts", type=Path, help="添付画像のフォルダ（既定: report の親）")
    args = parser.parse_args()
    if not args.list and args.out is None:
        parser.error("--plan / --extract には --out が必要です")
    if args.list and args.out is not None:
        parser.error("--list と --out は併用できません")
    try:
        if args.list:
            print(json.dumps(list_candidates(read_json(args.report)), ensure_ascii=False, indent=2))
        elif args.extract is not None:
            for warning in extract_results(args.report, [s.strip() for s in args.extract.split(",")],
                                           args.out, args.artifacts):
                print(f"WARN: {warning}", file=sys.stderr)
            print(f"index: {args.out / 'index.json'}")
        else:
            for warning in import_results(args.report, args.plan, args.out, args.artifacts):
                print(f"WARN: {warning}", file=sys.stderr)
            print(f"scenario: {args.out / 'scenario.json'}")
    except (ImportError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
