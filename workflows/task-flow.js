export const meta = {
  name: "task-flow",
  description: "着手の印が立ったタスク1件を、実装 → 受け入れ → 振り返りの段で進める（送り出しは呼んだ側が tw finish で行う）",
  whenToUse: "tw claim が CLAIMED を返したタスク1件を進めるとき（計画の判定と書き直しは呼ぶ側が済ませてから）",
  phases: [
    { title: "Implement", detail: "no-delegate が ## やること の段を順に実装し、段ごとに phase_result を呼ぶ" },
    { title: "Accept", detail: "acceptor の起動と停止で hook（tw accept-hook）が tw accept の準備と締めを打ち、acceptor が判定する。差し戻しは上限2回。範囲に入っていれば振り返りの agent が受け入れの phase_result も呼ぶ" },
    { title: "Retrospect", detail: "retrospector が retrospect スキルで兆候を判定し、範囲に入っていれば phase_result を呼ぶ" },
  ],
}

// args: { taskId, phaseCount, phaseIds, steps?, model?, from? }
//   taskId     着手したタスクの ID（GH-123 など）
//   phaseIds   tw plan-phases の JSON の phases（{ id, name } の並び。並列は配列）。phase() の題と agent() の phase は、この id からだけ作る
//   phaseCount tw plan-phases の delegatedRange.count（範囲の段の数。phase_result の count に渡す）。flow が script なら実装の段に受け入れ・振り返りを足した数
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

const SKIP_KINDS = ["SKIP", "NOTHING", "NO_RANGE"]

const JUDGMENT = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["accept", "reject"] },
    acceptKind: { type: "string", enum: ["REVIEW", ...SKIP_KINDS, "STOP"] },
    tree: { type: "string" },
    reason: { type: "string" },
    findings: { type: "array", items: { type: "string" } },
    visual: { type: "string" },
    friction: { type: "string" },
    human: { type: "number" },
    notes: { type: "string" },
  },
  required: ["verdict", "acceptKind", "findings", "friction", "human"],
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
const implCount = phaseIds.filter((id) => id.startsWith("impl-")).length
const lastImpl = phaseIds.findLastIndex((id) => id.startsWith("impl-"))
const rangeTail = lastImpl < 0 ? [] : phaseIds.slice(lastImpl + 1, lastImpl + 1 + Math.max(0, phaseCount - implCount))
const rangeNumber = (id) => (rangeTail.includes(id) ? implCount + 1 + rangeTail.indexOf(id) : null)
const acceptNumber = rangeNumber("accept")
const retrospectNumber = rangeNumber("retrospect")
const steps = Array.isArray(args.steps) ? args.steps : ALL_STEPS
const startAt = ALL_STEPS.includes(args.from) ? ALL_STEPS.indexOf(args.from) : 0
const runs = (step) => steps.includes(step) && ALL_STEPS.indexOf(step) >= startAt
const modelOpt = typeof args.model === "string" ? { model: args.model } : {}

const STOP_GATE =
  "返そうとして phase_result の関門に残りの段の申告を求めて止められても、自分の段の phase_result 以外は呼ばず、同じ返却を返し直す。"

const COMMON =
  `タスク ${taskId} は着手の印が立っている。作業ツリーは今の cwd。ファイルの書き換えは Edit・Write で行う。` +
  "tsukumo の phase_result がツール一覧に無いときは ToolSearch で select:mcp__tsukumo__phase_result を引いてから呼ぶ。" +
  "`vendor/tsukumo-plugins` は読み込み元なので、`git submodule update`・`git checkout` などで動かさない（`tw` が submodule のずれで止まっても、そのまま報告する）。" +
  STOP_GATE

// 受け入れ役の起動の hook（tw accept-hook）が tw brief acceptor で読む申し送り
const relaySentence = (key) =>
  `返す直前に、着手した作業ツリーで \`tw relay ${taskId} ${key} --body-file -\` の標準入力（一時ファイルに書かない）へ、` +
  "frictionLines・draftLines・conventionLines の行と、images の画像ごとに `- 画像: <絶対パス>` の行をそのまま書く。"

