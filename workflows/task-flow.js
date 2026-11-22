export const meta = {
  name: "task-flow",
  description: "着手の印が立ったタスク1件を、実装 → 受け入れ → 振り返りの段で進める（送り出しは呼んだ側が tw finish で行う）",
  whenToUse: "tw claim が CLAIMED を返したタスク1件を進めるとき（計画の判定と書き直しは呼ぶ側が済ませてから）",
  phases: [
    { title: "Implement", detail: "model が opus なら先に contract-writer が完了条件の行ごとの確かめ方を申し送りに置き、implementer が ## やること の段を順に実装し、段ごとに phase_result を呼ぶ" },
    { title: "Accept", detail: "acceptor の起動と停止で hook（tw accept-hook）が tw accept の準備と締めを打ち、acceptor が判定する。差し戻しは上限2回。受け入れより後ろで最初に走る agent（独自の段か振り返り）が受け入れの phase_result も呼ぶ" },
    { title: "Retrospect", detail: "retrospector が retrospect スキルで兆候を判定し、phase_result を呼ぶ" },
    { title: "Custom step", detail: "並びの独自の段（workflow-step の印のあるスキル）ごとに step-runner を1体起こし、スキルを呼ばせる。stopped なら並びを止める" },
  ],
}

// args: { taskId, phaseIds, steps?, model?, from? }
//   taskId     着手したタスクの ID（GH-123 など）
//   phaseIds   tw plan-phases の JSON の phases（{ id, name } の並び。並列は配列）。phase() の題と agent() の phase は、この id からだけ作る
//   steps      走らせる段の並び（tw config の steps）。この順に走らせる。既定は ["implement", "accept", "retrospect"]。
//              ほかの値は独自の段（スキルの全名）で、phaseIds に同じ id が要る
//   model      実装・直しの agent の model（haiku・sonnet・opus。省くとメインの model）
//   from       呼び直すときに始める段（steps の値。並びに無ければ先頭から）
//   計画の判定と書き直しは呼ぶ側（メイン）が起こす前に済ませる。phaseIds に impl- の id が無く impl だけなら、計画が未確定なので起動を拒む
// 戻り値: { stopped: false, conventionLines?, implement, accept, retrospect, steps? } か { stopped: true, phase, reason }
//   steps は独自の段の値 → step-runner の返した { outcome, reason?, summary? }

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
    unverifiedCriteria: { type: "array", items: { type: "integer" } },
    images: { type: "array", items: { type: "string" } },
    stopped: { type: "boolean" },
    reason: { type: "string" },
  },
  required: ["changedFiles", "verifyCommand", "verifyResult", "verifyPassed", "stepLines", "conventionLines", "frictionLines", "unverifiedCriteria", "stopped"],
}

const VERIFY_KINDS = ["コマンド", "目視", "差分"]

const CONTRACT = {
  type: "object",
  properties: {
    criteria: {
      type: "array",
      items: {
        type: "object",
        properties: {
          line: { type: "integer" },
          kind: { type: "string", enum: VERIFY_KINDS },
          how: { type: "string" },
        },
        required: ["line", "kind", "how"],
      },
    },
    undecided: { type: "array", items: { type: "integer" } },
    reason: { type: "string" },
  },
  required: ["criteria", "undecided"],
}

const UNVERIFIED_SENTENCE =
  "`## 完了条件` のうち、打ったコマンドの出力か撮った画で確かめていない行の番号を unverifiedCriteria に入れる。全部確かめたら空配列。"

const SKIP_KINDS = ["SKIP", "NOTHING", "NO_RANGE"]

