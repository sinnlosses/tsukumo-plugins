export const meta = {
  name: "task-flow",
  description: "着手の印が立ったタスク1件を、実装 → 受け入れ → 振り返りの段で進める（送り出しは呼んだ側が tw finish で行う）",
  whenToUse: "tw claim が CLAIMED を返したタスク1件を進めるとき（計画の判定と書き直しは呼ぶ側が済ませてから）",
  phases: [
    { title: "Implement", detail: "no-delegate が ## やること の段を順に実装し、段ごとに phase_result を呼ぶ" },
    { title: "Accept", detail: "tw accept の判定で道を分け、acceptor が判定する。差し戻しは上限2回" },
    { title: "Retrospect", detail: "retrospector が retrospect スキルで兆候を判定する" },
  ],
}

// args: { taskId, phaseCount, phaseIds, steps?, model?, from? }
//   taskId     着手したタスクの ID（GH-123 など）
//   phaseIds   tw plan-phases の JSON の phases（{ id, name } の並び。並列は配列）。phase() の題と agent() の phase は、この id からだけ作る
//   phaseCount ## やること の段の数（実装の agent が phase_result の count に渡す）
//   steps      走らせる段。既定は ["implement", "accept", "retrospect"]
//   model      実装・直しの agent の model（haiku・sonnet・opus。省くとメインの model）
//   from       呼び直すときに始める段（implement・accept・retrospect）
//   計画の判定と書き直しは呼ぶ側（メイン）が起こす前に済ませる。phaseIds に impl- の id が無く impl だけなら、計画が未確定なので起動を拒む
// 戻り値: { stopped: false, conventionLines?, implement, accept, retrospect } か { stopped: true, phase, reason }

const REJECT_LIMIT = 2
const ALL_STEPS = ["implement", "accept", "retrospect"]

const IMPLEMENT = {
  type: "object",
  properties: {
    changedFiles: { type: "array", items: { type: "string" } },
    verifyCommand: { type: "string" },
    verifyResult: { type: "string" },
    verifyPassed: { type: "boolean" },
    draftLines: { type: "array", items: { type: "string" } },
    stepLines: { type: "array", items: { type: "string" } },
    conventionLines: { type: "array", items: { type: "string" } },
    frictionLines: { type: "array", items: { type: "string" } },
    images: { type: "array", items: { type: "string" } },
    stopped: { type: "boolean" },
    reason: { type: "string" },
  },
  required: ["changedFiles", "verifyCommand", "verifyResult", "verifyPassed", "stepLines", "conventionLines", "frictionLines", "stopped"],
}

const ACCEPT_PREPARE = {
  type: "object",
  properties: {
    kind: { type: "string", enum: ["REVIEW", "SKIP", "NOTHING", "NO_RANGE", "STOP"] },
    acceptLines: { type: "string" },
    brief: { type: "string" },
    tree: { type: "string" },
    reason: { type: "string" },
  },
  required: ["kind", "acceptLines", "brief"],
}

const JUDGMENT = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["accept", "reject"] },
    findings: { type: "array", items: { type: "string" } },
    visual: { type: "string" },
    friction: { type: "string" },
    human: { type: "number" },
    notes: { type: "string" },
  },
  required: ["verdict", "findings", "friction", "human"],
}

const ACCEPT_CLOSE = {
  type: "object",
  properties: {
    verifyCheckLine: { type: "string" },
    closed: { type: "boolean" },
    reason: { type: "string" },
  },
  required: ["verifyCheckLine", "closed"],
}

const RETRO = {
  type: "object",
  properties: {
    signal: { type: "string", enum: ["none", "some"] },
    line: { type: "string" },
    draftIds: { type: "array", items: { type: "string" } },
  },
  required: ["signal", "line", "draftIds"],
}

if (!args || typeof args.taskId !== "string" || !Number.isInteger(args.phaseCount) || args.phaseCount < 1) {
  throw new Error("args に taskId（文字列）と phaseCount（1以上の整数）が要る")
}
const taskId = args.taskId
const phaseIds = (Array.isArray(args.phaseIds) ? args.phaseIds : [])
  .flat()
  .map((p) => (typeof p === "string" ? p : p?.id))
  .filter((id) => typeof id === "string" && id !== "")
const findId = (match) => phaseIds.find(match)
const phaseCount = args.phaseCount
const steps = Array.isArray(args.steps) ? args.steps : ALL_STEPS
const startAt = ALL_STEPS.includes(args.from) ? ALL_STEPS.indexOf(args.from) : 0
const runs = (step) => steps.includes(step) && ALL_STEPS.indexOf(step) >= startAt
const modelOpt = typeof args.model === "string" ? { model: args.model } : {}

