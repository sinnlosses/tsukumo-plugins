"""偽の GitHub（`FakeGitHub`）と、それへ `bd`・`gh` を向ける足場。

GitHub には繋がない。本物の `bd` の `bd github push`・`pull` を `GITHUB_API_URL` で
偽の HTTP サーバ（`FakeGitHub`。REST の Issue と Project の GraphQL）へ向ける。`gh` は PATH の先頭に置いた
偽のコマンドが `api` を同じサーバへ転送する。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import beads  # noqa: E402
from selftest_b_support import bd, make_repo  # noqa: E402
from selftest_support import local, write  # noqa: E402


class FakeGitHub:
    """偽の GitHub。REST の Issue（本物の `bd github push`・`pull` が `GITHUB_API_URL` で叩く）と、
    Project の GraphQL（偽の `gh api graphql` が転送する）を、1つの HTTP サーバの中の状態で受ける。"""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.items: dict[str, dict] = {}  # 項目 ID → {"url", "status"}
        self.option_prefix = "O-"
        self.status_writes = 0  # Status 欄を書いた回数
        self.issue_patches: list[int] = []  # Issue を PATCH で更新した番号（順に）
        self.fetched: list[int] = []  # Issue を1件ずつ GET で取った番号（`bd github pull` が取りに来た分）
        self.listed: list[list[int]] = []  # Issue の一覧 GET が返した番号（呼び出しごと）
        self.clock_offset = 0  # 秒。GitHub 側の編集を後の時刻にする
        self.delay = 0.0  # 秒。応答を遅らせる
        self.delay_post_only = False  # 立てる POST の応答だけ遅らせる
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401
                pass

            def _reply(self, code: int, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode() or "null") if n else None
                with fake.lock:
                    code, obj = fake.route(self.command, self.path, body)
                if not fake.delay_post_only or self.command == "POST":
                    time.sleep(fake.delay)
                self._reply(code, obj)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + self.clock_offset))

    def issue_json(self, i: dict) -> dict:
        n = i["number"]
        login = i.get("assignee")
        user = {"login": login, "id": 5000 + n} if login else None
        return {
            "id": 1000 + n, "node_id": f"I_{n}", "number": n, "title": i["title"], "body": i["body"],
            "state": i["state"], "labels": [{"name": l} for l in i["labels"]],
            "assignees": [user] if user else [], "assignee": user,
            "created_at": i["created_at"], "updated_at": i["updated_at"], "closed_at": None,
            "html_url": f"https://github.com/o/r/issues/{n}", "url": f"{self.url}/repos/o/r/issues/{n}",
            "user": {"login": "someone"},
        }

    def edit(self, number: int, **fields) -> None:
        """人が GitHub で Issue を直した（題・本文・state・labels・担当者の login の assignee）。"""
        with self.lock:
            self.issues[number].update(fields)
            self.issues[number]["updated_at"] = self.now()

    def open_issue(self, title: str, labels: list[str], body: str = "外で立てた") -> int:
        with self.lock:
            return self._create({"title": title, "body": body, "labels": labels})["number"]

    def _create(self, body: dict) -> dict:
        n = len(self.issues) + 1
        issue = {"number": n, "title": body.get("title", ""), "body": body.get("body") or "", "state": "open",
                 "labels": list(body.get("labels") or []), "created_at": self.now(), "updated_at": self.now()}
        self.issues[n] = issue
        return issue

    def status_of(self, number: int) -> str | None:
        url = f"https://github.com/o/r/issues/{number}"
        return next((i["status"] for i in self.items.values() if i["url"] == url), None)

    def route(self, method: str, path: str, body):
        u = urlparse(path)
        m = re.fullmatch(r"/repos/o/r/issues/(\d+)", u.path)
        if u.path == "/graphql":
            return 200, self.answer_graphql(body["query"], body.get("variables") or {})
        if method == "POST" and u.path == "/repos/o/r/issues":
            return 201, self.issue_json(self._create(body))
        if m and method == "PATCH":
            issue = self.issues[int(m.group(1))]
            self.issue_patches.append(issue["number"])
            for k in ("title", "body", "state", "labels"):
                if k in body:
                    issue[k] = body[k]
            issue["updated_at"] = self.now()
            return 200, self.issue_json(issue)
        if m and method == "GET":
            self.fetched.append(int(m.group(1)))
            issue = self.issues.get(int(m.group(1)))
            return (200, self.issue_json(issue)) if issue else (404, {"message": "Not Found"})
        if u.path == "/repos/o/r/issues" and method == "GET":
            q = parse_qs(u.query)
            state, since = (q.get("state") or ["open"])[0], (q.get("since") or [""])[0]
            found = [i for i in self.issues.values() if (state == "all" or i["state"] == state)
                     and (not since or i["updated_at"] >= since)]
            self.listed.append([i["number"] for i in found])
            return 200, [self.issue_json(i) for i in found]
        return 404, {"message": f"fake: {method} {u.path}"}

    def answer_graphql(self, query: str, v: dict) -> dict:
        names = ["Pending", "Todo", "In progress", "Done", "Cancel"]
        if "repositoryOwner" in query:
            options = [{"id": self.option_prefix + n, "name": n} for n in names]
            return {"data": {"repositoryOwner": {"projectV2": {"id": "PID", "field": {"id": "FID", "options": options}}}}}
        if "projectItems" in query:
            url = f"https://github.com/{v['owner']}/{v['repo']}/issues/{v['number']}"
            nodes = [{"id": k, "project": {"id": "PID"}} for k, i in self.items.items() if i["url"] == url]
            return {"data": {"repository": {"issue": {"id": f"I_{v['number']}", "projectItems": {"nodes": nodes}}}}}
        if "addProjectV2ItemById" in query:
            n = v["content"].split("_", 1)[1]
            item_id = f"PVTI_{n}"
            self.items[item_id] = {"url": f"https://github.com/o/r/issues/{n}", "status": None}
            return {"data": {"addProjectV2ItemById": {"item": {"id": item_id}}}}
        if "updateProjectV2ItemFieldValue" in query:
            option, item = v["option"], self.items.get(v["item"])
            if item is None or not option.startswith(self.option_prefix):
                return {"errors": [{"message": "Could not resolve to a node"}]}
            item["status"] = option[len(self.option_prefix):]
            self.status_writes += 1
            return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": v["item"]}}}}
        if "items(first" in query:
            nodes = [{"id": k, "content": {"url": i["url"]},
                      "fieldValueByName": {"name": i["status"]} if i["status"] else None} for k, i in self.items.items()]
            return {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}
        return {"errors": [{"message": "fake: 知らない問い合わせ"}]}


def _fake_bin(tmp: str) -> str:
    """偽の `gh`（`api` を偽の GitHub へ転送する）。"""
    bin_dir = os.path.join(tmp, "bin")
    write(os.path.join(bin_dir, "gh"), f"""#!{sys.executable}
