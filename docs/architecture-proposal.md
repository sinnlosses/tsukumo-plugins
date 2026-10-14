# アーキテクチャ提案: tsukumo-workflow（タスク運用）

作成: 2026-10-14。対象コミット: main の `1aa45d5`。コードは書き換えていない（このファイルと `docs/README.md` を足しただけ）。
2026-10-14 に作者が候補 B を採った。同日、「名前で呼ぶ決まり」と「差し替えの方法」（3. の小節）を書き足し、移行の段階を組み直した。

1. **性質**: LLM が読む手順書（スキル）と、状態と関門を持つ CLI `tw` の2本立て。流れは `next-task` が1つのスキルの中で分岐ごとに抱え、パーツ（受け入れ・振り返り）を兄弟のパスで直に呼んでいる。規則の正典 `WORKFLOW.md` には、全スキル共有のタスクのモデルと、`next-task` だけが読む流れの契約が混ざっている。
2. **様式**: 核と拡張点。入口 `next-task`（1件を決める）・オーケストレーター `task-workflow`（段を並べてパーツを呼ぶ）・パーツ（実装役・受け入れ役・振り返り・送り出し）に分け、パーツは名前と契約で呼ぶ。規則の正典は参照専用の `task-model`（仮）へ移す。境界を先に作り、そのうえで `.tw/config.toml` で差し込む仕組みと、それを描く tsukumo の設定画面を足す。
3. **最初の段階**（7段のうち1）: ファイルを動かさず、`WORKFLOW.md` から流れの節（「1サイクル」「受け入れ役の契約」）を `next-task` 側へ移す。

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

- **正典の中で層が混ざる。** `WORKFLOW.md` の14節のうち「1サイクル」「受け入れ役の契約」は `next-task` とその部品だけが読む流れの話で、残りは5つのスキルが読むタスクのモデル。「送り出し」は `tw ship` の振る舞いで、`plan-tasks` などタスクに紐付かない送り出しにも効くのでモデルの側に残る。スキルの名前が `task-workflow` なので、流れを担う側に見える
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
- 合わない点と補い方: 差し替える実装は今どれも1つずつで、差し込みの仕組みを先に作ると仕組みのほうが重くなる。境界（名前・契約・関門の結び先）を先に作り、差し込みの仕組みは後ろの段階に置く。作者は、今は差し替えの予定が無くても他の人が使う前提で仕組みまで作ると決めた（2026-10-14）
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
├── bin/tw                    住む: tw の入口（task-model/scripts/tw/task.py へ渡すだけ）
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

### 名前で呼ぶ決まり

今も委譲先と受け入れ役は名前で呼んでいる。`next-task/delegate.md`・`accept.md` は Agent ツールの一覧から「末尾が `no-delegate`／`acceptor` の行」を拾い、リンクで入れたとき（`no-delegate`）と plugin で入れたとき（`tsukumo-workflow:no-delegate`）の名前の違いを吸収し、無ければ `general-purpose` に戻る。スキルの一覧も同じで、リンクで入れたものは `retrospect`、plugin のものは `<プラグイン名>:<名前>` で並ぶ。これをパーツ全体の決まりにする。

| 決まり | 理由 |
| --- | --- |
| 既定のパーツは末尾一致で探し、設定でパーツを指すときは全名（`my-plugin:acceptor`）で書かせる | 末尾が同じパーツが2つ並ぶと末尾一致では決まらない |
| スキルのパーツは Skill ツールで呼ぶ | 読み込むと `SKILL.md` と置き場（Base directory）が渡り、中のファイルを兄弟のパスなしで引ける |
| スキルのパーツの `SKILL.md` は短くし、細部を副ファイルへ置く | Skill ツールは `SKILL.md` を丸ごと読む。今の `retro-step.md` は `retrospect` の `SKILL.md`（272行）の節を絞って読ませている |
| パーツは `disable-model-invocation: true` にしない | モデルから呼べなくなる（例: claude-skills の `implement`） |
| 関門はパーツの名前に結ばない | 下の段落 |

**関門がいま実装役の名前に結ばれている。** `hooks/hooks.json` の `commit-guard`・`handback-guard` は `--agent-scoped` で、`agent_type` の末尾が `no-delegate` のときだけ掛かる（`task.py` の `commit-guard` の説明）。リンクで入れたときは `agents/no-delegate.md` の frontmatter の hook が掛ける。どちらでも、実装役を別のエージェントに替えると関門が黙って外れる。関門は「着手の控えがある作業ツリーで動くサブエージェント」に名前を問わず掛ける形に替える。名前を問わず掛けるだけだと、受け入れ役の返却も「最後の段の返却」の判定（`## 構造の変更` の欠け）や検証の欠けで拒まれるので、`handback-guard` は `Edit`・`Write`・`NotebookEdit` を一度でも使ったサブエージェント（`agent_id` ごとの印）の返却だけを判定する（段階3で入れた。`tw-reference.md`「hook の関門」）。`hooks.json` が読まれるのは plugin で入れたときだけなので、リンクで入れた人が自作の実装役を使うときに関門をどう掛けるかは 7. に残す。

