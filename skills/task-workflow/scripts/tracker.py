"""トラッカー（GitHub・Jira・なし）との橋渡し。Beads 方式でだけ使う。

正典は WORKFLOW.md「Beads 方式」の「トラッカー」と「GitHub との双方向」。錠と本文は Beads が持ち、
トラッカーは写しを持つだけなので、ここの失敗はタスクの操作を止めない（呼ぶ側は
`TRACKER\\tFAILED\\t…` の行を足して先へ進み、`task sync` で打ち直す）。

- `github`: Beads の `issue_prefix` が `gh` なら双方向（取り込みは `bd` の増分に頼らず、番号を選んで
  `bd github pull <番号>…`）、`t` なら Beads から送るだけ。1件の操作はその1件だけを送り、`ship`・`sync` は
  全件を送る。そのあと Project の Status 欄を `gh api graphql` で書く（`bd` は Status 欄を触らない）。
  `bd` へ渡す token は `GITHUB_TOKEN` が無ければ `gh auth token` から取る
- `jira`: `bd jira sync --pull` だけ。Jira には書かない（`--push` と引数なしの `sync` は打たない）。取り込んだ
  振り分け前の課題は `external_ref` の Jira のキーの ID（`proj-123`）へ付け替える
- `なし`: 何もしない

GraphQL の枠（1時間に5000点、アカウント単位）は要求の上限で数えられる。`gh project item-list` は
入れ子の上限で1回約101点かかったので使わない。Project・Status 欄・選択肢の ID と、Issue ごとの
項目 ID・最後に書いた Status は `bd kv` に控え、Status が変わらない課題には触らない。
Issue の読み書き（`bd github push`・`pull` と、取り込む番号を選ぶ一覧）は REST で、GraphQL の枠を食わない。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import beads
import layout

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
JIRA_REF = re.compile(rf"^(?:\S*/browse/)?({layout.JIRA_ID_FRAGMENT})$")

# `bd kv` の控え。値はどれも JSON。
PROJECT_KEY = "task-workflow.project"  # {"project": "<owner>/<番号>", "id", "field", "options": {名前: ID}}
ITEMS_KEY = "task-workflow.project-items"  # {Issue の URL: {"item": 項目 ID, "status": 最後に書いた名前}}
SYNCED_KEY = "task-workflow.github-synced"  # {Issue 番号: その課題を最後に送った・取り込んだ時刻}（CONFLICT の判定）
# 課題ごとのキー `<これ><Issue 番号>`。値は最後に届いた送りの直前に読んだその課題の着手の時刻
# （`started_at`。着手中でなければ空）。この値は JSON でない。
PUSHED_KEY_PREFIX = "task-workflow.github-pushed."
SEEN_KEY = "task-workflow.github-seen"  # GitHub の一覧で見た最後の更新時刻
# `bd github pull` の actor は `<作業ツリーの名前>` にこれを足したもの（取り込みが書いた更新を監査の出来事で見分ける）。
PULL_ACTOR_SUFFIX = ":github-pull"

# 取り込む範囲を、前回見た更新時刻よりこれだけさかのぼる（立てた直後の Issue は一覧にすぐ出ない）。
SEEN_MARGIN = timedelta(hours=1)
# 課題ごとの同期の時刻と GitHub の更新時刻を比べるときの許し（時計のずれと、送った直後の更新）。
SYNC_SLACK = timedelta(seconds=10)
# 控えの無い課題がこれより多ければ、Issue ごとに引かず Project を一巡して控えを埋める（最初の1回）。
BULK_LOOKUP_THRESHOLD = 10


@dataclass(frozen=True)
class Tracker:
    kind: str  # "なし" | "github" | "jira"
    project_owner: str | None = None
    project_number: str | None = None


def read_tracker(toplevel: str) -> Tracker:
    config = layout.read_config(toplevel)
    project = config.project_owner_number
    if config.tracker != "github" or project is None:
        return Tracker(config.tracker)
    return Tracker(config.tracker, project[0], project[1])


def session(toplevel: str) -> "Session":
    return Session(toplevel, read_tracker(toplevel))


class _Failed(RuntimeError):
    """トラッカーに届かなかった（`TRACKER\\tFAILED` の行にする）。"""


class Session:
    """1回の `task` の操作の間のトラッカーとのやりとり。出力はどれも `TRACKER\\t…` の行のリスト。"""

    def __init__(self, toplevel: str, tracker: Tracker) -> None:
        self.toplevel = toplevel
        self.tracker = tracker
        self._actor = os.path.basename(toplevel)
        self._env: dict | None = None
        self._prefix: str | None = None
        self._repo: str | None = None
        self._project: dict | None = None
        self._items: dict | None = None
        self._items_dirty = False
        self._synced: dict | None = None

    @property
    def bidirectional(self) -> bool:
        """GitHub から取り込むか（`issue_prefix` が `gh`）。`t` のあいだは送るだけ。"""
        if self.tracker.kind != "github":
            return False
        if self._prefix is None:
            self._prefix = beads.read_prefix(self.toplevel)
        return self._prefix == beads.PREFIX_GITHUB

    # --- 1件の操作 -------------------------------------------------------------

    def before(self, bd_ids: list[str]) -> list[str]:
        """操作の前に、触る課題を GitHub から取り込む（双方向のときだけ）。"""
        numbers = [int(m.group(1)) for m in (GH_ID.match(i) for i in bd_ids) if m]
        if not self.bidirectional or not numbers:
            return []
        try:
            lines = self._pull({n: self._rest_issue(n) for n in numbers})
            self._save()
        except _Failed as e:
            return [f"TRACKER\tFAILED\tgithub\t{e}"]
        return lines

    def after(self, bd_ids: list[str]) -> list[str]:
        """操作のあと、触った課題だけを送って Status 欄を書く。"""
        if self.tracker.kind == "jira":
            return _jira_pull(self.toplevel)
        if self.tracker.kind != "github":
            return []
        ids = [i for i in bd_ids if not beads.is_provisional(i)]
        try:
            if ids:
                self._push(ids)
                self._mark_synced(ids)
            changed = self._bridge([i for i in (beads.show(self.toplevel, x) for x in ids) if i is not None])
            self._save()
        except _Failed as e:
            return [f"TRACKER\tFAILED\tgithub\t{e}"]
        return [f"TRACKER\tOK\tgithub\tpushed={len(ids)}\tstatus_changed={changed}"]

    def register(self, provisional: str) -> tuple[str, list[str]]:
        """`task new`（双方向のとき）: 仮の ID の課題で Issue を立て、`gh-<Issue 番号>` へ付け替える。

        落ちたら仮の ID のまま返す（`task sync` が送り直して付け替える）。
        """
        try:
            self._push([provisional])
            renamed, lines = self._rename([provisional])
            final = renamed.get(provisional)
            if final is None:
                return provisional, lines + [f"TRACKER\tFAILED\tgithub\t{provisional} に Issue の番号が付かなかった"]
            # 付け替えは Beads の更新なので、送り直して `bd` の前回の同期の時刻を進める（進めないと、次の
            # 取り込みがこの課題を「Beads で変えたもの」として飛ばし、GitHub での変更を送り返して消す）。
            self._push([final])
            self._mark_synced([final])
            issue = beads.show(self.toplevel, final)
            changed = self._bridge([issue] if issue else [])
            self._save()
        except _Failed as e:
            return provisional, [f"TRACKER\tFAILED\tgithub\t{e}"]
        return final, lines + [f"TRACKER\tOK\tgithub\tpushed=1\tstatus_changed={changed}"]

    # --- 全件 ------------------------------------------------------------------

    def sync_all(self, pull: bool) -> list[str]:
        """全件を送る（`ship`）。`pull` なら先に GitHub で変わった課題を取り込む（`task sync`）。"""
        if self.tracker.kind == "jira":
            return _jira_pull(self.toplevel) if pull else []
        if self.tracker.kind != "github":
            return []
        lines: list[str] = []
        try:
            started = _now()
            newest = None
            if pull and self.bidirectional:
                remote, newest = self._rest_changed()
                lines += self._pull(remote)
            before = beads.list_issues(self.toplevel)
            self._bd_ok(["github", "sync", "--push-only"], "bd github sync --push-only")
            if self.bidirectional:
                self._mark_synced([i.bd_id for i in before if _after(i.raw.get("updated_at"), self._synced_at(i))])
                renamed, renamed_lines = self._rename([i.bd_id for i in before if beads.is_provisional(i.bd_id)])
                lines += renamed_lines
                if renamed:
                    self._push(list(renamed.values()))
                    self._mark_synced(list(renamed.values()))
                # `--push-only` がどの課題を送ったかは分からないので、送りの印と違う着手はまとめて送り直す。
                pushed = self._load_pushed()
                unsent = [
                    i.bd_id for i in beads.list_issues(self.toplevel)
                    if (n := _ref_number(i.external_ref)) is not None and _claim_started(i)
                    and pushed.get(n) != _claim_started(i)
                ]
                if unsent:
                    self._push(unsent)
                    self._mark_synced(unsent)
            changed = self._bridge(beads.list_issues(self.toplevel))
            if pull and self.bidirectional:
                beads.run_ok(self.toplevel, ["kv", "set", SEEN_KEY, _format_time(newest or started)])
            self._save()
        except _Failed as e:
            return lines + [f"TRACKER\tFAILED\tgithub\t{e}"]
        return lines + [f"TRACKER\tOK\tgithub\tpushed=all\tstatus_changed={changed}"]

    # --- 取り込み ----------------------------------------------------------------

    def _pull(self, remote: dict[int, dict]) -> list[str]:
        """番号を選んで取り込み、着手の印を戻し、GitHub で閉じた・開き直した課題を片付ける。

        `bd github pull` は前回の同期より後に Beads で変えた課題を飛ばす（両側で変えたら Beads が勝つ）が、
        飛ばしたかを教えないので、課題ごとの同期の時刻より後に両側で更新されたものを `CONFLICT` にする。
        取り込みのあとは必ず送る（`bd` の前回の同期の時刻は取り込みでも進むので、送らずにもう一度
        取り込むと GitHub の版が勝つ）。着手の印を戻せなかった課題は `TRACKER\\tFAILED` の行にして、
        ほかの行と送りは続ける。
        """
        if not remote:
            return []
        old = _by_number(beads.list_issues(self.toplevel))
        lines = []
        for n in sorted(remote):
            local = old.get(n)
            since = self._synced_at(local) if local is not None else None
            if (
                since is not None
                and _after(local.raw.get("updated_at"), since)
                and _after(remote[n].get("updated_at"), since + SYNC_SLACK)
            ):
                lines.append(f"TRACKER\tCONFLICT\t{beads.to_task_id(local.bd_id)}")
        pull_actor = f"{self._actor}{PULL_ACTOR_SUFFIX}"
        pulled_at = _now()
        self._bd_ok(["--actor", pull_actor, "github", "pull", *(str(n) for n in sorted(remote))], "bd github pull")
        new = _by_number(beads.list_issues(self.toplevel))
        # 別の作業ツリーの送りも見るので、この Session の控えではなく今の値を読む。
        pushed = self._load_pushed()
        legacy = _loads(beads.run(self.toplevel, ["kv", "get", SYNCED_KEY]).stdout)
        legacy = legacy if isinstance(legacy, dict) else {}
        fixed: list[str] = []
        for n in sorted(remote):
            issue, was = new.get(n), old.get(n)
            if issue is None:
                continue
            try:
                overwritten = beads.overwritten_by(self.toplevel, issue.bd_id, pull_actor, pulled_at)
            except beads.BeadsError as e:
                lines.append(f"TRACKER\tFAILED\tgithub\t{beads.to_task_id(issue.bd_id)} の着手の印を戻せない: {e}")
                overwritten = None
            restore = [] if overwritten is None else _claim_restore_args(
                overwritten, issue, pushed.get(n), _parse_time(legacy.get(str(n)))
            )
            if restore:
                r = beads.run(self.toplevel, ["update", issue.bd_id, *restore], self._actor)
                if r.returncode == 0:
                    fixed.append(issue.bd_id)
                else:
                    lines.append(f"TRACKER\tFAILED\tgithub\t{beads.to_task_id(issue.bd_id)} の着手の印を戻せない: {_tail(r)}")
            was_closed = was is not None and was.status == "closed"
            if issue.status == "closed" and not was_closed and beads.ship_mark(issue) is None:
                if beads.CANCELLED_LABEL not in issue.labels:
                    beads.run_ok(self.toplevel, ["update", issue.bd_id, "--add-label", beads.CANCELLED_LABEL], self._actor)
                    fixed.append(issue.bd_id)
                lines.append(f"TRACKER\tCLOSED\t{beads.to_task_id(issue.bd_id)}")
            elif was_closed and issue.status != "closed":
                marks = [l for l in issue.labels if l == beads.CANCELLED_LABEL or l in beads.SHIP_LABELS.values()]
                if marks:
                    beads.run_ok(
                        self.toplevel, ["update", issue.bd_id, *[x for m in marks for x in ("--remove-label", m)]],
                        self._actor,
                    )
                    fixed.append(issue.bd_id)
        renamed, rename_lines = self._rename([new[n].bd_id for n in remote if n in new])
        # 取り込みのあとの直し（assignee・label・付け替え）は Beads の更新なので、ここで送って `bd` の前回の
        # 同期の時刻を進める（進めないと、次の取り込みがこの課題を飛ばして GitHub での変更を送り返す）。
        fixed = list(dict.fromkeys([renamed.get(i, i) for i in fixed] + list(renamed.values())))
        if fixed:
            self._push(fixed)
        self._mark_synced([renamed.get(new[n].bd_id, new[n].bd_id) for n in remote if n in new])
        return lines + rename_lines

    def _rename(self, bd_ids: list[str]) -> tuple[dict[str, str], list[str]]:
        """`external_ref` の番号と ID が違う課題を `gh-<番号>` へ付け替える。`{元: 先}` と出力の行を返す。"""
        wanted = set(bd_ids)
        issues = beads.list_issues(self.toplevel)
        taken = {i.bd_id for i in issues}
        renamed: dict[str, str] = {}
        lines: list[str] = []
        for issue in issues:
            n = _ref_number(issue.external_ref)
            if issue.bd_id not in wanted or n is None or beads.is_numbered(issue.bd_id):
                continue
            target = f"gh-{n}"
            if target in taken:
                lines.append(f"TRACKER\tINVALID\t{issue.bd_id}\t{target} が既にある（付け替えない）")
                continue
            r = beads.run(self.toplevel, ["rename", issue.bd_id, target], self._actor)
            if r.returncode != 0:
                lines.append(f"TRACKER\tINVALID\t{issue.bd_id}\tbd rename: {_tail(r)}")
                continue
            taken.add(target)
            renamed[issue.bd_id] = target
        return renamed, lines

    def _rest_issue(self, number: int) -> dict:
        data = self._gh_json(["api", f"repos/{self._repository()}/issues/{number}"], "gh api issues")
        if not isinstance(data, dict):
            raise _Failed(f"gh api issues/{number} の JSON が思った形でない")
        return data

    def _rest_changed(self) -> tuple[dict[int, dict], datetime | None]:
        """前回見た更新時刻（無ければ開いた全件）より後に更新された Issue（PR を除く）と、その最新の時刻。"""
        seen = _parse_time(beads.run(self.toplevel, ["kv", "get", SEEN_KEY]).stdout.strip())
        if seen is None:
            query = "state=open&per_page=100"
        else:
            query = "state=all&per_page=100&since=" + _format_time(seen - SEEN_MARGIN)
        pages = self._gh_json(
            ["api", "--paginate", "--slurp", f"repos/{self._repository()}/issues?{query}"], "gh api issues"
        )
        remote: dict[int, dict] = {}
        for page in pages if isinstance(pages, list) else []:
            for item in page if isinstance(page, list) else []:
                if isinstance(item, dict) and "pull_request" not in item and isinstance(item.get("number"), int):
                    remote[item["number"]] = item
        times = [t for t in (_parse_time(i.get("updated_at")) for i in remote.values()) if t is not None]
        return remote, max(times) if times else None

    # --- Project の Status 欄 --------------------------------------------------------

    def _bridge(self, issues: list[beads.Issue]) -> int:
        """Status 欄を Beads の状態に揃える。最後に書いた Status と同じ課題には触らない。書いた件数を返す。"""
        items = self._load_items()
        todo: list[tuple[str, str]] = []
        for issue in issues:
            url = _issue_url(issue.external_ref, self._repository_or_none())
            want = STATUS_OPTIONS.get(beads.workflow_state(issue))
            if url is not None and want is not None and (items.get(url) or {}).get("status") != want:
                todo.append((url, want))
        if not todo:
            return 0
        if sum(1 for url, _ in todo if not (items.get(url) or {}).get("item")) > BULK_LOOKUP_THRESHOLD:
            self._fill_items_from_project()
            todo = [(url, want) for url, want in todo if (items.get(url) or {}).get("status") != want]
        for url, want in todo:
            self._write_status(url, want)
        return len(todo)

    def _write_status(self, url: str, want: str) -> None:
        items = self._load_items()
        item_id = (items.get(url) or {}).get("item") or self._find_item(url)
        try:
            self._set_field(self._load_project(), item_id, want)
        except _Failed:
            # 控えが古い（欄・選択肢を作り直したか、Project から項目を外した）。1回だけ読み直す。
            self._project = self._fetch_project()
            item_id = self._find_item(url)
            self._set_field(self._project, item_id, want)
        items[url] = {"item": item_id, "status": want}
        self._items_dirty = True

    def _set_field(self, project: dict, item_id: str, want: str) -> None:
        option = project["options"].get(want)
        if option is None:
            raise _Failed(f"Status 欄に選択肢 {want} が無い")
        self._graphql(SET_STATUS, {"project": project["id"], "item": item_id, "field": project["field"], "option": option})

    def _find_item(self, url: str) -> str:
        """Issue 側から、この Project の項目を引く（無ければ足す）。"""
        m = ISSUE_URL.match(url)
        if m is None:
            raise _Failed(f"Issue の URL でない: {url}")
        project = self._load_project()
        data = self._graphql(ISSUE_ITEMS, {"owner": m.group(1), "repo": m.group(2), "number": int(m.group(3))})
        issue = (data.get("repository") or {}).get("issue") or {}
        if not issue.get("id"):
            raise _Failed(f"Issue が見つからない: {url}")
        for node in (issue.get("projectItems") or {}).get("nodes") or []:
            if ((node or {}).get("project") or {}).get("id") == project["id"]:
                return str(node["id"])
        added = self._graphql(ADD_ITEM, {"project": project["id"], "content": issue["id"]})
        item = (added.get("addProjectV2ItemById") or {}).get("item") or {}
        if not item.get("id"):
            raise _Failed(f"Project に足せなかった: {url}")
        return str(item["id"])

    def _fill_items_from_project(self) -> None:
        """Project の項目を一巡して、項目 ID と今の Status を控える（要る欄だけを取る）。"""
        items = self._load_items()
        project = self._load_project()
        after = None
        while True:
            data = self._graphql(PROJECT_ITEMS, {"project": project["id"], "after": after})
            page = (data.get("node") or {}).get("items") or {}
            for node in page.get("nodes") or []:
                url = ((node or {}).get("content") or {}).get("url")
                if url:
                    status = (node.get("fieldValueByName") or {}).get("name")
                    items[url] = {"item": str(node["id"]), "status": status}
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        self._items_dirty = True

    def _load_project(self) -> dict:
        if self._project is None:
            key = f"{self.tracker.project_owner}/{self.tracker.project_number}"
            cached = _loads(beads.run(self.toplevel, ["kv", "get", PROJECT_KEY]).stdout)
            if isinstance(cached, dict) and cached.get("project") == key and cached.get("id") and cached.get("field"):
                self._project = cached
            else:
                self._project = self._fetch_project()
        return self._project

    def _fetch_project(self) -> dict:
        owner, number = self.tracker.project_owner or "", int(self.tracker.project_number or 0)
        data = self._graphql(PROJECT_FIELDS, {"owner": owner, "number": number})
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
        beads.run_ok(self.toplevel, ["kv", "set", PROJECT_KEY, json.dumps(value, ensure_ascii=False)])
        return value

    def _load_items(self) -> dict:
        if self._items is None:
            cached = _loads(beads.run(self.toplevel, ["kv", "get", ITEMS_KEY]).stdout)
            self._items = cached if isinstance(cached, dict) else {}
        return self._items

    # --- 課題ごとの同期の時刻（CONFLICT の判定） ---------------------------------------

    def _load_synced(self) -> dict:
        if self._synced is None:
            cached = _loads(beads.run(self.toplevel, ["kv", "get", SYNCED_KEY]).stdout)
            self._synced = cached if isinstance(cached, dict) else {}
        return self._synced

    def _synced_at(self, issue: beads.Issue) -> datetime | None:
        n = _ref_number(issue.external_ref)
        return _parse_time(self._load_synced().get(str(n))) if n is not None else None

    def _mark_synced(self, bd_ids: list[str]) -> None:
        """送った・取り込んだ時刻を控え、その場で `bd kv` へ書く（あとの Status 欄の書き込みが落ちても残す）。

        書く直前に今の値を読み、触った番号だけを重ねる（別の作業ツリーが書いた時刻を古い値で戻さない）。
        """
        if not self.bidirectional or not bd_ids:
            return
        wanted = set(bd_ids)
        now = _format_time(_now())
        numbers = [
            str(n) for i in beads.list_issues(self.toplevel)
            if i.bd_id in wanted and (n := _ref_number(i.external_ref)) is not None
        ]
        if not numbers:
            return
        current = _loads(beads.run(self.toplevel, ["kv", "get", SYNCED_KEY]).stdout)
        synced = {**(current if isinstance(current, dict) else {}), **{n: now for n in numbers}}
        beads.run_ok(self.toplevel, ["kv", "set", SYNCED_KEY, json.dumps(synced)])
        self._synced = synced

    # --- 課題ごとの送りの印（着手を戻すかの判定） ---------------------------------------

    def _push(self, bd_ids: list[str]) -> None:
        """`bd github push` で送り、届いたら課題ごとに送った着手の時刻を `bd kv` へ書く（変わったものだけ）。"""
        sent = {i: _claim_started(beads.show(self.toplevel, i)) for i in bd_ids}
        self._bd_ok(["github", "push", *bd_ids], "bd github push")
        if not self.bidirectional:
            return
        pushed = self._load_pushed()
        for issue in beads.list_issues(self.toplevel):
            n = _ref_number(issue.external_ref)
            if issue.bd_id in sent and n is not None and pushed.get(n) != sent[issue.bd_id]:
                beads.run_ok(self.toplevel, ["kv", "set", f"{PUSHED_KEY_PREFIX}{n}", sent[issue.bd_id]])

    def _load_pushed(self) -> dict[int, str]:
        """課題ごとの送りの印の今の値（`{Issue 番号: 送った着手の時刻}`。印の無い課題は入らない）。"""
        listed = _loads(beads.run(self.toplevel, ["kv", "list", "--json"]).stdout)
        if not isinstance(listed, dict):
            return {}
        return {
            int(k[len(PUSHED_KEY_PREFIX):]): v for k, v in listed.items()
            if k.startswith(PUSHED_KEY_PREFIX) and k[len(PUSHED_KEY_PREFIX):].isdigit() and isinstance(v, str)
        }

    def _save(self) -> None:
        if self._items_dirty and self._items is not None:
            beads.run_ok(self.toplevel, ["kv", "set", ITEMS_KEY, json.dumps(self._items, ensure_ascii=False)])
            self._items_dirty = False

    # --- 呼び出し ------------------------------------------------------------------

    def _bd_ok(self, args: list[str], label: str) -> str:
        if self._env is None:
            env = os.environ.copy()
            if not env.get("GITHUB_TOKEN"):
                token = _gh(self.toplevel, ["auth", "token"])
                if token.returncode != 0 or not token.stdout.strip():
                    raise _Failed(f"gh auth token: {_tail(token)}")
                env["GITHUB_TOKEN"] = token.stdout.strip()
            self._env = env
        r = _bd(self.toplevel, [*args, "--json"], self._env)
        if r.returncode != 0:
            raise _Failed(f"{label}: {_tail(r)}")
        # `bd github` は GitHub に届かなくても終了コード0で、失敗は `stats.errors` と `warnings`（`--json` を
        # 受けない全件の `sync --push-only` では `Warning: Failed …` の行）にだけ出る。
        report = _loads(r.stdout[r.stdout.find("{"):]) if r.stdout.lstrip().startswith("{") else None
        if isinstance(report, dict):
            if (report.get("stats") or {}).get("errors") or report.get("success") is False:
                warnings = report.get("warnings") or [report.get("error") or "errors>0"]
                raise _Failed(f"{label}: {str(warnings[0]).replace(chr(9), ' ')[:300]}")
            return r.stdout
        failed = [l for l in (r.stderr + r.stdout).splitlines() if l.startswith("Warning: Failed")]
        if failed:
            raise _Failed(f"{label}: {failed[0].replace(chr(9), ' ')[:300]}")
        return r.stdout

    def _repository_or_none(self) -> str | None:
        if self._repo is None:
            self._repo = _default_repo(self.toplevel) or ""
        return self._repo or None

    def _repository(self) -> str:
        repo = self._repository_or_none()
        if repo is None:
            raise _Failed("bd config の github.repository が無い")
        return repo

    def _gh_json(self, args: list[str], label: str):
        r = _gh(self.toplevel, args)
        if r.returncode != 0:
            raise _Failed(f"{label}: {_tail(r)}")
        try:
            return json.loads(r.stdout)
        except ValueError as e:
            raise _Failed(f"{label} の JSON が読めない: {e}") from e

    def _graphql(self, query: str, variables: dict) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            if v is not None:
                args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
        data = self._gh_json(args, "gh api graphql")
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


# --- Jira --------------------------------------------------------------------------


def _jira_pull(toplevel: str) -> list[str]:
    r = _bd(toplevel, ["jira", "sync", "--pull"], os.environ.copy())
    if r.returncode != 0:
        return [f"TRACKER\tFAILED\tjira\tbd jira sync --pull: {_tail(r)}"]
    _renamed, lines = jira_rename(toplevel)
    return lines + ["TRACKER\tOK\tjira\tpulled"]


def jira_key(issue: beads.Issue) -> str | None:
    """`external_ref`（`https://<site>/browse/PROJ-123` かキーそのもの）の Jira のキーの Beads の ID（`proj-123`）。"""
    m = JIRA_REF.match((issue.external_ref or "").strip())
    return beads.to_bd_id(m.group(1)) if m else None


def jira_rename(toplevel: str, bd_ids: list[str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Jira のキーのある課題を `external_ref` のキーの ID へ付け替える。`{元: 先}` と出力の行を返す。

    `bd_ids` が無ければ振り分け前（`difficulty`・`loopable` の label が無い）の課題すべて。
    """
    issues = beads.list_issues(toplevel)
    taken = {i.bd_id for i in issues}
    renamed: dict[str, str] = {}
    lines: list[str] = []
    for issue in issues:
        target = jira_key(issue)
        if target is None or target == issue.bd_id:
            continue
        if bd_ids is None:
            if any(l.startswith((beads.DIFFICULTY_LABEL, beads.LOOPABLE_LABEL)) for l in issue.labels):
                continue
        elif issue.bd_id not in bd_ids:
            continue
        shown = beads.to_task_id(issue.bd_id)
        if target in taken:
            lines.append(f"TRACKER\tINVALID\t{shown}\t{beads.to_task_id(target)} が既にある（付け替えない）")
            continue
        r = beads.run(toplevel, ["rename", issue.bd_id, target], os.path.basename(toplevel))
        if r.returncode != 0:
            lines.append(f"TRACKER\tINVALID\t{shown}\tbd rename: {_tail(r)}")
            continue
        taken.add(target)
        renamed[issue.bd_id] = target
    return renamed, lines


