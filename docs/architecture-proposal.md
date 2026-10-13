# アーキテクチャ提案: tsukumo-workflow（タスク運用）

作成: 2026-10-14。対象コミット: main の `1aa45d5`。コードは書き換えていない（このファイルと `docs/README.md` を足しただけ）。採否は未定。

1. **性質**: LLM が読む手順書（スキル）と、状態と関門を持つ CLI `tw` の2本立て。流れは `next-task` が1つのスキルの中で分岐ごとに抱え、パーツ（受け入れ・振り返り）を兄弟のパスで直に呼んでいる。規則の正典 `WORKFLOW.md` には、全スキル共有のタスクのモデルと、`next-task` だけが読む流れの契約が混ざっている。
2. **様式**: 核と拡張点。入口 `next-task`（1件を決める）・オーケストレーター `task-workflow`（段を並べてパーツを呼ぶ）・パーツ（実装役・受け入れ役・振り返り・送り出し）に分け、パーツは名前と契約で呼ぶ。規則の正典は参照専用の `task-model`（仮）へ移す。境界は先に作り、設定で差し込む仕組みは2つ目の実装が出てから足す。
3. **最初の段階**: ファイルを動かさず、`WORKFLOW.md` から流れの節（「1サイクル」「受け入れ役の契約」「送り出し」）を `next-task` 側へ移す。

## 1. アプリの性質

| 軸 | 読み取ったこと | 根拠 |
| --- | --- | --- |
| 何を中心に回るか | 依頼1つで1サイクル（見渡す→選ぶ→着手→委譲→受け入れ→振り返り→送り出し）が回る手順。手順を読むのは LLM、状態の変化と関門は `tw` | `skills/next-task/SKILL.md` 手順0〜8、`README.md`「手順はコマンド `tw` が持ち、スキルは『どのサブコマンドをいつ打つか』だけを書く」 |
| 外の世界との境界 | Beads（`bd`・Dolt）、git、トラッカー（`gh`・Jira）、Claude Code（hook・Agent・`SendMessage`・`SubagentHandback`）、tsukumo の口（ツール一覧にあるときだけ） | `README.md`「要るもの」、`hooks/hooks.json`、`AGENTS.md`「書き方」 |
| 状態の持ち方 | タスクは Beads。着手の印・段の lap・段の控えは台帳（共有の git dir の `task-workflow/`）と `.tw/local/`。スキルは状態を持たない | `WORKFLOW.md`「ファイル配置と設定ファイル」 |
| 実行環境の数 | plugin で入れる／リンクで入れる、素の Claude Code／tsukumo の中（`mount`・`partial`・`user-installed`）、Codex の sandbox | `README.md`、tsukumo の `docs/architecture/workflow-contract.md`「合わせ方」 |
| 変わりやすい場所 | 2026-10-08 以降の203コミットで、`WORKFLOW.md` 63回・`next-task/SKILL.md` 42回・`next-task/implementer-brief.md` 17回。規則の変更と流れの変更が同じ `WORKFLOW.md` に集まる | `git log --since=2026-10-08 --format= --name-only -- skills agents hooks \| sort \| uniq -c \| sort -rn` |
| 守られている制約 | `scripts/check_repo.py`（`REQUIRES`・`OPTIONAL_SKILLS`・`${CLAUDE_SKILL_DIR}/../` の実在・hooks の形）。tsukumo の契約（`work_plan`・`report`・返却の1行目・Beads の欄）。プラグインの部品は `tsukumo-workflow:<名前>` の名前空間に入る。ユーザー単位のスキルがプロジェクト単位の同名スキルに勝ち、プロジェクト差分は `.tw/config.toml` で表す | `README.md`「決めたこと」4・「制約」、`check_repo.py` 冒頭 |
| 利用者と開発体制 | 今は作者1人。ただし他の人が自分の流れや部品を当てられる形を目標にする（2026-10-14 の作者の回答。近く差し替える予定は無い）。タスクはサブエージェントに委譲する運用で、スキルの文面が指示書そのもの | 作者の回答、`agents/no-delegate.md` |
| テストできる範囲 | `tw` は `selftest_*.py` で自動。スキルの文面は `check_repo.py` の整合だけで、流れの正しさは実行するまで分からない | `README.md`「検証」 |

### 現状の依存の辺

スキルどうしは import ではなく文面の参照でつながるので、参照を数えた。