### 差し替えの方法

**値の正は `.tw/config.toml` に置き、tsukumo の画面はその上に載せるだけにする。** tsukumo が値を持つと、tsukumo の ADR 0025（リポジトリに属する値の置き場は作らない）と ADR 0024（tsukumo はスキルの名前を知らない）の両方にぶつかり、素の Claude Code では差し替えが効かない。

```toml
# .tw/config.toml に足す形（段階6で入れた。書かなければ flow は delegate、枠は "既定"）
flow = "delegate"              # delegate / main

[parts]
implementer = "既定"           # 既定 = 同梱のパーツを一覧の末尾一致で探す
acceptor = "my-plugin:acceptor"   # 全名で書くと完全一致で呼ぶ
retrospect = "なし"            # "なし" で外せるのはこの枠だけ
ship = "既定"                  # 既定は tw finish の手順
```

`direct`（近道）と `hold` は、タスクごとに近道の印と status で決まるので `flow` の値にしない（リポジトリ全体を近道にすると、WORKFLOW.md「委譲しない近道」の基準の表を素通りする）。

- **選べるのは流れの種類と、各枠のパーツだけ。** 段を自由に並べ替える形にはしない。受け入れは実装の後・送り出しは最後という順の制約と関門は段の意味に依るので、自由に並べられると壊れた流れが作れ、制約を画面と `tw` の両方で守ることになる
- **`tw` が設定の口を2つ持つ。** `tw config --schema`（流れの種類・枠の名前・枠ごとの既定・`なし` を許すか、を JSON で出す）と `tw config set <キー> <値>`（検査してから書く）。オーケストレーターは今までどおり `tw config` で解けた値を読む
- **tsukumo の画面は欄の形をもらって描き、`tw` 経由で書くだけ。** 枠に入れられる候補は、tsukumo が SDK から受け取るスキルとエージェントの一覧から出せる。tsukumo は名前も値も持たない。これは tsukumo のワークフローの契約（`docs/architecture/workflow-contract.md`）に「設定の口」を足すことになるので、tsukumo 側で ADR 0024・0025 を改訂してから入れる

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
| `skills/task-workflow/`（`WORKFLOW.md`・`TRACKER.md`・`tw-reference.md`・`preflight.md`・`scripts/`） | `skills/task-model/` へ改名。`WORKFLOW.md` の「1サイクル」「受け入れ役の契約」は抜く |
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
| 1. 正典を割る | ファイルを動かさず、`WORKFLOW.md` の「受け入れ役の契約」を `next-task` の副ファイルへ移す（「1サイクル」は先行のタスクが SKILL への1行にする） | `plan-tasks`・`list-tasks`・`setup-tasks`・`retrospect` から移した節への参照が0件。`./check.sh --full` が通る | 固い |
| 2. パスで呼ぶのをやめる | `material.py` を `tw` のサブコマンドにし、`comment-audit` は名前で読ませる。tsukumo の `sdk-ask-guard.ts` を `bin/tw ask-guard` に替える。`check_repo.py` に兄弟の参照の検査を足す | `grep -rn 'CLAUDE_SKILL_DIR}/\.\./' skills` が `task-workflow`（段4の後は `task-model`）だけ。tsukumo の `pnpm run check` が通る | 固い |
| 3. 関門を名前と段から外す | lap はオーケストレーターが打つと正典に書く。`ask-guard` の境を「lap `retro` の後」から「作業の段（委譲・受け入れ）を終えた後」に替える。`commit-guard`・`handback-guard` を `agent_type` の末尾ではなく「着手の控えがある作業ツリーで動くサブエージェント」に掛ける | 振り返りの lap を打たない流れでも受け入れの後の質問が通る selftest と、`no-delegate` 以外の名前の委譲先でも拒む selftest | 試す価値あり |
| 4. 名前を組み替える | `task-workflow` を `task-model` に改名し、`next-task` の手順5〜8と副ファイルを新しい `task-workflow` へ、変種を `flows/`、パーツを `parts/` へ移す。`next-task` は印を立てたら `task-workflow` を呼ぶ | `next-task/SKILL.md` が手順0〜4だけ。実タスク1件を `/next-task` で送り出せる。tsukumo の `vendor/tsukumo-plugins` を上げて `pnpm run check --full` が通る | 試す価値あり（GH-779 で組み替えた。手順の番号4a〜8は `task-workflow/SKILL.md` に残し、`check_repo.py` に依存の辺と `task-model` の名指しの検査を足した。実タスクでの送り出しと tsukumo 側の追随は残り） |
| 5. ふんわりした指定 | `next-task` が頼み文の語と `tw status` の summary・label から `READY` を絞り、1件に決まらなければ候補を見せて聞く（`/loop` からは使わない） | 「不具合修正のどれか」で候補が絞られる | 試す価値あり |
| 6. 差し込みの設定 | `.tw/config.toml` に `flow` と `[parts]` を足し、`tw config` が解いて出す。`tw config --schema`・`tw config set` を足す。オーケストレーターは「名前で呼ぶ決まり」に沿って設定の名前で呼ぶ | 既定と違う受け入れ役を設定だけで当てられる。`--schema` の出力を selftest が固定する | 試す価値あり（GH-782 で入れた。`--schema` は版の欄 `schema` と、欄ごとの `label`・`description`・`candidates`〔`agent`／`skill`〕・`allow_none` を持つ1行の JSON で、説明文の正は `layout.py` の表。`set` は行で書き換え、ほかの行とコメントを変えない。流れの語彙は `tw` に持ち、`check_repo.py` が `flows/` と `SKILL.md` との対応を見る。`既定` の末尾一致が複数当たったら止めて `tw config set` で全名を選ばせる。実際に別の受け入れ役で1件送るのは残り） |
| 7. tsukumo の設定画面 | tsukumo 側で ADR 0024・0025 と `workflow-contract.md` に「設定の口」を足し、歯車の設定に、`tw config --schema` から描いて `tw config set` で書く欄を置く | 画面で受け入れ役を替えると `.tw/config.toml` が変わり、次の `/next-task` で効く | 推測 |