const ACCEPTOR_REQUEST =
  `タスク ${taskId} の受け入れ役。起動時の文脈（hook が渡した追加の文脈）の1行目が判定の行、続く行が \`tw accept\` の出力と依頼文。依頼文に従って判定する。` +
  "判定の行の最初の語を acceptKind に、`SNAPSHOT` の行の木の SHA を tree に写す（その行が無ければ tree は空）。" +
  "1行目が `STOP` で始まるか判定の行が無ければ、acceptKind を STOP にし、reason にその行（無ければ「起動時の文脈に受け入れの準備が無い」）を入れ、verdict は reject でよい。"

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
      `\`tw show ${taskId}\` の ## やること の段 1 から ${implCount} までを、番号の順に1段ずつ実装する。` +
      `最後の段より前の段を1つ済ませるたびに、\`tw step ${taskId} <段の番号>\` を打つ。` +
      "`STEPPED` の行が出るまで次の段へ進まない。`STEP_CHECK_FAILED` など終了コードが 0 でないときは、続く出力の指摘を直して打ち直す。" +
      `出た \`STEPPED\` の行は stepLines に1段1行で入れる（最後の段は \`tw verify\` で済むので \`STEPPED\` は要らない。行数は ${implCount - 1}）。` +
      "各段に入るたびに（済ませた・飛ばした・止めたの結果が出るたびに、必ず）phase_result を呼ぶ。呼ばずに次の段へ進まない。" +
      `{ phase: <段の番号>, count: ${phaseCount}, outcome: "finished", summary: <その段でしたこと・分かったことを1〜2文> } で呼ぶ` +
      "（count は範囲の段の数で、## やること の段の数と違ってもこの値を渡す）。" +
      "段が要らず飛ばすなら outcome を skipped にする（summary は付けない）。" +
      "判断が想定より要ると分かったら押し切らず、その段で phase_result を outcome: stopped（reason に理由）で呼んで止め、" +
      "schema の stopped を true、reason に理由を入れて返す。" +
      `最後の段のあと \`tw verify\` を打ち、打ったコマンド・結果の要点・通ったかを返す。` +
      "返す欄は schema のとおり。draftLines には委譲先の報告に書く `- ドラフト:` の行があれば入れる。" +
      "conventionLines には、`tw brief implementer` の決まりに従って報告に書く `- 規約:` の行（あれば `- 規約の指摘:` の行も）を1行1要素で入れる。" +
      "frictionLines には、段ごとに `tw brief implementer` の決まりの `## friction log` の書き方で1行ずつ入れる" +
      `（1段1行。何も起きなかった段は \`- 実装 段<n>: friction log: なし\`、起きた段はその段の色つきの行）。` +
      "段を skipped にしたときは、飛ばしたわけの1行を入れる。frictionLines は空にしない。" +
      "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。" +
      relaySentence("実装"),
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
  if (stepped < implCount - 1) {
    return {
      stopped: true,
      phase: "implement",
      reason: `tw step の STEPPED が ${stepped} 行しかない（${implCount - 1} 行が要る）。途中の段の控えが欠けている`,
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
  let judgment = null
  const fixes = []
  const reportSection = (heading, report) =>
    report
      ? `\n\n## ${heading}\n理由・所見: ${report.reason ?? "（なし）"}\n` +
        `検証: ${report.verifyCommand ?? ""} ${report.verifyResult ?? ""}\n` +
        `段の記録（stepLines）:\n${(report.stepLines ?? []).join("\n")}`
      : ""
  while (true) {
    const first = round === 0
    const previousTree = judgment?.tree ?? ""
    const carried = first
      ? ""
      : `\n\n前回の受け入れ役の指摘の原文（直しが指摘に応えているかを照らす）:\n${JSON.stringify(judgment.findings)}\n` +
        `材料の値: friction=${judgment.friction} human=${judgment.human}（前回の判定の値）`
    // 1周目は実装役の報告を渡さない。木は起動の hook が決めるので、前の回と同じかは受け入れ役が照らす
    const reports =
      !first && previousTree !== ""
        ? reportSection(`直しの報告（${fixes.length}回目）`, fixes[fixes.length - 1]) +
          `\n\n起動時の文脈の SNAPSHOT の木が前の回の木 ${previousTree} と同じ（直しがコードを変えず材料だけを返した）ときだけ、diff が空でも差し戻さず、` +
          "上の報告の材料（理由・所見、検証、stepLines）で前回の指摘に応えたかを見て判定する。木が違えば上の報告は読まずに判定する。"
        : ""
    judgment = await agent(`${ACCEPTOR_REQUEST}\n${STOP_GATE}${carried}${reports}`, {
      agentType: "tsukumo-workflow:acceptor",
      schema: JUDGMENT,
      label: `acceptor-${round + 1}`,
      phase: acceptTitle,
    })
    if (judgment === null) {
      return { stopped: true, phase: "accept", reason: "受け入れ役が null を返した" }
    }
    if (judgment.acceptKind === "STOP") {
      return { stopped: true, phase: "accept", reason: judgment.reason ?? "受け入れの準備（tw accept-hook）が止まった", judgment }
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
        "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。" +
        relaySentence("直し"),
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

  result.accept = { rounds: round, kind: judgment.acceptKind, judgment }
}

const acceptFinishedSentence =
  result.accept === undefined || acceptNumber === null
    ? ""
    : SKIP_KINDS.includes(result.accept.kind)
      ? `最初に受け入れの phase_result を { phase: ${acceptNumber}, count: ${phaseCount}, outcome: "skipped" } で1回呼ぶ（summary は付けない。tw accept の判定が ${result.accept.kind}）。`
      : `最初に受け入れの phase_result を { phase: ${acceptNumber}, count: ${phaseCount}, outcome: "finished", summary: <受け入れの結果を1〜2文> } で1回呼ぶ` +
        `（受け入れ役は差し戻し ${result.accept.rounds} 回のあと通した。tw accept の判定は ${result.accept.kind}）。`

if (runs("retrospect")) {
  phase(retrospectTitle)
  const friction = result.accept?.judgment?.friction ?? "missing"
  const human = result.accept?.judgment?.human ?? 0
  const retro = await agent(
    `${COMMON}\n` +
      `振り返りの段。tsukumo-workflow:retrospect スキルを Skill ツールで読み、タスク ${taskId}・friction=${friction}・human=${human} で従う。` +
      "兆候が無ければ `- 振り返り: 兆候なし`、あれば物差しを通った候補を tw new --draft で積み、`- 振り返り:` の1行（積んだドラフトの ID を添える）を line に入れて返す。" +
      "作業ツリーのファイルは書き換えない。" +
      acceptFinishedSentence +
      (retrospectNumber === null
        ? ""
        : `返す前に phase_result を { phase: ${retrospectNumber}, count: ${phaseCount}, outcome: "finished", summary: <line と同じ要点を1〜2文> } で1回呼ぶ。`),
    { agentType: "tsukumo-workflow:retrospector", schema: RETRO, model: "haiku", label: "retrospect", phase: retrospectTitle },
  )
  if (retro === null) {
    return { stopped: true, phase: "retrospect", reason: "振り返りの agent が null を返した" }
  }
  result.retrospect = retro
}

return result
