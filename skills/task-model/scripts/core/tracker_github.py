"""GitHub の REST・GraphQL と Project の Status 欄の読み書き（`tracker.Session` が呼ぶ）。

状態（Project・項目の控え・期限）は `Session` が持ち、ここの関数は必要な値を引数で受けて結果を返す。
`remaining` は呼ぶたびに操作の残りの秒数（`None` は上限なし）を返し、尽きていれば `_Failed` にする。
`gh` を打つたびに呼び直す。失敗は `_Failed`。
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable

import beads

Remaining = Callable[[], "float | None"]

# Project の Status 欄の選択肢の名前（`beads.workflow_state` → 欄の値）。
STATUS_OPTIONS = {
    "hold": "Pending",
    "open": "Todo",
    "in_progress": "In progress",
    "done": "Done",
    "cancelled": "Cancel",
}
ISSUE_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$")
ISSUE_NUMBER = re.compile(r"^(?:gh-|#)?(\d+)$")
GH_ID = re.compile(r"^gh-(\d+)$")

TIMED_OUT = 124

# `bd kv` の控え。値はどれも JSON。
PROJECT_KEY = "task-workflow.project"  # {"project": "<owner>/<番号>", "id", "field", "options": {名前: ID}}
# 課題ごとのキー `<これ><Issue 番号>`。値は {"item": 項目 ID, "status": 最後に書いた名前}。
ITEMS_KEY_PREFIX = "task-workflow.project-item."
# 全課題を1つの JSON に入れていた旧い控え。1値の上限を超えるので、最初の読み込みで課題ごとのキーへ移して消す。
LEGACY_ITEMS_KEY = "task-workflow.project-items"

# 控えの無い課題がこれより多ければ、Issue ごとに引かず Project を一巡して控えを埋める（最初の1回）。
BULK_LOOKUP_THRESHOLD = 10


class _Failed(RuntimeError):
    """トラッカーに届かなかった（`TRACKER\\tFAILED` の行にする）。"""


# --- REST --------------------------------------------------------------------------


def rest_issue(toplevel: str, repo: str, number: int, remaining: Remaining) -> dict:
    data = gh_json(toplevel, ["api", f"repos/{repo}/issues/{number}"], "gh api issues", remaining)
    if not isinstance(data, dict):
        raise _Failed(f"gh api issues/{number} の JSON が思った形でない")
    return data


def rest_issues(toplevel: str, repo: str, query: str, remaining: Remaining) -> dict[int, dict]:
    """`repos/<repo>/issues?<query>` の Issue（PR を除く）を番号で引く。"""
    pages = gh_json(toplevel, ["api", "--paginate", "--slurp", f"repos/{repo}/issues?{query}"], "gh api issues", remaining)
    remote: dict[int, dict] = {}
    for page in pages if isinstance(pages, list) else []:
        for item in page if isinstance(page, list) else []:
            if isinstance(item, dict) and "pull_request" not in item and isinstance(item.get("number"), int):
                remote[item["number"]] = item
    return remote


# --- Project の Status 欄 ------------------------------------------------------------


def write_status(
    toplevel: str, tracker, project: dict, items: dict, dirty: set[str], url: str, want: str, remaining: Remaining
) -> None:
    """Issue の Status 欄を `want` に書き、控え（`items`・`dirty`）を更新する。控えが古ければ `project` を読み直した版に差し替える。"""
    item_id = (items.get(url) or {}).get("item") or find_item(toplevel, project, url, remaining)
    try:
        set_field(toplevel, project, item_id, want, remaining)
    except _Failed:
        # 控えが古い（欄・選択肢を作り直したか、Project から項目を外した）。1回だけ読み直す。
        fresh = fetch_project(toplevel, tracker, remaining)
        project.clear()
        project.update(fresh)
        item_id = find_item(toplevel, project, url, remaining)
        set_field(toplevel, project, item_id, want, remaining)
    items[url] = {"item": item_id, "status": want}
    dirty.add(url)


def set_field(toplevel: str, project: dict, item_id: str, want: str, remaining: Remaining) -> None:
    option = project["options"].get(want)
    if option is None:
        raise _Failed(f"Status 欄に選択肢 {want} が無い")
    variables = {"project": project["id"], "item": item_id, "field": project["field"], "option": option}
    graphql(toplevel, SET_STATUS, variables, remaining)


def find_item(toplevel: str, project: dict, url: str, remaining: Remaining) -> str:
    """Issue 側から、この Project の項目を引く（無ければ足す）。"""
    m = ISSUE_URL.match(url)
    if m is None:
        raise _Failed(f"Issue の URL でない: {url}")
    data = graphql(toplevel, ISSUE_ITEMS, {"owner": m.group(1), "repo": m.group(2), "number": int(m.group(3))}, remaining)
    issue = (data.get("repository") or {}).get("issue") or {}
    if not issue.get("id"):
        raise _Failed(f"Issue が見つからない: {url}")
    for node in (issue.get("projectItems") or {}).get("nodes") or []:
        if ((node or {}).get("project") or {}).get("id") == project["id"]:
            return str(node["id"])
    added = graphql(toplevel, ADD_ITEM, {"project": project["id"], "content": issue["id"]}, remaining)
    item = (added.get("addProjectV2ItemById") or {}).get("item") or {}
    if not item.get("id"):
        raise _Failed(f"Project に足せなかった: {url}")
    return str(item["id"])


def fill_items(toplevel: str, project: dict, items: dict, dirty: set[str], repo: str, remaining: Remaining) -> None:
    """Project の項目を一巡して、項目 ID と今の Status を控える（要る欄だけを取る）。"""
    prefix = f"https://github.com/{repo}/issues/"
    after = None
    while True:
        data = graphql(toplevel, PROJECT_ITEMS, {"project": project["id"], "after": after}, remaining)
        page = (data.get("node") or {}).get("items") or {}
        for node in page.get("nodes") or []:
            url = ((node or {}).get("content") or {}).get("url")
            if url and url.startswith(prefix):
                status = (node.get("fieldValueByName") or {}).get("name")
                fresh = {"item": str(node["id"]), "status": status}
                if items.get(url) != fresh:
                    items[url] = fresh
                    dirty.add(url)
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")


def load_project(toplevel: str, tracker, remaining: Remaining) -> dict:
    """`bd kv` の控えの Project（設定の Project と合うもの）。無ければ GitHub から引く。"""
    key = f"{tracker.project_owner}/{tracker.project_number}"
    cached = _loads(beads.run(toplevel, ["kv", "get", PROJECT_KEY]).stdout)
    if isinstance(cached, dict) and cached.get("project") == key and cached.get("id") and cached.get("field"):
        return cached
    return fetch_project(toplevel, tracker, remaining)


def fetch_project(toplevel: str, tracker, remaining: Remaining) -> dict:
    owner, number = tracker.project_owner or "", int(tracker.project_number or 0)
    data = graphql(toplevel, PROJECT_FIELDS, {"owner": owner, "number": number}, remaining)
    project = (data.get("repositoryOwner") or {}).get("projectV2") or {}
    status = project.get("field") or {}
    if not project.get("id"):
        raise _Failed(f"Project {owner}/{number} が見つからない")
    if not status.get("id"):
        raise _Failed("Project に Status 欄が無い")
    options = {o.get("name"): o.get("id") for o in status.get("options") or []}
    missing = [n for n in STATUS_OPTIONS.values() if n not in options]
    if missing:
        raise _Failed("Status 欄に選択肢が無い: " + ", ".join(missing))
    value = {"project": f"{owner}/{number}", "id": project["id"], "field": status["id"], "options": options}
    beads.run_ok(toplevel, ["kv", "set", PROJECT_KEY, json.dumps(value, ensure_ascii=False)])
    return value


def load_items(listed: dict, repo: str | None) -> dict:
    """`bd kv list` の課題ごとの控えから `{Issue の URL: {"item", "status"}}` を作る。"""
    items: dict = {}
    for key, raw in listed.items():
        suffix = key[len(ITEMS_KEY_PREFIX):]
        value = _loads(raw) if isinstance(raw, str) else None
        if key.startswith(ITEMS_KEY_PREFIX) and suffix.isdigit() and isinstance(value, dict):
            items[items_url(repo, int(suffix))] = value
    return items


def items_url(repo: str | None, number: int) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def migrate_legacy_items(toplevel: str, items: dict, dirty: set[str], raw: object, repo: str | None) -> None:
    legacy = _loads(raw) if isinstance(raw, str) else None
    if not isinstance(legacy, dict) or repo is None:
        return
    for url, value in legacy.items():
        n = _ref_number(url)
        if n is not None and isinstance(value, dict) and items_url(repo, n) == url and url not in items:
            items[url] = value
            dirty.add(url)
    save_items(toplevel, items, dirty)
    beads.run_ok(toplevel, ["kv", "clear", LEGACY_ITEMS_KEY])


def save_items(toplevel: str, items: dict, dirty: set[str]) -> None:
    for url in sorted(dirty):
        n = _ref_number(url)
        if n is not None and url in items:
            beads.run_ok(toplevel, ["kv", "set", f"{ITEMS_KEY_PREFIX}{n}", json.dumps(items[url], ensure_ascii=False)])
    dirty.clear()


# --- gh の呼び出し -------------------------------------------------------------------


def gh_json(toplevel: str, args: list[str], label: str, remaining: Remaining):
    r = _gh(toplevel, args, remaining())
    if r.returncode != 0:
        raise _Failed(f"{label}: {_tail(r)}")
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise _Failed(f"{label} の JSON が読めない: {e}") from e


def graphql(toplevel: str, query: str, variables: dict, remaining: Remaining) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    data = gh_json(toplevel, args, "gh api graphql", remaining)
    if not isinstance(data, dict) or data.get("errors"):
        errors = data.get("errors") if isinstance(data, dict) else data
        raise _Failed(f"gh api graphql: {json.dumps(errors, ensure_ascii=False)[:300]}")
    return data.get("data") or {}


# --- GraphQL の問い合わせ（要る欄だけ。入れ子の接続を重ねないので点数が上限で膨らまない） -------------

PROJECT_FIELDS = """query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
      }
    }
  }
}"""

ISSUE_ITEMS = """query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) { id projectItems(first: 10) { nodes { id project { id } } } }
  }
}"""

PROJECT_ITEMS = """query($project: ID!, $after: String) {
  node(id: $project) {
    ... on ProjectV2 {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { url } }
          fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        }
      }
    }
  }
}"""

ADD_ITEM = """mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}"""

SET_STATUS = """mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}
  ) { projectV2Item { id } }
}"""


# --- 小道具 ---------------------------------------------------------------------------


def _ref_number(external_ref: str | None) -> int | None:
    m = ISSUE_URL.match((external_ref or "").strip())
    return int(m.group(3)) if m else None


def _issue_url(external_ref: str | None, default_repo: str | None) -> str | None:
    if not external_ref:
        return None
    ref = external_ref.strip()
    if ISSUE_URL.match(ref):
        return ref
    m = ISSUE_NUMBER.match(ref)
    if m and default_repo:
        return f"https://github.com/{default_repo}/issues/{m.group(1)}"
    return None


def _default_repo(toplevel: str) -> str | None:
    r = beads.run(toplevel, ["config", "get", "github.repository"])
    value = r.stdout.strip()
    if r.returncode == 0 and "/" in value and " " not in value:
        return value
    owner = beads.run(toplevel, ["config", "get", "github.owner"]).stdout.strip()
    repo = beads.run(toplevel, ["config", "get", "github.repo"]).stdout.strip()
    if owner and repo and " " not in owner + repo:
        return f"{owner}/{repo}"
    return None


def _loads(text: str):
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return None


def _bd(toplevel: str, args: list[str], env: dict[str, str], timeout: float | None = None) -> subprocess.CompletedProcess:
    beads.forget_reads()
    try:
        return subprocess.run([beads.BD, *args], cwd=toplevel, capture_output=True, text=True, env=env, timeout=timeout)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, TIMED_OUT, "", f"{timeout:.0f} 秒の上限を超えた")


def _gh(toplevel: str, args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args], cwd=toplevel, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, TIMED_OUT, "", f"{timeout:.0f} 秒の上限を超えた")


def _tail(r: subprocess.CompletedProcess) -> str:
    text = ((r.stderr or "") + (r.stdout or "")).strip().replace("\t", " ")
    return " / ".join(text.splitlines()[-3:]) or f"終了コード {r.returncode}"