## 6. 採らなかった案

- **候補 C（状態機械を `tw` に置く）**: 2. の理由
- **同名のスキルを置いて上書きする**: プラグインの部品は名前空間に入り、ユーザー単位がプロジェクト単位に勝つので、置いた場所で効き方が変わる（`README.md`「制約」）。設定で名前を指す形（段階6）にした
- **tsukumo の歯車の設定に流れの値を持たせる**: tsukumo の ADR 0024・0025 に反し、tsukumo の外では差し替えが効かない
- **段を自由に並べ替える編集画面**: 順の制約を画面と `tw` の両方で守ることになり、壊れた流れが作れる
- **パーツごとに別のプラグインにする**: `README.md`「決めたこと」1（プラグイン間の依存を作らない）とぶつかる
- **`tw` の実装をプラグインの根（`tw/`）に出す**: 実装と規則の正典が離れ、selftest と `check.sh` の段を組み直す割に得が小さい。段階4で `task-model` に一緒に入れる
- **流れごとに入口を分ける**（`next-task-direct` など）: 入口が増え、選び方の規則が重なる

## 7. 未決事項

- **`task-model` の名前**: 本文は `task-model` で書いた。`task-rules`・`task-store` を採っても、変わるのは段階4の改名先と参照の追随だけ。段階4（GH-779）で名前は `task-model` に決めて改名した
- **「他の人が使う」前提**: tsukumo の `docs/requirements.md` は対象ユーザーを作者本人だけとしている。このプラグインを他の人に向けるなら、tsukumo 側の記述をどう扱うかを決める必要がある
- **tsukumo 側の追随の範囲**: 段階4で `vendor/tsukumo-plugins` を上げるとき、tsukumo の文書（`workflow-contract.md`・`CLAUDE.md` の手順の参照など）のどこが `task-workflow` を規則の正典として指しているかは数えていない
- **パーツの選びの置き場**: 本文はリポジトリごと（`.tw/config.toml`。コミットされ、同じリポジトリの人と流れを共有できる）にした。利用者ごとの好み（受け入れ役だけ替えたい、など）を許すなら、利用者単位の設定を重ねる層が要る
- **リンクで入れたときの関門**: `hooks.json` は plugin で入れたときだけ読まれる。リンクで入れた人が自作の実装役を使うと、frontmatter に hook を持たないので関門が掛からない。plugin で入れることを前提にするか、`setup-tasks` がプロジェクトの `.claude/settings.json` に hook を足すかを決める
- **`next-task` から `task-workflow` を呼ぶ形**: Skill ツールで呼ぶと想定した。`/loop` の続行判断を `task-workflow` 側に置いたので、`/loop /next-task` が1サイクルで止まるかは段階4で確かめる
  - 段階4（GH-779）で決めた: 完了報告と `/loop` の続行判断は `task-workflow/completion-report.md` が持ち、文脈を空にして送り直すときは「呼ばれたときに渡された `/loop` の依頼文」をそのまま渡す。`next-task` は手順5より前で止まったときの節だけを持ち、そこからは続行しない。実際の `/loop` で1サイクルずつ回るかは、実タスクで確かめる
- **改名と `tw` の張り替え**（段階4で決めた）: `install.sh` は、改名前の置き場（`skills/task-workflow/scripts/task.py`）を指す `~/.local/bin/tw` を自分のリンクとして張り替える。主ブランチへ入れたら、続けて `./install.sh` を打つ（打つまで PATH の `tw` は消えたパスを指して動かない）
- **`task-model` の書き方**（段階4で決めた）: ほかのスキルとエージェントを名指さず、`WORKFLOW.md` の冒頭の用語（入口・登録の入口・一覧の入口・用意の入口・流れ／オーケストレーター・パーツ）の役の名で書く。データの名前（`bd kv` の `task-workflow.*`・台帳の `task-workflow/`・`docs/history/retrospect.md`・`retrospect_due`）は互換のために変えない