| 参照元 → 参照先 | 形 | 数 |
| --- | --- | --- |
| `next-task` → `task-workflow` | 正典の節名・`tw`・`preflight.md` | 節8つ |
| `plan-tasks` → `task-workflow` | 正典の節名 | 節7つ |
| `setup-tasks` → `task-workflow` | 正典の節名・`${CLAUDE_SKILL_DIR}/../task-workflow/` | 節3つ・パス2本 |
| `retrospect` → `task-workflow` | 正典の節名 | 節3つ |
| `list-tasks` → `task-workflow` | 正典 | 1 |
| `next-task` → `retrospect` | `${CLAUDE_SKILL_DIR}/../retrospect/`（`scripts/material.py`・`SKILL.md` の節） | パス2本 |
| `next-task` → `comment-audit` | `${CLAUDE_SKILL_DIR}/../comment-audit/`（`scripts/`・`SKILL.md` を受け入れ役に渡す） | パス2本 |
| `next-task` → agents | `no-delegate`・`acceptor` を Agent の名前で | 2 |
| `tw lap` → 段の名前 | `LAP_STAGES = ("direct", "delegate", "accept", "review", "retro", "land", "sync")` | `skills/task-workflow/scripts/tw_handback.py` |
| `tw ask-guard` → 段 `retro` | `RETRO_STAGE = "retro"` の lap の後だけ質問を通す | `skills/task-workflow/scripts/ask_guard.py` |
| tsukumo → `task.py` | `skills/task-workflow/scripts/task.py` のパスを直に指す | tsukumo の `src/server/session-driver/adapter/sdk-ask-guard.ts` |

数え方: `grep -rhoE '正典「[^」]+」|WORKFLOW\.md[^「]{0,8}「[^」]+」' skills/<スキル>`、`grep -rn 'CLAUDE_SKILL_DIR}/\.\./' skills`。

### いま痛んでいる兆候

- **正典の中で層が混ざる。** `WORKFLOW.md` の14節のうち「1サイクル」「受け入れ役の契約」「送り出し」は `next-task` とその部品だけが読む流れの話で、残りは5つのスキルが読むタスクのモデル。スキルの名前が `task-workflow` なので、流れを担う側に見える
- **パーツをパスで呼ぶ。** `next-task` が `retrospect` と `comment-audit` を兄弟のパスで直に指す。差し替えるには、兄弟の位置に同じ形のファイルを置くしかない
- **流れの変種が1つのスキルの分岐になっている。** 委譲・近道（`direct-run.md`）・メインで行う（`main-run.md`）・hold（`hold.md`）が `next-task` の手順5の中の分岐。流れを1つ足すと `SKILL.md` と副ファイル（今27本・848行）が増える
- **関門が段の名前を知っている。** `ask-guard` は lap `retro` を境にしているので、振り返りを外した流れでは `loopable: Y` のタスクの質問がずっと拒まれる
- **外から中身のパスを指す。** tsukumo が `task.py` の場所を直に指していて、契約の外の持ち物を読んでいる
- **上書きの道が無い。** プラグインの部品は名前空間に入るので、同名のスキルを置いても置き換わらない。今の差し替えはプラグインを直すことだけ

## 2. 候補と選択

土台は動かさない: 「手順書（スキル）＋状態と関門の CLI（`tw`）」の2本立てと、「tsukumo とは契約だけで話す」は性質に合っている。候補はこの土台の中の割り方で立てる。

### 候補 A: 現状維持＋割り直し

- 効く性質: 変わりやすい `WORKFLOW.md` を共有のモデルと流れに割るだけで、変更の集まりと名前の食い違いが解ける
- 合わない点と補い方: パーツをパスで呼ぶこと・関門の段名・上書きの道の無さは残り、他の人が部品を当てられない。補えない
- 要求する形: ディレクトリはそのまま。`WORKFLOW.md` の流れの節を `next-task` へ移す
- 確度: 固い

### 候補 B: 核と拡張点（入口・オーケストレーター・パーツ）

- 効く性質: 「他の人が流れや部品を当てる」という目標に直接効く。パーツを名前と契約で呼ぶので兄弟の位置に縛られない。関門をパーツではなくオーケストレーターの段に結ぶので、パーツを替えても関門は変わらない
- 合わない点と補い方: 差し替える実装は今どれも1つずつで、差し込みの仕組みを先に作ると仕組みのほうが重くなる。境界（名前・契約・関門の結び先）だけ先に作り、設定で差し込む仕組みは2つ目の実装が出たときに足す
- 要求する形: `skills/next-task/`（入口）・`skills/task-workflow/`（オーケストレーター・流れ・パーツの契約）・`skills/task-model/`（規則と `tw`）・パーツのスキルと agents
- 確度: 試す価値あり（境界の部分は固い、差し込みの部分は推測）