const COMMON =
  `タスク ${taskId} は着手の印が立っている。作業ツリーは今の cwd。ファイルの書き換えは Edit・Write で行う。` +
  "tsukumo の phase_result がツール一覧に無いときは ToolSearch で select:mcp__tsukumo__phase_result を引いてから呼ぶ。" +
  "`vendor/tsukumo-plugins` は読み込み元なので、`git submodule update`・`git checkout` などで動かさない（`tw` が submodule のずれで止まっても、そのまま報告する）。"

const result = { stopped: false }

const titleOf = (step, match) => {
  const id = findId(match)
  if (id === undefined) {
    throw new Error(`args.phaseIds に ${step} の段の id が無い（tw plan-phases の phases をそのまま渡す）`)
  }
  return id
}
if (runs("implement") && !phaseIds.some((id) => id.startsWith("impl-"))) {
  throw new Error(
    "args.phaseIds に impl- の段の id が無い（実装の範囲が未確定）。起動の前に、メインが tw plan-check で計画を確かめ、" +
      "古ければ書き直して、tw plan-phases T-xxx --through 計画 の phases を渡す（skills/next-task/script-flow.md の手順1a）",
  )
}
const implementTitle = runs("implement") ? titleOf("implement", (id) => id.startsWith("impl-")) : ""
const acceptTitle = runs("accept") ? titleOf("accept", (id) => id === "accept") : ""
const retrospectTitle = runs("retrospect") ? titleOf("retrospect", (id) => id === "retrospect") : ""

if (runs("implement")) {
  phase(implementTitle)
  const implement = await agent(
    `${COMMON}\n` +
      `最初に \`tw brief implementer ${taskId}\` を打ち、出力の2行目以降の決まりを読んで従う。` +
      `\`tw show ${taskId}\` の ## やること の段 1 から ${phaseCount} までを、番号の順に1段ずつ実装する。` +
      `最後の段より前の段を1つ済ませるたびに、\`tw step ${taskId} <段の番号>\` を打つ。` +
      "`STEPPED` の行が出るまで次の段へ進まない。`STEP_CHECK_FAILED` など終了コードが 0 でないときは、続く出力の指摘を直して打ち直す。" +
      `出た \`STEPPED\` の行は stepLines に1段1行で入れる（最後の段は \`tw verify\` で済むので \`STEPPED\` は要らない。行数は ${phaseCount - 1}）。` +
      "各段に入るたびに（済ませた・飛ばした・止めたの結果が出るたびに、必ず）phase_result を呼ぶ。呼ばずに次の段へ進まない。" +
      `{ phase: <段の番号>, count: ${phaseCount}, outcome: "finished", summary: <その段でしたこと・分かったことを1〜2文> } で呼ぶ。` +
      "段が要らず飛ばすなら outcome を skipped にする（summary は付けない）。" +
      "判断が想定より要ると分かったら押し切らず、その段で phase_result を outcome: stopped（reason に理由）で呼んで止め、" +
      "schema の stopped を true、reason に理由を入れて返す。" +
      `最後の段のあと \`tw verify\` を打ち、打ったコマンド・結果の要点・通ったかを返す。` +
      "返す欄は schema のとおり。draftLines には委譲先の報告に書く `- ドラフト:` の行があれば入れる。" +
      "conventionLines には、`tw brief implementer` の決まりに従って報告に書く `- 規約:` の行（あれば `- 規約の指摘:` の行も）を1行1要素で入れる。" +
      "frictionLines には、段ごとに `tw brief implementer` の決まりの `## friction log` の書き方で1行ずつ入れる" +
      `（1段1行。何も起きなかった段は \`- 実装 段<n>: friction log: なし\`、起きた段はその段の色つきの行）。` +
      "段を skipped にしたときは、飛ばしたわけの1行を入れる。frictionLines は空にしない。" +
      "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。",
    { agentType: "tsukumo-workflow:no-delegate", schema: IMPLEMENT, label: "implement", phase: implementTitle, ...modelOpt },
  )
  if (implement === null) {
    return { stopped: true, phase: "implement", reason: "agent が null を返した" }
  }
  result.implement = implement
  if (implement.stopped) {
    return { stopped: true, phase: "implement", reason: implement.reason ?? "実装の agent が止めた", implement }
  }
  if (!implement.verifyPassed) {
    return { stopped: true, phase: "implement", reason: "tw verify が通っていない", implement }
  }
  const stepped = implement.stepLines.filter((line) => line.startsWith("STEPPED")).length
  if (stepped < phaseCount - 1) {
    return {
      stopped: true,
      phase: "implement",
      reason: `tw step の STEPPED が ${stepped} 行しかない（${phaseCount - 1} 行が要る）。途中の段の控えが欠けている`,
      implement,
    }
  }
  if (implement.frictionLines.length === 0) {
    return {
      stopped: true,
      phase: "implement",
      reason: "frictionLines が空（段ごとの friction log の行が返っていない）。受け入れへ進まない",
      implement,
    }
  }
  result.conventionLines = implement.conventionLines
}