# --- 小道具 ---------------------------------------------------------------------------


def _by_number(issues: list[beads.Issue]) -> dict[int, beads.Issue]:
    return {n: i for i in issues if (n := _ref_number(i.external_ref)) is not None}


def _claim_restore_args(
    was: beads.Issue, issue: beads.Issue, pushed_claim: str | None, legacy_synced_at: datetime | None
) -> list[str]:
    """取り込みが `was` を `issue` へ上書きして消した着手の印（status・assignee と `beads.MARK_KEYS` の
    metadata）を戻す `bd update` の引数。戻すものが無ければ空。

    `pushed_claim` はこの課題の送りの印（最後に届いた送りが運んだ着手の時刻。空なら着手なし）。印の無い
    課題（印を書く前に送ったもの）は、`legacy_synced_at`（最後に送った・取り込んだ時刻）が着手より前なら
    送りが届いていないとみなす。

    `was` は取り込みの前に読んだ一覧ではなく、取り込みが書く直前の版
    （取り込みの最中に別の作業ツリーが着手すると、前に読んだ一覧にはその着手が無い）。
    GitHub で手放された着手（担当者が付いた、または着手を送ったあとで label `status::in_progress` が
    外れて `open` に戻った）は戻さない。着手を送る前の GitHub の版で `open` に戻ったものは戻す。
    """
    if issue.status == "closed":
        return []
    args: list[str] = []
    claimed = was.status == "in_progress" and bool(was.assignee)
    if claimed and not issue.assignee:
        started = beads.parse_time(was.started_at)
        if pushed_claim is not None:
            sent = beads.parse_time(pushed_claim)
            unsent = started is not None and (
                sent is None or sent.replace(microsecond=0) != started.replace(microsecond=0)
            )
        else:
            unsent = started is not None and (
                legacy_synced_at is None or legacy_synced_at < started.replace(microsecond=0)
            )
        if issue.status == "in_progress":
            args += ["--assignee", was.assignee]
        elif issue.status == "open" and unsent:
            args += ["--status", "in_progress", "--assignee", was.assignee]
    had, has = _marks(was), _marks(issue)
    args += [x for k, v in had.items() if k not in has for x in ("--set-metadata", f"{k}={v}")]
    return args