### 候補 C: 状態機械を `tw` に置く

- 効く性質: `tw` が段の遷移を持ち、`tw next` が次にすべきことを返す。流れの順序を機械で守れ、selftest で流れを試せる
- 合わない点と補い方: 段の中身は LLM の判断（受け入れの採否・差し戻し・difficulty を上げる）で、分岐の多くが文脈に依る。`tw` に移すと分岐を `tw` とスキルの2か所に書くことになる。流れの差し替えに Python が要り、他の人の敷居が上がる
- 要求する形: `tw` に流れの定義と遷移表、スキルは段ごとの手順だけ
- 確度: 推測

### 選んだもの: B（最初の2段階は A と同じ）

性質の「他の人が当てられる形」「規則と流れの変更が1ファイルに集まる」「パーツをパスで呼ぶ」に効くのは B だけ。A は B の最初の段階としてそのまま残るので、途中で止めても価値がある。C は流れの判断を LLM が持つという中心の形と合わず、捨てた。

## 3. 目標のディレクトリ構造

```
tsukumo-plugins/
├── bin/tw                    住む: tw の入口（task-model/scripts/task.py へ渡すだけ）
│                             置かない: 処理
├── hooks/hooks.json          住む: tw の関門を hook に結ぶ設定
│                             置かない: 判定（tw が持つ）
├── agents/                   住む: 委譲先として起こすパーツ（no-delegate＝実装役、acceptor＝受け入れ役）の定義
│                             置かない: 依頼文（task-workflow/parts/ が持つ）
└── skills/
    ├── next-task/            入口。住む: 対象を1件に決める（ID・ふんわり・おまかせ）、取り残しの扱い、
    │                         週ごとの振り返りの起動、着手の印、task-workflow を呼ぶ
    │                         置かない: 着手した後の手順
    ├── task-workflow/        オーケストレーター。住む: 段の並び・lap を打つ位置・完了報告と /loop の続行判断
    │   ├── SKILL.md          既定の流れ（委譲→受け入れ→振り返り→送り出し）
    │   ├── flows/            流れの変種（direct.md・main.md・hold.md）。置かない: パーツの中身
    │   └── parts/            パーツごとの契約と既定の依頼文（implementer.md・acceptor.md・retrospect.md・ship.md）
    │                         置かない: 規則（task-model）、パーツの道具（各パーツのスキル）
    ├── task-model/           規則の正典（参照専用）。住む: 今の WORKFLOW.md の共有の節・TRACKER.md・
    │                         tw-reference.md・preflight.md・tw の実装（scripts/）
    │                         置かない: 流れの順序・パーツの契約・他のスキルの名前
    ├── retrospect/           パーツの既定の実装。住む: 自分の手順と道具
    ├── comment-audit/          置かない: 流れの中のどこで呼ばれるか
    └── plan-tasks/ list-tasks/ setup-tasks/ retro/
                              流れの外の入口（今のまま）
```

パーツの契約（`task-workflow/parts/*.md`）が持つもの:

| パーツ | 入力 | 返すもの | 守る関門 |
| --- | --- | --- | --- |
| 実装役 | タスクの ID・段の番号 | 返却の1行目（tsukumo の `workflow-contract.md`「委譲の返却」を参照）と `## 構造の変更` | `tw handback-guard`（`tw step`・`tw verify`・`tw pause`）、`tw commit-guard` |
| 受け入れ役 | 差分の範囲・完了条件・観点 | `- 指摘:`・`- 付随:`・`- 材料:` の行（今の「受け入れ役の契約」） | 書き換えない |
| 振り返り | 受け入れ役の `- 材料:` | `## 結果` に入れる `- 振り返り:` の1行と、積んだドラフトの ID | 作業ツリーを書き換えない |
| 送り出し | タスクの ID | `tw finish` の最後の行 | `tw ship` |

lap はパーツではなくオーケストレーターが段の前後で打つ。

### 許す依存の辺