if (runs("accept")) {
  phase(acceptTitle)
  let round = 0
  let tree = ""
  let judgment = null
  let prepared = null
  const fixes = []
  const reportSection = (heading, report) =>
    report
      ? `\n\n## ${heading}\n理由・所見: ${report.reason ?? "（なし）"}\n` +
        `検証: ${report.verifyCommand ?? ""} ${report.verifyResult ?? ""}\n` +
        `段の記録（stepLines）:\n${(report.stepLines ?? []).join("\n")}`
      : ""
  while (true) {
    const first = round === 0
    const imagePaths = [...new Set([result.implement, ...fixes].flatMap((report) => report?.images ?? []))]
    const imageSentence =
      "画面や見た目を確かめる完了条件があるタスクでは、次の画像のパスを全部 `--image <パス>` で並べて `tw brief acceptor` に付ける。" +
      `画像が無ければ \`--visual\` を付ける: ${imagePaths.length > 0 ? imagePaths.join(" ") : "（画像のパスは無い）"}`
    prepared = await agent(
      `${COMMON}\n` +
        (first && result.implement
          ? `最初に、\`tw relay ${taskId} 実装 --body-file -\` の標準入力へ次の行を、この順に1行1要素で書く（実装の申し送り。tw accept より前に書き終える）。` +
            `frictionLines:\n${result.implement.frictionLines.join("\n")}\n` +
            `draftLines:\n${(result.implement.draftLines ?? []).join("\n")}\n` +
            `conventionLines:\n${result.implement.conventionLines.join("\n")}\n` +
            "（見出しの `frictionLines:` などは書かず、各配列の行だけを並べる。空の配列は行を足さない）。書いたあと、"
          : "") +
        (first
          ? `\`tw accept ${taskId}\` を打つ。出力の最後の行で道を分ける: ` +
            "`REVIEW`（SNAPSHOT の行がある）なら kind を REVIEW に、" +
            "`SKIP`・`NOTHING`・`NO_RANGE` の行が出たなら同名の kind にする。" +
            "`NOT_LAST_STEP`・`FORMAT_FAILED`・`MISSING`・`INVALID`・`NOT_OWNER` など止まる行が出たら kind を STOP にして reason に行を入れる。"
          : `受け入れ役が差し戻したので、\`tw lap ${taskId} review start\` を打つ。\`tw accept\` は打たない（\`accept\` の end は受け入れの締めの agent が \`--after-review\` で打つ。ここで打つと、受け入れ役の前に \`accept\` が閉じて、締めの agent が \`delegate\` の start を偽って書く）。` +
            "kind は REVIEW にする。`NOT_CLAIMED`・`NOT_OWNER` など止まる行が出たら kind を STOP にして reason に行を入れる。") +
        (first
          ? "続けて、打った `tw accept` の出力を一時ファイルに書かず標準入力へ渡し、" +
            `\`tw brief acceptor ${taskId} --accept-file -\` を打つ。${imageSentence}`
          : `続けて \`tw brief acceptor ${taskId} --round ${round + 1} --since ${tree}\` を打つ（\`--accept-file\` は付けない）。${imageSentence}`) +
        "kind が STOP のときは brief を空文字にしてよい。" +
        "返すのは、kind・`tw accept` の出力全部（acceptLines）・`tw brief` の2行目以降の依頼文（brief）・tree。" +
        (first
          ? "tree は `tw accept` の出力の `SNAPSHOT` の行の木の SHA（その行が無ければ空）。"
          : "tree は `tw brief` の1行目の末尾の木の SHA。"),
      { agentType: "tsukumo-workflow:no-delegate", schema: ACCEPT_PREPARE, label: `accept-prepare-${round + 1}`, phase: acceptTitle },
    )
    if (prepared === null) {
      return { stopped: true, phase: "accept", reason: "受け入れの準備の agent が null を返した" }
    }
    if (prepared.kind === "STOP") {
      return { stopped: true, phase: "accept", reason: prepared.reason ?? prepared.acceptLines, prepared }
    }
    if (typeof prepared.tree === "string" && prepared.tree !== "") {
      tree = prepared.tree
    }
    const carried = first
      ? ""
      : `\n\n前回の受け入れ役の指摘の原文（直しが指摘に応えているかを照らす）:\n${JSON.stringify(judgment.findings)}\n` +
        `材料の値: friction=${judgment.friction} human=${judgment.human}（前回の判定の値）`
    const reports =
      reportSection("実装役の報告", result.implement) +
      fixes.map((fix, i) => reportSection(`直しの報告（${i + 1}回目）`, fix)).join("") +
      (first
        ? ""
        : "\n\n前の回と作業ツリーの木が同じ（直しがコードを変えず材料だけを返した）なら、diff が空でも差し戻さず、" +
          "上の報告の材料（理由・所見、検証、stepLines）で前回の指摘に応えたかを見て判定する。")
    judgment = await agent(prepared.brief + carried + reports, {
      agentType: "tsukumo-workflow:acceptor",
      schema: JUDGMENT,
      label: `acceptor-${round + 1}`,
      phase: acceptTitle,
    })
    if (judgment === null) {
      return { stopped: true, phase: "accept", reason: "受け入れ役が null を返した", prepared }
    }
    if (judgment.verdict === "accept") {
      break
    }
    if (round >= REJECT_LIMIT) {
      return { stopped: true, phase: "accept", reason: `差し戻しが上限（${REJECT_LIMIT}回）に達した`, judgment }
    }
    round += 1
    const fix = await agent(
      `${COMMON}\n受け入れ役の指摘を直す。指摘:\n${JSON.stringify(judgment.findings)}\n` +
        `直したら作業ツリー（着手した側。別のリポジトリのタスクなら \`tw brief implementer ${taskId}\` が言う作業先の作業ツリー）でコミットし、\`tw verify\` を打つ。` +
        "コミットのあとに打つ。打ったコマンド・結果の要点・通ったかを返す。" +
        "frictionLines には、この直しで気づいた点を `- <色>: <何が起きたか>（<根拠の場所>）` で1行以上入れる（何も無ければ `- 直し: friction log: なし`）。" +
        "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。",
      { agentType: "tsukumo-workflow:no-delegate", schema: IMPLEMENT, label: `fix-${round}`, phase: acceptTitle, ...modelOpt },
    )
    if (fix === null) {
      return { stopped: true, phase: "accept", reason: "直しの agent が null を返した" }
    }
    if (fix.stopped || !fix.verifyPassed) {
      return { stopped: true, phase: "accept", reason: fix.reason ?? "直しのあとの tw verify が通っていない", fix }
    }
    fixes.push(fix)
  }

  let closed = null
  if (prepared.kind === "REVIEW" || round > 0) {
    closed = await agent(
      `${COMMON}\n` +
        `受け入れ役が通した。\`tw accept ${taskId} --after-review\` を打って、整形・verify-check・accept の end を済ませる。` +
        "出力に NOT_VERIFIED が出たら `tw verify` を打ってから打ち直す。最後の行を verifyCheckLine に入れる。accept の end は出力に行が出ないので探さず、最後の行が `VERIFIED` か `VERIFIED_SAME` で始まれば closed を true にして返す（それ以外なら false）。",
      { agentType: "tsukumo-workflow:no-delegate", schema: ACCEPT_CLOSE, label: "accept-close", phase: acceptTitle },
    )
    if (closed === null || !closed.closed) {
      return { stopped: true, phase: "accept", reason: "受け入れの締めが済まなかった", closed }
    }
  }
  result.accept = { rounds: round, kind: prepared.kind, judgment, closed }
}

if (runs("retrospect")) {
  phase(retrospectTitle)
  const friction = result.accept?.judgment?.friction ?? "missing"
  const human = result.accept?.judgment?.human ?? 0
  const retro = await agent(
    `${COMMON}\n` +
      `振り返りの段。tsukumo-workflow:retrospect スキルを Skill ツールで読み、タスク ${taskId}・friction=${friction}・human=${human} で従う。` +
      "兆候が無ければ `- 振り返り: 兆候なし`、あれば物差しを通った候補を tw new --draft で積み、`- 振り返り:` の1行（積んだドラフトの ID を添える）を line に入れて返す。" +
      "作業ツリーのファイルは書き換えない。",
    { agentType: "tsukumo-workflow:retrospector", schema: RETRO, model: "haiku", label: "retrospect", phase: retrospectTitle },
  )
  if (retro === null) {
    return { stopped: true, phase: "retrospect", reason: "振り返りの agent が null を返した" }
  }
  result.retrospect = retro
}

return result