def _claim_started(issue: beads.Issue | None) -> str:
    """送りの印に書く値: 着手中（`in_progress` で担当者あり）ならその `started_at`、ほかは空。"""
    if issue is None or issue.status != "in_progress" or not issue.assignee:
        return ""
    return issue.started_at or ""


def _marks(issue: beads.Issue) -> dict[str, str]:
    metadata = issue.raw.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    return {k: str(metadata[k]) for k in beads.MARK_KEYS if metadata.get(k)}


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


def _now() -> datetime:
    return datetime.fromtimestamp(time.time(), timezone.utc).replace(microsecond=0)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _format_time(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(value, since: datetime | None) -> bool:
    """`value`（ISO の文字列）が `since` より後か。`since` が無ければ真（まだ一度も同期していない）。"""
    if since is None:
        return True
    at = _parse_time(value)
    return at is not None and at > since


def _loads(text: str):
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return None


def _bd(toplevel: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    beads.forget_reads()
    try:
        return subprocess.run([beads.BD, *args], cwd=toplevel, capture_output=True, text=True, env=env)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))


def _gh(toplevel: str, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args], cwd=toplevel, capture_output=True, text=True)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))


def _tail(r: subprocess.CompletedProcess) -> str:
    text = ((r.stderr or "") + (r.stdout or "")).strip().replace("\t", " ")
    return " / ".join(text.splitlines()[-3:]) or f"終了コード {r.returncode}"
