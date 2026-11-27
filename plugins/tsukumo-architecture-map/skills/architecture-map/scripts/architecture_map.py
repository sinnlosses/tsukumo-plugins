#!/usr/bin/env python3
"""地図の定義（TOML）と dependency-cruiser の JSON から、地図のファイル（JSON）を書く。

使い方:
  python3 architecture_map.py --definition <toml> --dependencies <depcruise の JSON か -> \
      [--output .architecture-map.json] [--tree <sha>] [--root <dir>] [--commit <sha>]

終了コード: 0（error のずれ無し）・1（error のずれあり。ファイルは書く）・2（定義が壊れている。書かない）。
契約は版 1。正典は tsukumo の docs/architecture/architecture-map.md。
Python 3.13 以降が要る（glob.translate）。標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib

PRODUCER = "tsukumo-architecture-map 0.1.0"
VERSION = 1
ID_SEGMENT = re.compile(r"\.?[A-Za-z0-9_][A-Za-z0-9._-]*")
FORBIDDEN_GLOB_CHARS = "?[]{}!"
BOX_KEYS = (
    "id",
    "title",
    "summary",
    "purpose",
    "overview",
    "paths",
    "entrypoints",
    "links",
    "aliases",
    "external",
    "lane",
    "place",
    "children",
)
LANE_KINDS = ("runtime", "crosscut", "external")
FINDING_FIELDS = ("from", "to", "path", "box", "boxes", "count")


class DefinitionError(Exception):
    pass


def valid_id(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        return False
    if any(ch in value for ch in " {}"):
        return False
    return all(ID_SEGMENT.fullmatch(part) for part in value.split("/"))


def check_glob(value, where):
    if not isinstance(value, str) or not value:
        raise DefinitionError(f"{where}: paths の要素は空でない文字列")
    if value.startswith("./") or value.startswith("/"):
        raise DefinitionError(f"{where}: パス {value!r} の頭に ./ ・ / を付けない")
    for seg in value.split("/"):
        if not seg:
            raise DefinitionError(f"{where}: パス {value!r} に空の区切りがある")
        if any(ch in seg for ch in FORBIDDEN_GLOB_CHARS):
            raise DefinitionError(f"{where}: パス {value!r} に使えない字がある")
        if "**" in seg and seg != "**":
            raise DefinitionError(f"{where}: パス {value!r} の ** は区切り1つだけで書く")


_GLOB_CACHE: dict[str, re.Pattern] = {}


def glob_match(pattern, path):
    compiled = _GLOB_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(glob.translate(pattern, recursive=True, include_hidden=False, seps="/"))
        _GLOB_CACHE[pattern] = compiled
    return compiled.fullmatch(path) is not None


def text_field(box, key, where, required=False):
    value = box.get(key)
    if value is None:
        if required:
            raise DefinitionError(f"{where}: {key} が要る")
        return None
    if not isinstance(value, str) or not value.strip():
        raise DefinitionError(f"{where}: {key} は空でない文字列")
    return value


def parse_box(raw, level, seen_ids, where):
    if not isinstance(raw, dict):
        raise DefinitionError(f"{where}: 箱はテーブル")
    box_id = raw.get("id")
    if not valid_id(box_id):
        raise DefinitionError(f"{where}: 箱の ID {box_id!r} が決まりに合わない")
    where = f"{where}[{box_id}]"
    aliases = raw.get("aliases", [])
    if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
        raise DefinitionError(f"{where}: aliases は文字列の配列")
    links = raw.get("links")
    if links is not None:
        if not isinstance(links, dict):
            raise DefinitionError(f"{where}: links はテーブル")
        for key in ("adr", "terms"):
            value = links.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise DefinitionError(f"{where}: links.{key} は文字列の配列")
    names = [box_id] + list(aliases)
    for name in names:
        if not valid_id(name):
            raise DefinitionError(f"{where}: ID・別名 {name!r} が決まりに合わない")
        if name in seen_ids:
            raise DefinitionError(f"{where}: ID・別名 {name!r} が重なっている")
        seen_ids.add(name)
    external = raw.get("external", False)
    if external and level != 0:
        raise DefinitionError(f"{where}: external は大の箱だけ")
    box = {"id": box_id, "level": level, "external": bool(external)}
    if "lane" in raw:
        if level != 0:
            raise DefinitionError(f"{where}: lane は大の箱だけ")
        box["lane"] = text_field(raw, "lane", where)
    box["title"] = text_field(raw, "title", where)
    box["summary"] = text_field(raw, "summary", where, required=True)
    box["purpose"] = text_field(raw, "purpose", where)
    box["overview"] = text_field(raw, "overview", where)
    box["aliases"] = list(aliases)
    box["links"] = links
    entrypoints = raw.get("entrypoints")
    if entrypoints is not None:
        if external:
            raise DefinitionError(f"{where}: 外の世界の箱は entrypoints を持たない")
        if not isinstance(entrypoints, list) or not entrypoints:
            raise DefinitionError(f"{where}: entrypoints は文字列の配列（1件以上）")
        for entry in entrypoints:
            check_glob(entry, where)
            if "*" in entry:
                raise DefinitionError(f"{where}: entrypoints の {entry!r} に * を含めない")
    box["entrypoints"] = list(entrypoints) if entrypoints is not None else None
    box["place"] = raw.get("place")
    if box["place"] is not None:
        place = box["place"]
        if not (isinstance(place, dict) and isinstance(place.get("column"), int) and isinstance(place.get("row"), int)):
            raise DefinitionError(f"{where}: place は column・row の整数")
    children_raw = raw.get("children", [])
    if external:
        if children_raw or raw.get("paths"):
            raise DefinitionError(f"{where}: 外の世界の箱は paths も children も持たない")
        box["globs"] = []
        box["children"] = []
        return box
    if level == 0:
        if not children_raw:
            raise DefinitionError(f"{where}: 大の箱は中の箱を1つ以上持つ")
        box["children"] = [parse_box(c, 1, seen_ids, f"{where}.children") for c in children_raw]
        box["globs"] = []
        for child in box["children"]:
            for pattern in child["globs"]:
                if pattern not in box["globs"]:
                    box["globs"].append(pattern)
    else:
        if children_raw:
            raise DefinitionError(f"{where}: 中の箱の子（小の箱）は抽出が作る。定義に書かない")
        paths = raw.get("paths")
        if not isinstance(paths, list) or not paths:
            raise DefinitionError(f"{where}: 中の箱は paths が要る")
        for pattern in paths:
            check_glob(pattern, where)
        box["globs"] = list(paths)
        box["children"] = []
    return box


def parse_definition(defn):
    seen_ids: set[str] = set()
    raw_boxes = defn.get("boxes")
    if not isinstance(raw_boxes, list) or not raw_boxes:
        raise DefinitionError("boxes が要る（大の箱を1つ以上）")
    boxes = [parse_box(b, 0, seen_ids, "boxes") for b in raw_boxes]
    ids = set(seen_ids)
    edges = []
    for raw in defn.get("edges", []):
        if not isinstance(raw, dict):
            raise DefinitionError("edges の要素はテーブル")
        src, dst = raw.get("from"), raw.get("to")
        if src not in ids or dst not in ids:
            raise DefinitionError(f"edges: {src!r} → {dst!r} の端が定義の ID にない（別名は端に使えない）")
        if src == dst:
            raise DefinitionError(f"edges: {src!r} から自分自身へは書けない")
        edge = {"from": src, "to": dst, "origin": "definition"}
        if "label" in raw:
            if not isinstance(raw["label"], str):
                raise DefinitionError("edges: label は文字列")
            edge["label"] = raw["label"]
        edges.append(edge)
    prefixes = defn.get("forbidden_prefixes", [])
    if not isinstance(prefixes, list) or not all(isinstance(p, str) and p for p in prefixes):
        raise DefinitionError("forbidden_prefixes は文字列の配列")
    overview = text_field(defn, "overview", "頭")
    lanes = parse_lanes(defn.get("lanes"), boxes)
    return {"boxes": boxes, "edges": edges, "prefixes": prefixes, "overview": overview, "ids": ids, "lanes": lanes}


def parse_lanes(raw_lanes, boxes):
    if raw_lanes is None:
        if any("lane" in b for b in boxes):
            raise DefinitionError("lane を書くなら [[lanes]] が要る")
        return None
    if not isinstance(raw_lanes, list) or not raw_lanes:
        raise DefinitionError("lanes は区画のテーブルを1つ以上並べる")
    lanes = []
    kinds = {}
    for raw in raw_lanes:
        if not isinstance(raw, dict):
            raise DefinitionError("lanes の要素はテーブル")
        lane_id = raw.get("id")
        if not valid_id(lane_id) or "/" in lane_id:
            raise DefinitionError(f"lanes: 区画の id {lane_id!r} が決まりに合わない")
        if lane_id in kinds:
            raise DefinitionError(f"lanes: 区画の id {lane_id!r} が重なっている")
        kind = raw.get("kind")
        if kind not in LANE_KINDS:
            raise DefinitionError(f"lanes[{lane_id}]: kind は runtime・crosscut・external のどれか")
        kinds[lane_id] = kind
        lane = {"id": lane_id, "kind": kind}
        title = text_field(raw, "title", f"lanes[{lane_id}]")
        if title is not None:
            lane["title"] = title
        lanes.append(lane)
    members = {lane_id: 0 for lane_id in kinds}
    for box in boxes:
        lane_id = box.get("lane")
        if lane_id is None:
            raise DefinitionError(f"{box['id']}: lanes を書くなら大の箱は全部 lane を持つ")
        if lane_id not in kinds:
            raise DefinitionError(f"{box['id']}: lane {lane_id!r} が lanes の id にない")
        if box["external"] != (kinds[lane_id] == "external"):
            raise DefinitionError(f"{box['id']}: external の箱は kind = external の区画にだけ属し、その区画には外の世界の箱だけが属する")
        members[lane_id] += 1
    for lane_id, count in members.items():
        if count == 0:
            raise DefinitionError(f"lanes[{lane_id}]: 属する大の箱が1つも無い")
    return lanes


def read_modules(dep):
    if not isinstance(dep, dict) or not isinstance(dep.get("modules"), list):
        raise DefinitionError("dependency-cruiser の JSON に modules が無い")
    files = set()
    pairs = set()
    modules = []
    for module in dep["modules"]:
        source = module.get("source")
        if not isinstance(source, str) or module.get("coreModule"):
            continue
        source = source.removeprefix("./")
        if source.startswith("node_modules/") or "/node_modules/" in source:
            continue
        files.add(source)
        modules.append((source, module.get("dependencies") or []))
    for source, deps in modules:
        for item in deps:
            resolved = item.get("resolved")
            if not isinstance(resolved, str) or item.get("coreModule") or item.get("couldNotResolve"):
                continue
            resolved = resolved.removeprefix("./")
            if resolved in files:
                pairs.add((source, resolved))
    return files, pairs


def read_forbidden(dep, prefixes):
    rules = (((dep.get("summary") or {}).get("ruleSetUsed") or {}).get("forbidden")) or []
    found = []
    for rule in rules:
        name = rule.get("name")
        if not isinstance(name, str) or not any(name.startswith(p) for p in prefixes):
            continue
        src = (rule.get("from") or {}).get("path")
        dst = (rule.get("to") or {}).get("pathNot")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        source_dir = unescape_prefix(src)
        target = re.fullmatch(r"\^(.+?)/(?:\(\?:([^()]+)\)|([^/|()]+))/", dst)
        if source_dir is None or target is None:
            continue
        parent = unescape_prefix("^" + target.group(1) + "/")
        if parent is None:
            continue
        names = [re.sub(r"\\(.)", r"\1", n) for n in (target.group(2) or target.group(3)).split("|")]
        found.append((name, source_dir, [f"{parent}/{n}" for n in names]))
    return found


def unescape_prefix(value):
    match = re.fullmatch(r"\^(.+)/", value)
    if match is None:
        return None
    body = match.group(1)
    if re.search(r"[()\[\]*+?|^$]", re.sub(r"\\.", "", body)):
        return None
    return re.sub(r"\\(.)", r"\1", body)


def flatten(boxes):
    for big in boxes:
        yield big
        for mid in big["children"]:
            yield mid


def head_directory(globs):
    """paths の共通の頭のディレクトリ（ワイルドカードより前の区切り）。無ければ None"""
    heads = []
    for pattern in globs:
        segments = pattern.split("/")
        literal = []
        for seg in segments[:-1]:
            if "*" in seg:
                break
            literal.append(seg)
        heads.append(literal)
    if not heads:
        return None
    common = heads[0]
    for head in heads[1:]:
        size = 0
        while size < min(len(common), len(head)) and common[size] == head[size]:
            size += 1
        common = common[:size]
    return "/".join(common) or None


def pick_entrypoint(globs, files):
    """頭のディレクトリの直下から index.* → ディレクトリと同名 → main.* の順で最初の1つを選ぶ"""
    directory = head_directory(globs)
    if directory is None:
        return None
    base = directory.rsplit("/", 1)[-1]
    inside = sorted(
        f for f in files if f.rsplit("/", 1)[0] == directory and "/" in f and any(glob_match(p, f) for p in globs)
    )
    for wanted in ("index", base, "main"):
        for f in inside:
            if os.path.splitext(os.path.basename(f))[0] == wanted:
                return f
    return None


def resolve_entrypoints(box, files):
    if box["external"]:
        return
    if box.get("entrypoints"):
        for entry in box["entrypoints"]:
            if entry not in files or not any(glob_match(p, entry) for p in box["globs"]):
                raise DefinitionError(f"{box['id']}: entrypoints の {entry!r} がこの箱の paths に当たる抽出したファイルにない")
        return
    picked = pick_entrypoint(box["globs"], files)
    box["entrypoints"] = [picked] if picked else None


def build(defn, dep, commit, tree=None, root=None):
    parsed = parse_definition(defn)
    files, pairs = read_modules(dep)
    bigs = parsed["boxes"]
    for box in flatten(bigs):
        resolve_entrypoints(box, files)
    mediums = [m for big in bigs for m in big["children"]]
    medium_big = {m["id"]: big["id"] for big in bigs for m in big["children"]}

    matches = {f: [m["id"] for m in mediums if any(glob_match(p, f) for p in m["globs"])] for f in sorted(files)}
    findings = []
    by_dir: dict[str, list[str]] = {}
    for f in sorted(files):
        if "/" in f:
            by_dir.setdefault(f.rsplit("/", 1)[0], []).append(f)
        owners = matches[f]
        if not owners:
            findings.append({"kind": "unmapped-file", "severity": "error", "path": f})
        elif len(owners) > 1:
            findings.append({"kind": "overlapping-file", "severity": "error", "path": f, "boxes": sorted(owners)})

    placed: dict[str, str] = {}
    small_of_medium: dict[str, list[str]] = {m["id"]: [] for m in mediums}
    for directory in sorted(by_dir):
        owners = {matches[f][0] for f in by_dir[directory] if len(matches[f]) == 1}
        if len(owners) > 1:
            findings.append({"kind": "split-directory", "severity": "error", "path": directory, "boxes": sorted(owners)})
            continue
        if not owners or not valid_id(directory):
            continue
        owner = next(iter(owners))
        placed[directory] = owner
        small_of_medium[owner].append(directory)

    def small_box(directory):
        paths = [f"{directory}/*"]
        if any(os.path.basename(f).startswith(".") for f in by_dir[directory]):
            paths.append(f"{directory}/.*")
        small = {"id": directory, "paths": paths}
        picked = pick_entrypoint(paths, by_dir[directory])
        if picked:
            small["entrypoints"] = [picked]
        return small

    def chain(path):
        directory = path.rsplit("/", 1)[0] if "/" in path else None
        if directory not in placed:
            return None
        medium = placed[directory]
        return (directory, medium, medium_big[medium])

    actual: dict[tuple[str, str], int] = {}
    uncovered: dict[tuple[str, str], list] = {}
    placed_pairs = []
    closed = dropped = 0
    for a, b in sorted(pairs):
        ca, cb = chain(a), chain(b)
        if ca is None or cb is None:
            dropped += 1
            continue
        if ca[0] == cb[0]:
            closed += 1
            continue
        actual[(ca[0], cb[0])] = actual.get((ca[0], cb[0]), 0) + 1
        placed_pairs.append((a, b, ca, cb))

    intended = list(parsed["edges"])
    known = {(e["from"], e["to"]) for e in intended}
    for name, source_dir, targets in read_forbidden(dep, parsed["prefixes"]):
        src_box = box_for_directory(source_dir, files, matches, placed, medium_big)
        for target in targets:
            dst_box = box_for_directory(target, files, matches, placed, medium_big)
            if src_box is None or dst_box is None or src_box == dst_box or (src_box, dst_box) in known:
                continue
            known.add((src_box, dst_box))
            intended.append({"from": src_box, "to": dst_box, "origin": name})
    intended.sort(key=lambda e: (e["from"], e["to"]))
    external = {b["id"] for b in bigs if b["external"]}

    for a, b, ca, cb in placed_pairs:
        if ca[1] == cb[1]:
            continue
        if ca[2] == cb[2]:
            level = (ca[1], cb[1])
        else:
            level = (ca[2], cb[2])
        if any(e["from"] in ca and e["to"] in cb for e in intended):
            continue
        uncovered.setdefault(level, []).append((a, b))
    for (src, dst), items in sorted(uncovered.items()):
        first = min(items)
        findings.append(
            {
                "kind": "undeclared-edge",
                "severity": "error",
                "from": src,
                "to": dst,
                "count": len(items),
                "sample": {"from": first[0], "to": first[1]},
            }
        )
    for e in intended:
        if e["from"] in external or e["to"] in external:
            continue
        if not any(e["from"] in ca and e["to"] in cb for _, _, ca, cb in placed_pairs):
            findings.append({"kind": "unused-intended-edge", "severity": "warn", "from": e["from"], "to": e["to"]})

    for box in flatten(bigs):
        if box["external"]:
            continue
        if not any(any(glob_match(p, f) for p in box["globs"]) for f in files):
            findings.append({"kind": "empty-box", "severity": "error", "box": box["id"]})
        links = box.get("links") or {}
        for adr in links.get("adr", []) or []:
            if root is not None and not os.path.exists(os.path.join(root, adr)):
                findings.append({"kind": "missing-link", "severity": "error", "box": box["id"], "path": adr})
    findings.sort(key=lambda f: (f["kind"],) + tuple(str(f.get(k, "")) for k in FINDING_FIELDS))

    def render(box):
        out = {}
        for key in BOX_KEYS:
            if key == "paths":
                out["paths"] = list(box["globs"])
            elif key == "children":
                if box["external"]:
                    continue
                if box["level"] == 0:
                    out["children"] = [render(m) for m in box["children"]]
                else:
                    out["children"] = [small_box(d) for d in sorted(small_of_medium[box["id"]])]
            elif key == "external":
                if box["external"]:
                    out["external"] = True
            elif key == "aliases":
                if box["aliases"]:
                    out["aliases"] = box["aliases"]
            elif box.get(key) is not None:
                out[key] = box[key]
        return out

    source = {"producer": PRODUCER, "commit": commit}
    if tree:
        source["tree"] = tree
    doc = {"version": VERSION, "source": source}
    if parsed["overview"]:
        doc["overview"] = parsed["overview"]
    if parsed["lanes"]:
        doc["lanes"] = parsed["lanes"]
    doc["boxes"] = [render(b) for b in bigs]
    doc["edges"] = {
        "intended": intended,
        "actual": [{"from": s, "to": d, "count": n} for (s, d), n in sorted(actual.items())],
    }
    doc["findings"] = findings
    stats = {"pairs": len(pairs), "closed": closed, "dropped": dropped, "actual": sum(actual.values())}
    return doc, stats


def box_for_directory(directory, files, matches, placed, medium_big):
    members = [f for f in files if f.startswith(directory + "/") and len(matches[f]) == 1]
    if not members:
        return None
    dirs = {f.rsplit("/", 1)[0] for f in members}
    if len(dirs) == 1 and next(iter(dirs)) in placed:
        return next(iter(dirs))
    mediums = {matches[f][0] for f in members}
    if len(mediums) == 1:
        return next(iter(mediums))
    bigs = {medium_big[m] for m in mediums}
    if len(bigs) == 1:
        return next(iter(bigs))
    return None


def write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".architecture-map-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def head_commit(root):
    result = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
    if result.returncode != 0:
        raise DefinitionError("git rev-parse HEAD が打てない（--commit で渡す）")
    return result.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--definition", required=True)
    parser.add_argument("--dependencies", required=True)
    parser.add_argument("--output", default=".architecture-map.json")
    parser.add_argument("--tree")
    parser.add_argument("--root", default=".")
    parser.add_argument("--commit")
    args = parser.parse_args(argv)
    try:
        with open(args.definition, "rb") as handle:
            defn = tomllib.load(handle)
        raw = sys.stdin.read() if args.dependencies == "-" else open(args.dependencies, encoding="utf-8").read()
        dep = json.loads(raw)
        commit = args.commit or head_commit(args.root)
        doc, stats = build(defn, dep, commit, args.tree, args.root)
    except (DefinitionError, tomllib.TOMLDecodeError, json.JSONDecodeError, OSError) as error:
        print(f"定義か入力が壊れている: {error}", file=sys.stderr)
        return 2
    write_atomic(args.output, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    errors = [f for f in doc["findings"] if f["severity"] == "error"]
    print(
        f"書いた: {args.output}（実際のエッジ {len(doc['edges']['actual'])} 件・本数 {stats['actual']}"
        f"・同じ小の箱で閉じた {stats['closed']}・箱に属さず落とした {stats['dropped']}"
        f"・ずれ error {len(errors)} 件・warn {len(doc['findings']) - len(errors)} 件）"
    )
    for finding in doc["findings"]:
        print("  " + json.dumps(finding, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