const JUDGMENT = {
  type: "object",
  properties: {
    verdict: { type: "string", enum: ["accept", "reject"] },
    acceptKind: { type: "string", enum: ["REVIEW", ...SKIP_KINDS, "STOP"] },
    tree: { type: "string" },
    reason: { type: "string" },
    findings: { type: "array", items: { type: "string" } },
    findingKinds: {
      type: "array",
      items: { type: "string", enum: ["完了条件", "正しさ", "規約", "簡素化"] },
    },
    visual: { type: "string" },
    friction: { type: "string" },
    human: { type: "number" },
    notes: { type: "string" },
  },
  required: ["verdict", "acceptKind", "findings", "findingKinds", "friction", "human"],
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

const STEP = {
  type: "object",
  properties: {
    outcome: { type: "string", enum: ["finished", "skipped", "stopped"] },
    reason: { type: "string" },
    summary: { type: "string" },
  },
  required: ["outcome"],
}

if (!args || typeof args.taskId !== "string") {
  throw new Error("args に taskId（文字列）が要る")
}
const taskId = args.taskId
const phaseIds = (Array.isArray(args.phaseIds) ? args.phaseIds : [])
  .flat()
  .map((p) => (typeof p === "string" ? p : p?.id))
  .filter((id) => typeof id === "string" && id !== "")
const findId = (match) => phaseIds.find(match)
const implIds = phaseIds.filter((id) => id.startsWith("impl-"))
const implCount = implIds.length
const steps = Array.isArray(args.steps) ? args.steps : ALL_STEPS
const startAt = steps.includes(args.from) ? steps.indexOf(args.from) : 0
const runs = (step) => steps.includes(step) && steps.indexOf(step) >= startAt
const acceptAt = steps.indexOf("accept")
const modelOpt = typeof args.model === "string" ? { model: args.model } : {}
// 実装の前の控えの回（完了条件の行ごとの確かめ方）を走らせる model。ほかの model と model の省略では走らせない
const CONTRACT_MODELS = ["opus"]
// 実装・直しの effort。skills/task-model/WORKFLOW.md の対応表の effort の列と同じ（載せていない model は既定のまま）
const EFFORT_BY_MODEL = { haiku: "medium" }
const effortOpt = Object.hasOwn(EFFORT_BY_MODEL, args.model) ? { effort: EFFORT_BY_MODEL[args.model] } : {}

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
const customTitles = Object.fromEntries(
  steps.filter((step) => !ALL_STEPS.includes(step) && runs(step)).map((step) => [step, titleOf(step, (id) => id === step)]),
)

// 受け入れ役の立場の agent が実装の前に完了条件の行ごとの確かめ方を決め、申し送り `確かめ方` に置く（実装役と受け入れ役が読む）
const runContract = async () => {
  if (!CONTRACT_MODELS.includes(args.model)) {
    return null
  }
  const contract = await agent(
    `タスク ${taskId} の実装の前の控えの回。作業ツリーのファイルは書き換えない。\`tw show ${taskId}\` の ## 完了条件 を行ごとに読み、` +
      `各行を実装のあとにどう確かめるか（種類は ${VERIFY_KINDS.join("・")}。コマンドなら打つコマンド、目視なら撮る場面と見るところ、差分なら読む箇所）を決める。` +
      "行番号は ## 完了条件 の番号（番号が無ければ上から 1 で数える）。決めたものを criteria に { line, kind, how } で入れ、" +
      "確かめ方を決められない行（文言が曖昧で、合否を分ける観点が1つに定まらない行）は criteria に入れず undecided に行番号を入れ、reason にわけを書く。" +
      `返す直前に \`tw relay ${taskId} 確かめ方 --body-file -\` の標準入力（一時ファイルに書かない）へ、criteria の行ごとに \`<line>\\t<kind>\\t<how>\` の1行を書く。` +
      "tw relay 以外の tw と検証コマンドは打たない。",
    { agentType: "tsukumo-workflow:contract-writer", schema: CONTRACT, label: "確かめ方", phase: implementTitle },
  )
  if (contract === null) {
    return { stopped: true, phase: "implement", reason: "控えの回の agent が null を返した" }
  }
  if (contract.undecided.length > 0) {
    return {
      stopped: true,
      phase: "implement",
      reason: `完了条件 ${contract.undecided.join(",")} の確かめ方を決められない: ${contract.reason ?? "（理由なし）"}`,
    }
  }
  return null
}

const runImplement = async () => {
  phase(implementTitle)
  const contractStop = await runContract()
  if (contractStop !== null) {
    return contractStop
  }
  const implement = await agent(
    `${COMMON}\n` +
      `最初に \`tw brief implementer ${taskId}\` を打ち、出力の2行目以降の決まりを読んで従う。` +
      `\`tw show ${taskId}\` の ## やること の段 1 から ${implCount} までを、番号の順に1段ずつ実装する。` +
      `最後の段より前の段を1つ済ませるたびに、\`tw step ${taskId} <段の番号>\` を打つ。` +
      "`STEPPED` の行が出るまで次の段へ進まない。`STEP_CHECK_FAILED` など終了コードが 0 でないときは、続く出力の指摘を直して打ち直す。" +
      `出た \`STEPPED\` の行は stepLines に1段1行で入れる（最後の段は \`tw verify\` で済むので \`STEPPED\` は要らない。行数は ${implCount - 1}）。` +
      "各段に入るたびに（済ませた・飛ばした・止めたの結果が出るたびに、必ず）phase_result を呼ぶ。呼ばずに次の段へ進まない。" +
      `## やること の段の番号と段の id の対応は ${implIds.map((id, i) => `段 ${i + 1} は ${id}`).join("、")}。` +
      '{ step: <その段の id>, outcome: "finished", summary: <その段でしたこと・分かったことを1〜2文> } で呼ぶ（位置の番号と段の数は渡さない）。' +
      "段が要らず飛ばすなら outcome を skipped にし、同じく step を渡す（summary は付けない）。" +
      "判断が想定より要ると分かったら押し切らず、その段の id を step に渡し、phase_result を outcome: stopped（reason に理由）で呼んで止め、" +
      "schema の stopped を true、reason に理由を入れて返す。" +
      `最後の段のあと \`tw verify\` を打ち、打ったコマンド・結果の要点・通ったかを返す。` +
      "返す欄は schema のとおり。draftLines には委譲先の報告に書く `- ドラフト:` の行があれば入れる。" +
      "conventionLines には、`tw brief implementer` の決まりに従って報告に書く `- 規約:` の行（あれば `- 規約の指摘:` の行も）を1行1要素で入れる。" +
      "frictionLines には、段ごとに `tw brief implementer` の決まりの friction log の書き方で1行ずつ入れる" +
      `（1段1行。何も起きなかった段は \`- 実装 段<n>: friction log: なし\`、起きた段はその段の色つきの行）。` +
      "段を skipped にしたときは、飛ばしたわけの1行を入れる。frictionLines は空にしない。" +
      UNVERIFIED_SENTENCE +
      "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。" +
      relaySentence("実装"),
    { agentType: "tsukumo-workflow:implementer", schema: IMPLEMENT, label: "実装", phase: implementTitle, ...modelOpt, ...effortOpt },
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
  return null
}

const runAccept = async () => {
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
    const unverified = (fixes[fixes.length - 1] ?? result.implement)?.unverifiedCriteria ?? []
    const unverifiedLine =
      unverified.length > 0
        ? `\n\n実装役が確かめていない完了条件: ${unverified.join(",")}。受け入れ役が自分で確かめ、確かめられなければ \`完了条件\` の指摘にする`
        : ""
    judgment = await agent(`${ACCEPTOR_REQUEST}\n${STOP_GATE}${carried}${reports}${unverifiedLine}`, {
      agentType: "tsukumo-workflow:acceptor",
      schema: JUDGMENT,
      label: `受け入れ ${round + 1}`,
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
        UNVERIFIED_SENTENCE +
        "画面や見た目を撮ったら、その画像の絶対パスを images に入れる。撮らなければ空配列。" +
        relaySentence("直し"),
      { agentType: "tsukumo-workflow:implementer", schema: IMPLEMENT, label: `直し ${round}`, phase: acceptTitle, ...modelOpt, ...effortOpt },
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
  return null
}

// 受け入れの申告は、受け入れより後ろで最初に走る agent（独自の段でも振り返りでも）が1回だけ行う
let acceptAnnounced = false
const acceptFinishedSentence = () => {
  if (acceptAnnounced || result.accept === undefined) {
    return ""
  }
  acceptAnnounced = true
  return SKIP_KINDS.includes(result.accept.kind)
      ? `最初に受け入れの phase_result を { step: "accept", outcome: "skipped" } で1回呼ぶ（summary は付けない。呼ぶ agent の段とは別の段なので step は明示で渡す。tw accept の判定が ${result.accept.kind}）。`
      : `最初に受け入れの phase_result を { step: "accept", outcome: "finished", summary: <受け入れの結果を1〜2文> } で1回呼ぶ` +
        `（呼ぶ agent の段とは別の段なので step は明示で渡す。受け入れ役は差し戻し ${result.accept.rounds} 回のあと通した。tw accept の判定は ${result.accept.kind}）。`
}

const runRetrospect = async () => {
  phase(retrospectTitle)
  const friction = result.accept?.judgment?.friction ?? "missing"
  const human = result.accept?.judgment?.human ?? 0
  const retro = await agent(
    `${COMMON}\n` +
      `振り返りの段。tsukumo-workflow:retrospect スキルを Skill ツールで読み、タスク ${taskId}・friction=${friction}・human=${human} で従う。` +
      "兆候が無ければ `- 振り返り: 兆候なし`、あれば物差しを通った候補を tw new --draft で積み、`- 振り返り:` の1行（積んだドラフトの ID を添える）を line に入れて返す。" +
      "作業ツリーのファイルは書き換えない。" +
      acceptFinishedSentence() +
      '返す前に phase_result を { outcome: "finished", summary: <line と同じ要点を1〜2文> } で1回呼ぶ（段は tsukumo が agent の段から補うので、位置の欄は渡さない）。',
    { agentType: "tsukumo-workflow:retrospector", schema: RETRO, model: "sonnet", effort: "medium", label: "振り返り", phase: retrospectTitle },
  )
  if (retro === null) {
    return { stopped: true, phase: "retrospect", reason: "振り返りの agent が null を返した" }
  }
  result.retrospect = retro
  return null
}

// 独自の段（workflow-step の印のあるスキル）。段ごとに step-runner を1体起こし、model はメインと同じにする
const runCustom = async (step) => {
  const title = customTitles[step]
  phase(title)
  const afterAccept = acceptAt >= 0 && steps.indexOf(step) > acceptAt
  const out = await agent(
    `${COMMON}\n` +
      `独自の段。Skill ツールで \`${step}\` を、タスクの ID ${taskId} を引数に1回呼んで従う。` +
      "スキルが最後に返した結果の語（finished・skipped・stopped）を outcome に、したこと・分かったことの1〜2文を summary（止めたなら reason）に入れて schema で返す。" +
      acceptFinishedSentence() +
      "返す前に phase_result を { outcome, reason?, summary? } で1回呼ぶ（位置の欄は渡さない）。" +
      (afterAccept ? "受け入れより後ろの段なので、作業ツリーのファイルを書き換えない。" : ""),
    { agentType: "tsukumo-workflow:step-runner", schema: STEP, label: step, phase: title },
  )
  if (out === null) {
    return { stopped: true, phase: step, reason: "独自の段の agent が null を返した" }
  }
  result.steps = { ...result.steps, [step]: out }
  if (out.outcome === "stopped") {
    return { stopped: true, phase: step, reason: out.reason ?? "独自の段の agent が止めた" }
  }
  return null
}

const RUNNERS = { implement: runImplement, accept: runAccept, retrospect: runRetrospect }
for (const step of steps.filter(runs)) {
  const stop = await (RUNNERS[step] ?? (() => runCustom(step)))()
  if (stop !== null) {
    return stop
  }
}

return result