import json, os, sys, urllib.request
args = sys.argv[1:]
server = os.environ["GITHUB_API_URL"]
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method, headers={{"Content-Type": "application/json"}})
    try:
        return json.load(urllib.request.urlopen(req, timeout=10))
    except OSError as e:
        sys.stderr.write(f"fake gh: {{e}}\\n")
        sys.exit(1)
if args[:2] == ["auth", "token"]:
    print("fake-token")
elif args[:2] == ["api", "graphql"]:
    query, variables = "", {{}}
    for flag, kv in zip(args[2::2], args[3::2]):
        k, v = kv.split("=", 1)
        if k == "query":
            query = v
        else:
            variables[k] = int(v) if flag == "-F" else v
    print(json.dumps(call("POST", "/graphql", {{"query": query, "variables": variables}})))
elif args[:1] == ["api"]:
    path = "/" + [a for a in args[1:] if not a.startswith("--")][0]
    out = call("GET", path)
    print(json.dumps([out] if "--slurp" in args else out))
else:
    sys.stderr.write("fake gh: 知らない呼び出し " + " ".join(args) + "\\n")
    sys.exit(1)
""")
    os.chmod(os.path.join(bin_dir, "gh"), 0o755)
    # `BD_COUNT_FILE` があるときだけ、`bd` の起動を1行ずつ控える。
    write(os.path.join(bin_dir, "bd"),
          f'#!/bin/sh\n[ -n "$BD_COUNT_FILE" ] && echo "$*" >> "$BD_COUNT_FILE"\nexec {shutil.which("bd")} "$@"\n')
    os.chmod(os.path.join(bin_dir, "bd"), 0o755)
    return bin_dir


def _with_fakes(tmp: str, fake: FakeGitHub) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = _fake_bin(tmp) + os.pathsep + env.get("PATH", "")
    env["GITHUB_API_URL"] = fake.url
    env.pop("GITHUB_TOKEN", None)
    return env


@contextlib.contextmanager
def github(
    prefix: str | None = beads.PREFIX_GITHUB, verify: str | None = None
) -> Iterator[tuple["FakeGitHub", str, str, str]]:
    """偽の GitHub へ向けた `(偽の GitHub, 一時ディレクトリ, 本体, 作業ツリー1)`。
    `prefix` が `None` なら `.beads` は `init.py` がトラッカーの行から prefix を選んで作る。`verify` は `make_repo` へ渡す。"""
    fake = FakeGitHub()
    with tempfile.TemporaryDirectory() as tmp:
        local.env = _with_fakes(tmp, fake)
        try:
            main_path, wt1, _ = make_repo(
                tmp, extra='tracker = "github"\ngithub_project = "sinnlosses/1"\n', verify=verify, prefix=prefix
            )
            bd(main_path, "config", "set", "github.repository", "o/r")
            yield fake, tmp, main_path, wt1
        finally:
            fake.close()
            del local.env


def status_labels(fake: "FakeGitHub", n: int) -> list[str]:
    return [l for l in fake.issues[n]["labels"] if l.startswith("status::")]