| 辺 | 可否 |
| --- | --- |
| `next-task` → `task-workflow`（呼ぶ）・`task-model`（`tw`・規則） | 可 |
| `task-workflow` → `task-model` | 可 |
| `task-workflow` → パーツ（agents の名前・スキルの名前・`tw` のサブコマンド） | 可。パスでは指さない |
| パーツ → `task-model` | 可 |
| パーツ → `task-workflow`・`next-task` | 不可（パーツは流れの中の位置を知らない） |
| `task-model` → 他のスキル | 不可（参照専用。流れやパーツの名前を書かない） |
| `plan-tasks`・`list-tasks`・`setup-tasks` → `task-model` | 可（それ以外へは不可） |
| `tw` の関門 → オーケストレーターが打つ段 | 可。パーツの名前には結ばない |
| tsukumo → `bin/tw` | 可。`skills/` の中のパスは指さない |

`check_repo.py` に足す検査: `${CLAUDE_SKILL_DIR}/../` で指してよい兄弟を `task-model` だけに絞る。`task-model` の文書が `next-task`・`task-workflow`・パーツの名前を書いていないことを見る。

### 名前について

- `task-model`（仮）: タスクという概念の形（本文の枠・status・difficulty・loopable・ドラフト・Beads との対応）を言う名。候補は `task-model`・`task-rules`・`task-store`（7. 未決事項）
- `task-workflow`: 名前は残し、中身を規則から流れに入れ替える（作者の頭の中の呼び方に合わせる）
- `task-model` の冒頭に足す用語の文面案:
  - **流れ（flow）**: オーケストレーターが段を並べる順。既定は委譲→受け入れ→振り返り→送り出しで、変種は `task-workflow/flows/` に置く
  - **パーツ（part）**: 流れの1段を担う差し替えの単位。入力・返すもの・守る関門の3つ（パーツの契約）で決まり、オーケストレーターは名前で呼ぶ

## 4. 現状との差分

| 今 | 目標 |
| --- | --- |
| `skills/task-workflow/`（`WORKFLOW.md`・`TRACKER.md`・`tw-reference.md`・`preflight.md`・`scripts/`） | `skills/task-model/` へ改名。`WORKFLOW.md` の「1サイクル」「受け入れ役の契約」「送り出し」は抜く |
| `skills/next-task/SKILL.md` の手順5〜8 | `skills/task-workflow/SKILL.md` |
| `next-task/` の `delegate.md`・`implementer-brief.md`・`rewrite-plan.md`・`handback-read.md`・`unresponsive.md`・`return-abnormal.md`・`other-repo.md` | `task-workflow/parts/implementer.md` と、流れに共通の手順（`other-repo.md`）は `task-workflow/` 直下 |
| `next-task/` の `accept.md`・`acceptor-brief.md`・`review-criteria.md`・`review-rounds.md`・`visual-review.md`・`verify-check.md`・`verify-merge.md` | `task-workflow/parts/acceptor.md`（と副ファイル） |
| `next-task/retro-step.md` | `task-workflow/parts/retrospect.md` |
| `next-task/` の `finish.md`・`ship-stopped.md`・`commits-since-claim.md` | `task-workflow/parts/ship.md` |
| `next-task/completion-report.md` | `task-workflow/` 直下 |
| `next-task/` の `direct-run.md`・`main-run.md`・`hold.md` | `task-workflow/flows/` |
| `retrospect/scripts/material.py` を `next-task` がパスで呼ぶ | `tw` のサブコマンドから呼ぶ |
| `comment-audit/scripts/` を `next-task` がパスで受け入れ役に渡す | 受け入れ役のパーツの契約に「`comment-audit` スキルを名前で読む」と書く |
| `bin/tw` が `skills/task-workflow/scripts/task.py` を呼ぶ | `skills/task-model/scripts/task.py` を呼ぶ |
| tsukumo の `sdk-ask-guard.ts` が `task.py` のパスを指す | `bin/tw ask-guard` を呼ぶ |

**動かさないもの**: `tw` のサブコマンドとその出力、Beads の欄、tsukumo の契約（`workflow-contract.md`）、agents の名前、`hooks/hooks.json` の形、`plan-tasks`・`list-tasks`・`setup-tasks`・`retro` の中身（参照先の名前を追随させるだけ）。`next-task` に残すのは `SKILL.md` の手順0〜4・`start-stopped.md`・`leftover-claim.md`・`no-candidate.md`・`weekly-retrospect.md`。

## 5. 移行の段階

