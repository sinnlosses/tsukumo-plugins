"""トラッカー（GitHub・Jira・なし）との橋渡し。

正典は TRACKER.md の「トラッカー」と「GitHub との双方向」。錠と本文は Beads が持ち、
トラッカーは写しを持つだけなので、ここの失敗はタスクの操作を止めない（呼ぶ側は
`TRACKER\\tFAILED\\t…` の行を足して先へ進み、`task sync` で打ち直す）。

- `github`: Beads の `issue_prefix` が `gh` なら双方向（取り込みは `bd` の増分に頼らず、番号を選んで
  `bd github pull <番号>…`）、`t` なら Beads から送るだけ。書くのは `new` と、`ship`・`sync` の全件の送りだけ。そのあと Project の Status 欄を `gh api graphql` で書く（`bd` は Status 欄を触らない）。
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

import os
import re
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import beads
import layout
import tracker_github as gh
from tracker_github import (
    BULK_LOOKUP_THRESHOLD,
    GH_ID,
    LEGACY_ITEMS_KEY,
    STATUS_OPTIONS,
    _bd,
    _Failed,
    _gh,
    _issue_url,
    _loads,
    _ref_number,
    _repo_of,
    _tail,
)
from tracker_claims import (
    PUSHED_KEY_PREFIX,
    SYNCED_ITEM_PREFIX,
    _claim_restore_args,
    _claim_started,
    _pushed_of,
    _restore_from_snapshots,
    _synced_of,
)

JIRA_REF = re.compile(rf"^(?:\S*/browse/)?({layout.JIRA_ID_FRAGMENT})$")

# 1件の操作（before・after・register）の始めから数えた反映の上限（秒）。
OP_TIMEOUT = 25.0
SHOW_LIMIT = 3
OP_TIMEOUT_ENV = "TASK_WORKFLOW_TRACKER_TIMEOUT"

SEEN_KEY = "task-workflow.github-seen"  # GitHub の一覧で見た最後の更新時刻
# `bd github pull` の actor は `<作業ツリーの名前>` にこれと `:<pid>-<time_ns>` を足したもの（その取り込みが書いた
# 更新だけを監査の出来事で見分ける）。
PULL_ACTOR_SUFFIX = ":github-pull"

# 取り込む範囲を、前回見た更新時刻よりこれだけさかのぼる（立てた直後の Issue は一覧にすぐ出ない）。
SEEN_MARGIN = timedelta(hours=1)
# 課題ごとの同期の時刻と GitHub の更新時刻を比べるときの許し（時計のずれと、送った直後の更新）。
SYNC_SLACK = timedelta(seconds=10)


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


def restore_claims(toplevel: str, bd_ids: list[str], actor: str) -> list[str]:
    """着手の印の控え（`beads.write_claim_snapshot`）から、取り込みが消したまま残った着手の印を Beads へ戻し、出力の行を返す。

    取り込みの無い（双方向でない）ときは何もしない。GitHub には触らない（戻した課題は次の送りで送られる）。
    """
    if not session(toplevel).bidirectional:
        return []
    return _restore_from_snapshots(toplevel, beads.read_kv(toplevel), lambda _wanted: beads.list_issues(toplevel), bd_ids, actor)[0]


class Session:
    """1回の `task` の操作の間のトラッカーとのやりとり。出力はどれも `TRACKER\\t…` の行のリスト。"""

    def __init__(self, toplevel: str, tracker: Tracker) -> None:
        self.toplevel = toplevel
        self.tracker = tracker
        self._actor = os.path.basename(toplevel)
        self._env: dict | None = None
        self._config: dict | None = None
        self._prefix: str | None = None
        self._repo: str | None = None
        self._project: dict | None = None
        self._items: dict | None = None
        self._items_dirty: set[str] = set()
        self._synced: dict | None = None
        self._listed: list[beads.Issue] | None = None
        self._listed_at = -1
        self._kv_all: dict | None = None
        self._deadline: float | None = None
        # 経過を1行ずつ受ける先（`tw sync` が stderr へ出す）。無ければ何も出さない。
        self.progress: Callable[[str], None] | None = None

    def _say(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    @property
    def bidirectional(self) -> bool:
        """GitHub から取り込むか（`issue_prefix` が `gh`）。`t` のあいだは送るだけ。"""
        if self.tracker.kind != "github":
            return False
        if self._prefix is None:
            self._prefix = beads.prefix_of(self._bd_config())
        return self._prefix == beads.PREFIX_GITHUB

    def _bd_config(self) -> dict:
        if self._config is None:
            self._config = beads.read_bd_config(self.toplevel)
        return self._config

    # --- 1件の操作 -------------------------------------------------------------

    def before(self, bd_ids: list[str]) -> list[str]:
        """操作の前に、触る課題を GitHub から取り込む（双方向のときだけ）。"""
        self._begin(time.monotonic() + _op_timeout())
        numbers = [int(m.group(1)) for m in (GH_ID.match(i) for i in bd_ids) if m]
        if not self.bidirectional or not numbers:
            return []
        try:
            lines = self._pull({n: gh.rest_issue(self.toplevel, self._repository(), n, self._remaining) for n in numbers})
            self._save()
        except _Failed as e:
            return [f"TRACKER\tFAILED\tgithub\t{e}"]
        return lines

    def after(self, bd_ids: list[str]) -> list[str]:
        """操作のあと。jira は取り込むだけで、github・なしは何も書かない（送りは `push_created` と `sync_all`）。"""
        self._begin(time.monotonic() + _op_timeout())
        if self.tracker.kind == "jira":
            return _jira_pull(self.toplevel)
        return []

    def push_created(self, bd_ids: list[str]) -> list[str]:
        """`new`（`t` 接頭辞）のあと、作った課題だけを送って Status 欄を書く。"""
        self._begin(time.monotonic() + _op_timeout())
        if self.tracker.kind != "github":
            return self.after(bd_ids)
        ids = [i for i in bd_ids if not beads.is_provisional(i)]
        try:
            if ids:
                self._push(ids)
                self._mark_synced(ids)
            changed = self._bridge([i for i in (self._issue(x) for x in ids) if i is not None])
            self._save()
        except _Failed as e:
            return [f"TRACKER\tFAILED\tgithub\t{e}"]
        return [f"TRACKER\tOK\tgithub\tpushed={len(ids)}\tstatus_changed={changed}"]

    def register(self, provisional: str) -> tuple[str, list[str]]:
        """`task new`（双方向のとき）: 仮の ID の課題で Issue を立て、`gh-<Issue 番号>` へ付け替える。

        落ちたら仮の ID のまま返す（`task sync` が送り直して付け替える）。
        """
        self._begin(time.monotonic() + _op_timeout())
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
            issue = self._issue(final)
            changed = self._bridge([issue] if issue else [])
            self._save()
        except _Failed as e:
            return provisional, [f"TRACKER\tFAILED\tgithub\t{e}"]
        return final, lines + [f"TRACKER\tOK\tgithub\tpushed=1\tstatus_changed={changed}"]

    def register_many(self, provisionals: list[str]) -> tuple[dict[str, str], list[str]]:
        """`task new --batch`（双方向のとき）: 仮の ID の課題をまとめて送り、`gh-<Issue 番号>` へ付け替える。`{仮: 最終}` と行を返す。

        `register` を1件ずつ打つのと同じ結果を、送り・付け替え・Status 欄の書き込みを各1回にして得る。
        付け替えられなかった課題は `{仮: 仮}` のまま返し（`task sync` が送り直す）、行に `FAILED` を足す。
        """
        self._begin(time.monotonic() + _op_timeout())
        if not provisionals:
            return {}, []
        try:
            self._push(provisionals)
            renamed, lines = self._rename(provisionals)
            finals = {p: renamed.get(p, p) for p in provisionals}
            done = [f for p, f in finals.items() if p in renamed]
            if done:
                self._push(done)
                self._mark_synced(done)
            sent = [i for i in (self._issue(x, many=True) for x in done) if i is not None]
            changed = self._bridge(sent, self._content_ids(sent))
            self._save()
        except _Failed as e:
            return {p: p for p in provisionals}, [f"TRACKER\tFAILED\tgithub\t{e}"]
        lost = [p for p in provisionals if p not in renamed]
        lines = lines + [f"TRACKER\tFAILED\tgithub\t{p} に Issue の番号が付かなかった" for p in lost]
        return finals, lines + [f"TRACKER\tOK\tgithub\tpushed={len(done)}\tstatus_changed={changed}"]

    # --- 全件 ------------------------------------------------------------------

    def sync_all(self, pull: bool) -> list[str]:
        """全件を送る（`ship`）。`pull` なら先に GitHub で変わった課題を取り込む（`task sync`）。"""
        if self.tracker.kind == "jira":
            return _jira_pull(self.toplevel) if pull else []
        if self.tracker.kind != "github":
            return []
        self._begin(None)
        lines: list[str] = []
        try:
            started = _now()
            newest = None
            # 取り込みは送った時刻を進める（Beads が勝って飛ばした課題も）ので、送る課題は取り込みの前に選ぶ。
            pushed_count = "all"
            if not self.bidirectional:
                self._say("GitHub へ全件を送る（bd github sync --push-only）")
                self._bd_ok(["github", "sync", "--push-only"], "bd github sync --push-only")
            else:
                # GitHub の一覧と Beads の読みは互いに頼らないので、一覧を先に走らせて待ちを重ねる。
                # 走らせる前に `bd` を打つ読み（前回見た時刻・リポジトリ名）を済ませる（スレッドでは `gh` だけを打つ）。
                fetch = None
                with ThreadPoolExecutor(max_workers=1) as pool:
                    if pull:
                        query = self._changed_query()
                        self._repository()
                        self._say(f"GitHub の更新を取得中（{query.partition('since=')[2] or '開いている全件'} 以降）")
                        fetch = pool.submit(self._rest_issues, query)
                    self._say("Beads の課題を読み込み中")
                    self._adopt_existing([i.bd_id for i in self._issues() if beads.is_provisional(i.bd_id)])
                    listed = self._issues()
                    stale = {i.bd_id for i in listed if _after(i.raw.get("updated_at"), self._synced_at(i))}
                    self._say(f"Beads: {len(listed)} 件を読んだ（前回の送りより後に変えたもの {len(stale)} 件）")
                    if fetch is not None:
                        remote = fetch.result()
                        newest = _newest_update(remote)
                        self._say(f"GitHub: {len(remote)} 件が更新されていた")
                if fetch is not None:
                    incoming = self._without_echo(remote, listed)
                    if incoming:
                        self._say(f"Beads へ取り込む: {len(incoming)} 件（#{', #'.join(str(n) for n in sorted(incoming))}）")
                    lines += self._pull(incoming)
                # 取り込まなかった課題も控えから戻す（取り込みが読んだ kv を使い回す）。戻した課題は下で送る。
                restore_lines, restored = self._restore_from_snapshots(None)
                lines += restore_lines
                stale |= set(restored)
                before = self._issues()
                changed_ids = [i.bd_id for i in before if i.bd_id in stale]
                pushed_count = str(len(changed_ids))
                if changed_ids:
                    self._say(f"GitHub へ送る: {len(changed_ids)} 件")
                    has_ref = all(_ref_number(i.external_ref) is not None for i in before if i.bd_id in stale)
                    self._bd_ok(
                        ["github", "sync", "--push-only", "--issues", ",".join(changed_ids)],
                        "bd github sync --push-only",
                        keeps_issues=has_ref,
                    )
                self._mark_synced(changed_ids)
                renamed, renamed_lines = self._rename([i.bd_id for i in before if beads.is_provisional(i.bd_id)])
                lines += renamed_lines
                if renamed:
                    self._push(list(renamed.values()))
                    self._mark_synced(list(renamed.values()))
                # `--push-only` がどの課題を送ったかは分からないので、送りの印と違う着手はまとめて送り直す。
                pushed = self._load_pushed()
                unsent = [
                    i.bd_id for i in self._issues()
                    if (n := _ref_number(i.external_ref)) is not None and _claim_started(i)
                    and pushed.get(n) != _claim_started(i)
                ]
                if unsent:
                    self._push(unsent)
                    self._mark_synced(unsent)
            self._say("Project の Status 欄を揃える")
            changed = self._bridge(self._issues())
            if pull and self.bidirectional:
                beads.run_ok(self.toplevel, ["kv", "set", SEEN_KEY, _format_time(newest or started)])
            self._save()
        except _Failed as e:
            return lines + [f"TRACKER\tFAILED\tgithub\t{e}"]
        return lines + [f"TRACKER\tOK\tgithub\tpushed={pushed_count}\tstatus_changed={changed}"]

    # --- 取り込み ----------------------------------------------------------------

    def _without_echo(self, remote: dict[int, dict], issues: list[beads.Issue]) -> dict[int, dict]:
        """自分の送りで `updated_at` が進んだだけの Issue（送った時刻 + `SYNC_SLACK` 以前の更新）を落とす。

        控えの無い課題・Beads に無い Issue・更新時刻を読めない Issue は落とさない。
        """
        local = _by_number(issues)
        kept: dict[int, dict] = {}
        for n, item in remote.items():
            issue = local.get(n)
            since = self._synced_at(issue) if issue is not None else None
            updated = item.get("updated_at")
            if since is None or beads.parse_time(updated) is None or _after(updated, since + SYNC_SLACK):
                kept[n] = item
        return kept

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
        old = self._by_numbers(sorted(remote))
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
        pull_actor = f"{self._actor}{PULL_ACTOR_SUFFIX}:{os.getpid()}-{time.time_ns()}"
        self._bd_ok(["--actor", pull_actor, "github", "pull", *(str(n) for n in sorted(remote))], "bd github pull")
        new = self._by_numbers(sorted(remote))
        twins = [
            (new[n].bd_id, twin.bd_id)
            for n in sorted(remote)
            if n in new and n not in old and (twin := _duplicate_of(new[n], self._issues())) is not None
        ]
        # 別の作業ツリーの送りも見るので、この Session の控えではなく今の値を読む。
        pushed = self._load_pushed(fresh=True)
        legacy = self._load_synced()
        fixed: list[str] = []
        for n in sorted(remote):
            issue, was = new.get(n), old.get(n)
            if issue is None:
                continue
            try:
                overwritten = beads.overwritten_by(self.toplevel, issue.bd_id, pull_actor)
            except beads.BeadsError as e:
                lines.append(f"TRACKER\tFAILED\tgithub\t{beads.to_task_id(issue.bd_id)} の着手の印を戻せない: {e}")
                overwritten = None
            restore = [] if overwritten is None else _claim_restore_args(
                overwritten, issue, pushed.get(n), beads.parse_time(legacy.get(str(n)))
            )
            if restore:
                r = self._write(["update", issue.bd_id, *restore])
                if r.returncode == 0:
                    fixed.append(issue.bd_id)
                else:
                    lines.append(f"TRACKER\tFAILED\tgithub\t{beads.to_task_id(issue.bd_id)} の着手の印を戻せない: {_tail(r)}")
            was_closed = was is not None and was.status == "closed"
            if issue.status == "closed" and not was_closed and beads.ship_mark(issue) is None:
                if beads.CANCELLED_LABEL not in issue.labels:
                    self._write(["update", issue.bd_id, "--add-label", beads.CANCELLED_LABEL], check=True)
                    fixed.append(issue.bd_id)
                lines.append(f"TRACKER\tCLOSED\t{beads.to_task_id(issue.bd_id)}")
            elif was_closed and issue.status != "closed":
                marks = [l for l in issue.labels if l == beads.CANCELLED_LABEL or l in beads.SHIP_LABELS.values()]
                if marks:
                    self._write(["update", issue.bd_id, *[x for m in marks for x in ("--remove-label", m)]], check=True)
                    fixed.append(issue.bd_id)
        # 前の取り込みが戻す前に止まっていれば、取り込みの直前の版も空なので、控えから戻す。
        restore_lines, restored = self._restore_from_snapshots([new[n].bd_id for n in remote if n in new])
        lines += restore_lines
        fixed += restored
        renamed, rename_lines = self._rename([new[n].bd_id for n in remote if n in new])
        # 取り込みのあとの直し（assignee・label・付け替え）は Beads の更新なので、ここで送って `bd` の前回の
        # 同期の時刻を進める（進めないと、次の取り込みがこの課題を飛ばして GitHub での変更を送り返す）。
        fixed = list(dict.fromkeys([renamed.get(i, i) for i in fixed] + list(renamed.values())))
        if fixed:
            self._push(fixed)
        self._mark_synced([renamed.get(new[n].bd_id, new[n].bd_id) for n in remote if n in new])
        duplicate_lines = [
            f"TRACKER\tINVALID\t{beads.to_task_id(renamed.get(a, a))}\t{beads.to_task_id(renamed.get(b, b))} と重複している"
            for a, b in twins
        ]
        return lines + rename_lines + duplicate_lines

    def _rename(self, bd_ids: list[str]) -> tuple[dict[str, str], list[str]]:
        """`external_ref` の番号と ID が違う課題を `gh-<番号>` へ付け替える。`{元: 先}` と出力の行を返す。"""
        wanted = set(bd_ids)
        issues = self._issues_of(bd_ids)
        renamed: dict[str, str] = {}
        lines: list[str] = []
        for issue in issues:
            n = _ref_number(issue.external_ref)
            if issue.bd_id not in wanted or n is None or beads.is_numbered(issue.bd_id):
                continue
            target = f"gh-{n}"
            if target in renamed.values() or self._issue(target) is not None:
                lines.append(f"TRACKER\tINVALID\t{issue.bd_id}\t{target} が既にある（付け替えない）")
                continue
            r = self._write(["rename", issue.bd_id, target])
            if r.returncode != 0:
                lines.append(f"TRACKER\tINVALID\t{issue.bd_id}\tbd rename: {_tail(r)}")
                continue
            renamed[issue.bd_id] = target
        return renamed, lines

    def _changed_query(self) -> str:
        """前回見た更新時刻（無ければ開いた全件）より後に更新された Issue を引く一覧の問い合わせ。"""
        raw = self._kv_listing().get(SEEN_KEY)
        seen = beads.parse_time(raw.strip()) if isinstance(raw, str) else None
        if seen is None:
            return "state=open&per_page=100"
        return "state=all&per_page=100&since=" + _format_time(seen - SEEN_MARGIN)

    def _rest_issues(self, query: str) -> dict[int, dict]:
        return gh.rest_issues(self.toplevel, self._repository(), query, self._remaining)

    # --- Project の Status 欄 --------------------------------------------------------

    def _bridge(self, issues: list[beads.Issue], content_ids: dict[str, str] | None = None) -> int:
        """Status 欄を Beads の状態に揃える。最後に書いた Status と同じ課題には触らない。書いた件数を返す。

        `content_ids`（`{Issue の URL: node_id}`）にある課題は、Issue 側の引きを飛ばして Project へ足す。
        """
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
            gh.fill_items(self.toplevel, self._load_project(), items, self._items_dirty, self._repository(), self._remaining)
            todo = [(url, want) for url, want in todo if (items.get(url) or {}).get("status") != want]
        for url, want in todo:
            gh.write_status(
                self.toplevel, self.tracker, self._load_project(), items, self._items_dirty, url, want, self._remaining,
                (content_ids or {}).get(url),
            )
        return len(todo)

    def _content_ids(self, issues: list[beads.Issue]) -> dict[str, str]:
        """送った Issue の `{URL: node_id}` を、REST の一覧1回で引く（Status 欄を書くとき Issue ごとに引かないため）。引けなければ空。"""
        repo = self._repository_or_none()
        urls = {u for i in issues if (u := _issue_url(i.external_ref, repo)) is not None}
        if not urls:
            return {}
        starts = [beads.parse_time(i.raw.get("created_at")) for i in issues]
        query = "state=open&per_page=100"
        if all(t is not None for t in starts):
            query = "state=all&per_page=100&since=" + _format_time(min(starts) - SYNC_SLACK)
        try:
            found = self._rest_issues(query)
        except _Failed:
            return {}
        return {str(i["html_url"]): str(i["node_id"]) for i in found.values() if i.get("html_url") in urls and i.get("node_id")}

    def _load_project(self) -> dict:
        if self._project is None:
            self._project = gh.load_project(
                self.toplevel, self.tracker, self._kv_listing().get(gh.PROJECT_KEY), self._remaining
            )
        return self._project

    def _load_items(self) -> dict:
        if self._items is None:
            listed, repo = self._kv_listing(), self._repository_or_none()
            self._items = gh.load_items(listed, repo)
            gh.migrate_legacy_items(self.toplevel, self._items, self._items_dirty, listed.get(LEGACY_ITEMS_KEY), repo)
        return self._items

    # --- 課題ごとの同期の時刻（CONFLICT の判定） ---------------------------------------

    def _load_synced(self) -> dict:
        if self._synced is None:
            self._synced = _synced_of(self._kv_listing())
        return self._synced

    def _synced_at(self, issue: beads.Issue) -> datetime | None:
        n = _ref_number(issue.external_ref)
        return beads.parse_time(self._load_synced().get(str(n))) if n is not None else None

    def _mark_synced(self, bd_ids: list[str]) -> None:
        """送った・取り込んだ時刻を控え、その場で `bd kv` へ書く（あとの Status 欄の書き込みが落ちても残す）。

        触った番号のキーだけを書く（別の作業ツリーが書いた別の番号の時刻を戻さない）。
        """
        if not self.bidirectional or not bd_ids:
            return
        wanted = set(bd_ids)
        now = _format_time(_now())
        numbers = [
            str(n) for i in self._issues_of(bd_ids)
            if i.bd_id in wanted and (n := _ref_number(i.external_ref)) is not None
        ]
        if not numbers:
            return
        for n in dict.fromkeys(numbers):
            beads.run_ok(self.toplevel, ["kv", "set", SYNCED_ITEM_PREFIX + n, now])
            if self._kv_all is not None:
                self._kv_all[SYNCED_ITEM_PREFIX + n] = now
        self._synced = None

    # --- 課題ごとの送りの印（着手を戻すかの判定） ---------------------------------------

    def _push(self, bd_ids: list[str]) -> None:
        """`bd github push` で送り、届いたら課題ごとに送った着手の時刻を `bd kv` へ書く（変わったものだけ）。"""
        if self.bidirectional:
            self._adopt_existing(bd_ids)
        before = {i.bd_id: i for i in self._issues_of(bd_ids)}
        sent = {i: _claim_started(before.get(i)) for i in bd_ids}
        has_ref = all(i in before and _ref_number(before[i].external_ref) is not None for i in bd_ids)
        self._bd_ok(["github", "push", *bd_ids], "bd github push", keeps_issues=has_ref)
        if not self.bidirectional:
            return
        pushed = self._load_pushed()
        for issue in self._issues_of(bd_ids):
            n = _ref_number(issue.external_ref)
            if issue.bd_id in sent and n is not None and pushed.get(n) != sent[issue.bd_id]:
                key = f"{PUSHED_KEY_PREFIX}{n}"
                beads.run_ok(self.toplevel, ["kv", "set", key, sent[issue.bd_id]])
                self._kv_all = {**self._kv_listing(), key: sent[issue.bd_id]}

    def _adopt_existing(self, bd_ids: list[str]) -> None:
        """番号の無い課題と題・本文が同じ Issue が既にあれば、その番号を `external_ref` に控える。

        `bd github push` が Issue を立てた直後に上限で殺されると番号が Beads に書かれず、
        そのまま送り直すともう1つ立つ。
        """
        pending = [i for i in self._issues_of(bd_ids) if i.bd_id in bd_ids and _ref_number(i.external_ref) is None]
        if not pending:
            return
        taken: set[int] | None = None
        # 照らす Issue は作成が課題の作成 - SYNC_SLACK 以後なので、更新もそれ以後。読めない課題があれば全件。
        starts = [beads.parse_time(i.raw.get("created_at")) for i in pending]
        query = "state=all&per_page=100"
        if all(t is not None for t in starts):
            query += "&since=" + _format_time(min(starts) - SYNC_SLACK)
        found = self._rest_issues(query)
        remote = [found[n] for n in sorted(found)]
        for issue in pending:
            created = beads.parse_time(issue.raw.get("created_at"))
            for item in list(remote):
                made = beads.parse_time(item.get("created_at"))
                if (
                    item.get("title") == issue.title
                    and (item.get("body") or "").strip() == str(issue.raw.get("description") or "").strip()
                    and item.get("html_url")
                    and (created is None or made is None or made >= created - SYNC_SLACK)
                ):
                    if taken is None:
                        taken = {n for i in self._issues() if (n := _ref_number(i.external_ref)) is not None}
                    if item.get("number") in taken:
                        remote.remove(item)
                        continue
                    self._write(["update", issue.bd_id, "--external-ref", item["html_url"]], check=True)
                    remote.remove(item)
                    break

    def _load_pushed(self, fresh: bool = False) -> dict[int, str]:
        """課題ごとの送りの印の値（`{Issue 番号: 送った着手の時刻}`。印の無い課題は入らない）。
        `fresh` なら読み直す（別の作業ツリーが書いたものを見るため）。"""
        if fresh:
            self._kv_all = None
        return _pushed_of(self._kv_listing())

    # --- 着手の印の控え ------------------------------------------------------------

    def _restore_from_snapshots(self, bd_ids: list[str] | None) -> tuple[list[str], list[str]]:
        """読み済みの `bd kv list` の控えから戻す（モジュールの `_restore_from_snapshots`）。"""
        return _restore_from_snapshots(self.toplevel, self._kv_listing(), self._issues_of, bd_ids, self._actor)

    def _save(self) -> None:
        gh.save_items(self.toplevel, self._items or {}, self._items_dirty)

    # --- 呼び出し ------------------------------------------------------------------

    def _begin(self, deadline: float | None) -> None:
        """呼び出し（`before`・`after`・`register`・`sync_all`）の始め。`kv` は別の作業ツリーが書くので読み済みの値を捨てる。"""
        self._deadline = deadline
        self._kv_all = None
        self._synced = None

    def _listed_is_current(self) -> bool:
        return self._listed is not None and self._listed_at == beads.writes(self.toplevel)

    def _issues(self) -> list[beads.Issue]:
        """課題の一覧。読んだあとに課題を書く `bd` を打っていれば（`beads.writes(toplevel)` が進んでいれば）読み直す。"""
        if not self._listed_is_current():
            self._listed_at = beads.writes(self.toplevel)
            self._listed = beads.list_issues(self.toplevel)
        return self._listed

    def _issue(self, bd_id: str, many: bool = False) -> beads.Issue | None:
        """1件。一覧が読み済みか `many`（何件も引く）なら一覧から、でなければ `bd show`。"""
        if not many and not self._listed_is_current():
            shown = beads.show(self.toplevel, bd_id)
            return shown if shown is not None and shown.bd_id == bd_id else None  # `bd show` は ID の前方一致も引く
        return next((i for i in self._issues() if i.bd_id == bd_id), None)

    def _issues_of(self, bd_ids: list[str]) -> list[beads.Issue]:
        """`bd_ids` の課題（余分に返すことがある）。少なければ1件ずつ `bd show`、多ければ一覧。"""
        if len(bd_ids) > SHOW_LIMIT:
            return self._issues()
        return [i for i in (self._issue(b) for b in bd_ids) if i is not None]

    def _by_numbers(self, numbers: list[int]) -> dict[int, beads.Issue]:
        """Issue 番号 → 課題。`gh-<番号>` が引けない番号があれば、一覧から番号で探す。"""
        if len(numbers) <= SHOW_LIMIT:
            found = {}
            for n in numbers:
                issue = self._issue(f"gh-{n}")
                if issue is not None and _ref_number(issue.external_ref) == n:
                    found[n] = issue
            if len(found) == len(set(numbers)):
                return found
        return _by_number(self._issues())

    def _kv_listing(self) -> dict:
        if self._kv_all is None:
            listed = _loads(beads.run(self.toplevel, ["kv", "list", "--json"]).stdout)
            self._kv_all = listed if isinstance(listed, dict) else {}
        return self._kv_all

    def _write(self, args: list[str], check: bool = False) -> subprocess.CompletedProcess:
        """この作業ツリーの名前で課題を書く `bd` を打つ。`check` なら失敗で `BeadsError`。"""
        r = beads.run(self.toplevel, args, self._actor)
        if check and r.returncode != 0:
            raise beads.BeadsError(f"bd {' '.join(args)} が失敗（{r.returncode}）: {(r.stderr or r.stdout).strip()}")
        return r

    def _bd_ok(self, args: list[str], label: str, keeps_issues: bool = False) -> str:
        """`bd` を打つ。`keeps_issues` は、Beads の課題が変わらない呼び出し（番号の付いた課題の送り）に付ける。"""
        if self._env is None:
            env = os.environ.copy()
            if not env.get("GITHUB_TOKEN"):
                token = _gh(self.toplevel, ["auth", "token"], self._remaining())
                if token.returncode != 0 or not token.stdout.strip():
                    raise _Failed(f"gh auth token: {_tail(token)}")
                env["GITHUB_TOKEN"] = token.stdout.strip()
            self._env = env
        r = _bd(self.toplevel, [*args, "--json"], self._env, self._remaining(), keeps_issues)
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

    def _remaining(self) -> float | None:
        if self._deadline is None:
            return None
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise _Failed("トラッカーへの反映が上限の秒数を超えた")
        return left

    def _repository_or_none(self) -> str | None:
        if self._repo is None:
            self._repo = _repo_of(self._bd_config()) or ""
        return self._repo or None

    def _repository(self) -> str:
        repo = self._repository_or_none()
        if repo is None:
            raise _Failed("bd config の github.repository が無い")
        return repo


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

    `bd_ids` が無ければ振り分け前（`difficulty`・`loopable` が無い）の課題すべて。
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
            if beads.attribute_values(issue, beads.DIFFICULTY_KEY) or beads.attribute_values(issue, beads.LOOPABLE_KEY):
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


def _duplicate_of(issue: beads.Issue, others: list[beads.Issue]) -> beads.Issue | None:
    """題と本文（前後の空白を除く）が同じ別の課題。"""
    body = str(issue.raw.get("description") or "").strip()
    return next(
        (o for o in others if o.bd_id != issue.bd_id and o.title == issue.title
         and str(o.raw.get("description") or "").strip() == body),
        None,
    )


def _now() -> datetime:
    return datetime.fromtimestamp(time.time(), timezone.utc).replace(microsecond=0)


def _newest_update(remote: dict[int, dict]) -> datetime | None:
    """Issue の一覧の中で最も新しい `updated_at`。"""
    times = [t for t in (beads.parse_time(i.get("updated_at")) for i in remote.values()) if t is not None]
    return max(times) if times else None


def _format_time(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after(value, since: datetime | None) -> bool:
    """`value`（ISO の文字列）が `since` より後か。`since` が無ければ真（まだ一度も同期していない）。"""
    if since is None:
        return True
    at = beads.parse_time(value)
    return at is not None and at > since


def _op_timeout() -> float:
    try:
        value = float(os.environ.get(OP_TIMEOUT_ENV, ""))
    except ValueError:
        return OP_TIMEOUT
    return value if value > 0 else OP_TIMEOUT


