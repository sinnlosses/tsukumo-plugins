#!/usr/bin/env python3
"""architecture_map.py の自己テスト。架空の小さな入力だけを使う。

使い方: python3 architecture_map_selftest.py
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import architecture_map as am  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"

DEFINITION = """
overview = "架空の店の注文サービス。API が注文を受け、領域の層が処理し、基盤の層が保存する。"
forbidden_prefixes = ["shop-edge:"]

[[boxes]]
id = "app"
summary = "外からの要求を受ける"
purpose = "外の形式を内側へ持ち込まないため"

  [[boxes.children]]
  id = "api"
  summary = "要求を受けて領域へ渡す"
  paths = ["src/api/**"]

[[boxes]]
id = "core"
summary = "注文の決まりを持つ"

  [[boxes.children]]
  id = "domain"
  summary = "注文を扱う"
  paths = ["src/domain/**"]

[[boxes]]
id = "platform"
summary = "保存を受け持つ"

  [[boxes.children]]
  id = "db"
  summary = "表へ書く"
  paths = ["src/infra/**"]

[[edges]]
from = "api"
to = "domain"
label = "import"
"""

RULES = [
    {
        "name": "shop-edge:domain",
        "from": {"path": "^src/domain/"},
        "to": {"pathNot": "^src/(?:infra|domain)/"},
    },
    {"name": "other-rule", "from": {"path": "^src/api/"}, "to": {"pathNot": "^src/(?:domain)/"}},
]

FILES = {
    "src/api/routes/a.ts": ["src/domain/order/o.ts"],
    "src/api/routes/b.ts": [],
    "src/domain/order/o.ts": ["src/domain/order/p.ts", "src/infra/db/d.ts"],
    "src/domain/order/p.ts": [],
    "src/infra/db/d.ts": [],
    "src/infra/db/e.ts": [],
}


def depcruise(files, rules=RULES, extra_deps=None):
    modules = []
    for source, targets in files.items():
        deps = [{"resolved": t} for t in targets]
        deps += [{"resolved": "fs", "coreModule": True}, {"resolved": "node_modules/x/i.js"}]
        modules.append({"source": source, "dependencies": deps})
    return {"modules": modules, "summary": {"ruleSetUsed": {"forbidden": rules}}}


def definition(text=DEFINITION):
    return tomllib.loads(text)


def run(files, text=DEFINITION, rules=RULES, root=None):
    return am.build(definition(text), depcruise(files, rules), COMMIT, None, root)


def kinds(doc):
    return [f["kind"] for f in doc["findings"]]


def small_boxes(doc):
    return [s for big in doc["boxes"] for mid in big.get("children", []) for s in mid["children"]]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def conserved(doc, files):
    """入力の全ファイルが小の箱に属し、入力の全依存が実際のエッジか閉じた本数に残る"""
    owner = {}
    for small in small_boxes(doc):
        for path in files:
            if any(am.glob_match(p, path) for p in small["paths"]):
                owner[path] = small["id"]
    if set(owner) != set(files):
        return False
    pairs = {(s, t) for s, targets in files.items() for t in targets if t in files}
    closed = sum(1 for s, t in pairs if owner[s] == owner[t])
    return sum(e["count"] for e in doc["edges"]["actual"]) + closed == len(pairs)


class ArchitectureMapTest(unittest.TestCase):
    def test_clean_input_has_no_findings_and_conserves_every_import(self):
        doc, stats = run(FILES)
        self.assertEqual(doc["findings"], [])
        self.assertEqual(
            doc["edges"]["actual"],
            [
                {"from": "src/api/routes", "to": "src/domain/order", "count": 1},
                {"from": "src/domain/order", "to": "src/infra/db", "count": 1},
            ],
        )
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(stats["actual"] + stats["closed"] + stats["dropped"], stats["pairs"])
        self.assertTrue(conserved(doc, FILES))
        self.assertEqual(doc["boxes"][0]["children"][0]["children"][0]["paths"], ["src/api/routes/*"])
        self.assertEqual(doc["boxes"][0]["paths"], ["src/api/**"])
        self.assertEqual(doc["boxes"][0]["purpose"], "外の形式を内側へ持ち込まないため")
        self.assertTrue(doc["overview"].startswith("架空の店"))

    def test_conservation_check_fails_when_one_import_is_dropped(self):
        doc, _ = run(FILES)
        broken = copy.deepcopy(doc)
        broken["edges"]["actual"] = broken["edges"]["actual"][1:]
        self.assertFalse(conserved(broken, FILES))
        shortened = copy.deepcopy(doc)
        shortened["boxes"][0]["children"][0]["children"] = []
        self.assertFalse(conserved(shortened, FILES))

    def test_undeclared_import_is_an_error_at_the_declaring_level(self):
        files = {**FILES, "src/api/routes/b.ts": ["src/infra/db/e.ts"]}
        doc, _ = run(files)
        self.assertEqual(kinds(doc), ["undeclared-edge"])
        finding = doc["findings"][0]
        self.assertEqual((finding["from"], finding["to"], finding["count"]), ("app", "platform", 1))
        self.assertEqual(finding["sample"], {"from": "src/api/routes/b.ts", "to": "src/infra/db/e.ts"})
        self.assertEqual(finding["severity"], "error")

    def test_unmapped_file_is_an_error(self):
        doc, _ = run({**FILES, "src/misc/x.ts": []})
        self.assertEqual(kinds(doc), ["unmapped-file"])
        self.assertEqual(doc["findings"][0]["path"], "src/misc/x.ts")

    def test_intended_edge_is_read_from_the_named_rule_only(self):
        doc, _ = run(FILES)
        intended = {(e["from"], e["to"]): e for e in doc["edges"]["intended"]}
        self.assertEqual(intended[("src/domain/order", "src/infra/db")]["origin"], "shop-edge:domain")
        self.assertEqual(intended[("api", "domain")]["origin"], "definition")
        self.assertEqual(len(intended), 2)

    def test_unused_intended_edge_is_a_warning(self):
        text = DEFINITION + '\n[[edges]]\nfrom = "api"\nto = "db"\n'
        doc, _ = run(FILES, text)
        self.assertEqual(kinds(doc), ["unused-intended-edge"])
        self.assertEqual(doc["findings"][0]["severity"], "warn")

    def test_hidden_file_needs_the_dot_path_in_the_medium_box(self):
        files = {**FILES, "src/domain/order/.hidden.ts": []}
        doc, _ = run(files)
        self.assertEqual([(f["kind"], f["path"]) for f in doc["findings"]], [("unmapped-file", "src/domain/order/.hidden.ts")])
        order = next(s for s in small_boxes(doc) if s["id"] == "src/domain/order")
        self.assertEqual(order["paths"], ["src/domain/order/*", "src/domain/order/.*"])
        fixed = DEFINITION.replace('paths = ["src/domain/**"]', 'paths = ["src/domain/**", "src/domain/*/.*"]')
        doc, _ = run(files, fixed)
        self.assertEqual(doc["findings"], [])

    def test_other_finding_kinds(self):
        overlapping = DEFINITION.replace('paths = ["src/infra/**"]', 'paths = ["src/infra/**", "src/api/**"]')
        doc, _ = run(FILES, overlapping)
        self.assertIn("overlapping-file", kinds(doc))
        split = DEFINITION.replace('paths = ["src/api/**"]', 'paths = ["src/api/**", "src/infra/db/d.ts"]').replace(
            'paths = ["src/infra/**"]', 'paths = ["src/infra/db/e.ts"]'
        )
        doc, _ = run(FILES, split)
        self.assertIn("split-directory", kinds(doc))
        empty = DEFINITION.replace('paths = ["src/infra/**"]', 'paths = ["src/nothing/**"]')
        doc, _ = run(FILES, empty)
        self.assertIn("empty-box", kinds(doc))
        linked = DEFINITION.replace('summary = "要求を受けて領域へ渡す"', 'summary = "要求を受けて領域へ渡す"\n  links = { adr = ["docs/nope.md"] }')
        with tempfile.TemporaryDirectory() as root:
            doc, _ = run(FILES, linked, root=root)
        self.assertIn("missing-link", kinds(doc))

    def test_cli_exit_codes_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {name: os.path.join(tmp, name) for name in ("def.toml", "dep.json", "map.json", "bad.toml")}
            with open(paths["def.toml"], "w", encoding="utf-8") as handle:
                handle.write(DEFINITION)
            with open(paths["bad.toml"], "w", encoding="utf-8") as handle:
                handle.write(DEFINITION.replace('id = "api"', 'id = "a pi"'))

            def cli(files, definition_path=paths["def.toml"]):
                with open(paths["dep.json"], "w", encoding="utf-8") as handle:
                    json.dump(depcruise(files), handle)
                return subprocess.run(
                    [sys.executable, os.path.join(HERE, "architecture_map.py"), "--definition", definition_path,
                     "--dependencies", paths["dep.json"], "--output", paths["map.json"], "--commit", COMMIT, "--root", tmp],
                    capture_output=True, text=True,
                ).returncode

            self.assertEqual(cli(FILES), 0)
            first = read_bytes(paths["map.json"])
            self.assertEqual(cli(FILES), 0)
            self.assertEqual(first, read_bytes(paths["map.json"]))
            self.assertEqual(cli({**FILES, "src/misc/x.ts": []}), 1)
            self.assertIn(b"unmapped-file", read_bytes(paths["map.json"]))
            before = read_bytes(paths["map.json"])
            self.assertEqual(cli(FILES, paths["bad.toml"]), 2)
            self.assertEqual(before, read_bytes(paths["map.json"]))

    def test_broken_definition_is_rejected(self):
        for bad in (
            DEFINITION.replace('paths = ["src/api/**"]', 'paths = ["./src/api/**"]'),
            DEFINITION.replace('paths = ["src/api/**"]', 'paths = ["src/{a,b}/**"]'),
            DEFINITION.replace('id = "db"', 'id = "api"'),
            DEFINITION.replace('to = "domain"', 'to = "ghost"'),
            "boxes = []",
        ):
            with self.assertRaises(am.DefinitionError):
                run(FILES, bad)


if __name__ == "__main__":
    unittest.main(verbosity=1)