| 段階 | すること | 終わったと分かる証拠 | 確度 |
| --- | --- | --- | --- |
| 1. 正典を割る | ファイルを動かさず、`WORKFLOW.md` の「1サイクル」「受け入れ役の契約」「送り出し」を `next-task` の副ファイルへ移す | `plan-tasks`・`list-tasks`・`setup-tasks`・`retrospect` から移した節への参照が0件。`./check.sh --full` が通る | 固い |
| 2. パスで呼ぶのをやめる | `material.py` を `tw` のサブコマンドにし、`comment-audit` は名前で読ませる。tsukumo の `sdk-ask-guard.ts` を `bin/tw ask-guard` に替える。`check_repo.py` に兄弟の参照の検査を足す | `grep -rn 'CLAUDE_SKILL_DIR}/\.\./' skills` が `task-workflow`（段4の後は `task-model`）だけ。tsukumo の `pnpm run check` が通る | 固い |
| 3. 関門を流れの段に結ぶ | lap はオーケストレーターが打つと正典に書く。`ask-guard` の境を「lap `retro` の後」から「作業の段（委譲・受け入れ）を終えた後」に替える | 振り返りの lap を打たない流れでも、受け入れの後の質問が通る selftest | 試す価値あり |
| 4. 名前を組み替える | `task-workflow` を `task-model` に改名し、`next-task` の手順5〜8と副ファイルを新しい `task-workflow` へ、変種を `flows/`、パーツを `parts/` へ移す。`next-task` は印を立てたら `task-workflow` を呼ぶ | `next-task/SKILL.md` が手順0〜4だけ。実タスク1件を `/next-task` で送り出せる。tsukumo の `vendor/tsukumo-plugins` を上げて `pnpm run check --full` が通る | 試す価値あり |
| 5. ふんわりした指定 | `next-task` が頼み文の語と `tw status` の summary・label から `READY` を絞り、1件に決まらなければ候補を見せて聞く（`/loop` からは使わない） | 「不具合修正のどれか」で候補が絞られる | 試す価値あり |
| 6. 差し込みの設定 | 2つ目の実装が出たとき、`.tw/config.toml` に `flow` と `[parts]` を足し、`tw config` が解いて出す。オーケストレーターはその名前で呼ぶ | 既定と違う受け入れ役を設定だけで当てられる | 推測 |

## 6. 採らなかった案

- **候補 C（状態機械を `tw` に置く）**: 2. の理由
- **同名のスキルを置いて上書きする**: プラグインの部品は名前空間に入り、ユーザー単位がプロジェクト単位に勝つので、置いた場所で効き方が変わる（`README.md`「制約」）。設定で名前を指す形（段階6）にした
- **パーツごとに別のプラグインにする**: `README.md`「決めたこと」1（プラグイン間の依存を作らない）とぶつかる
- **`tw` の実装をプラグインの根（`tw/`）に出す**: 実装と規則の正典が離れ、selftest と `check.sh` の段を組み直す割に得が小さい。段階4で `task-model` に一緒に入れる
- **流れごとに入口を分ける**（`next-task-direct` など）: 入口が増え、選び方の規則が重なる

## 7. 未決事項

- **`task-model` の名前**: 本文は `task-model` で書いた。`task-rules`・`task-store` を採っても、変わるのは段階4の改名先と参照の追随だけ
- **`ask-guard` の境の読み方**: `WORKFLOW.md`「loopable」は「振り返りの `tw lap T-xxx retro` のあとの質問は通す」とだけ書き、理由を書いていない。本文は「作業の段を終えたら質問してよい」と読んだ。振り返りそのものに質問を許す意図だったなら、段階3の境は `retro` のまま残し、振り返りを外す流れが代わりの lap を打つ形になる
- **「他の人が使う」前提**: tsukumo の `docs/requirements.md` は対象ユーザーを作者本人だけとしている。このプラグインを他の人に向けるなら、tsukumo 側の記述をどう扱うかを決める必要がある
- **tsukumo 側の追随の範囲**: 段階4で `vendor/tsukumo-plugins` を上げるとき、tsukumo の文書（`workflow-contract.md`・`CLAUDE.md` の手順の参照など）のどこが `task-workflow` を規則の正典として指しているかは数えていない
- **`next-task` から `task-workflow` を呼ぶ形**: Skill ツールで呼ぶと想定した。`/loop` の続行判断を `task-workflow` 側に置いたので、`/loop /next-task` が1サイクルで止まるかは段階4で確かめる
