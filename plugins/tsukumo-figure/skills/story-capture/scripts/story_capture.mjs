#!/usr/bin/env node
// 変えたファイルに当たる Storybook の story を選び、story 1件ずつの #storybook-root を PNG に撮る。
// 口: select / shoot / base-worktree。終了コード 0 撮れた・2 引数の誤り・3 撮れない環境。
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

export class ExitError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

const normalize = (p) => p.replace(/\\/g, "/").replace(/^\.\//, "");

// Storybook の importPath は index.json の根からの相対で、repo の根とずれることがある（モノレポ）。
// 末尾が一致する長い側を優先して照らす。
function samePath(importPath, changed) {
  const a = normalize(importPath);
  const b = normalize(changed);
  return a === b || a.endsWith(`/${b}`) || b.endsWith(`/${a}`);
}

/** index.json の entries と変えたファイルから、`{ ids, noStory }` を返す。 */
export function selectStories(index, changedFiles) {
  const stories = Object.values(index.entries ?? {}).filter(
    (e) => e.type === "story" && typeof e.importPath === "string",
  );
  const ids = [];
  const noStory = [];
  for (const file of changedFiles) {
    let hit = stories.filter((s) => samePath(s.importPath, file));
    if (hit.length === 0) {
      const dir = path.posix.dirname(normalize(file));
      hit = stories.filter((s) => samePath(path.posix.dirname(normalize(s.importPath)), dir));
    }
    if (hit.length === 0) noStory.push(file);
    for (const s of hit) if (!ids.includes(s.id)) ids.push(s.id);
  }
  return { ids, noStory };
}

function git(repo, args) {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function defaultBase(repo) {
  for (const ref of ["origin/main", "main", "origin/master", "master"]) {
    try {
      git(repo, ["rev-parse", "--verify", "--quiet", ref]);
      return ref;
    } catch {
      /* 次の候補へ */
    }
  }
  throw new ExitError(2, "主ブランチが見つからない。--base で渡す");
}

export function changedFiles(repo, base) {
  const lines = [
    ...git(repo, ["diff", "--name-only", `${base}...HEAD`]).split("\n"),
    ...git(repo, ["diff", "--name-only", "HEAD"]).split("\n"),
    ...git(repo, ["ls-files", "--others", "--exclude-standard"]).split("\n"),
  ];
  return [...new Set(lines.map((l) => l.trim()).filter(Boolean))];
}

async function readIndex(source, repo) {
  if (/^https?:\/\//.test(source)) {
    const res = await fetch(source).catch(() => null);
    if (!res?.ok) throw new ExitError(3, `index.json を取れない: ${source}`);
    return res.json();
  }
  const file = fs.statSync(source, { throwIfNoEntry: false })?.isDirectory() ? path.join(source, "index.json") : source;
  if (!fs.existsSync(file)) throw new ExitError(3, `index.json が無い: ${file}（repo: ${repo}）`);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function parseArgs(argv, spec) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    if (!key.startsWith("--") || !spec.includes(key.slice(2)) || argv[i + 1] === undefined) {
      throw new ExitError(2, `引数の誤り: ${key}`);
    }
    out[key.slice(2)] = argv[i + 1];
  }
  return out;
}

function need(args, names) {
  for (const n of names) if (!args[n]) throw new ExitError(2, `--${n} が要る`);
}

async function cmdSelect(argv, log) {
  const args = parseArgs(argv, ["repo", "base", "index"]);
  need(args, ["repo"]);
  const repo = path.resolve(args.repo);
  const base = args.base ?? defaultBase(repo);
  const index = args.index ?? (fs.existsSync(path.join(repo, "storybook-static", "index.json")) ? path.join(repo, "storybook-static") : "http://localhost:6006/index.json");
  const files = changedFiles(repo, base);
  const { ids, noStory } = selectStories(await readIndex(index, repo), files);
  for (const id of ids) log(id);
  for (const f of noStory) log(`NO_STORY\t${f}`);
  return 0;
}

const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };

export function serveStatic(dir) {
  const root = path.resolve(dir);
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(new URL(req.url, "http://x").pathname);
    const file = path.join(root, rel === "/" ? "index.html" : rel);
    if (!file.startsWith(root) || !fs.statSync(file, { throwIfNoEntry: false })?.isFile()) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() }));
  });
}

function loadPlaywright(repo) {
  try {
    return createRequire(path.join(repo, "noop.js"))("playwright");
  } catch {
    throw new ExitError(3, `${repo} の node_modules に playwright が無い。導入は人がやる（このスクリプトは取得しない）`);
  }
}

export async function shoot({ url, ids, out, label, repo = process.cwd() }, log) {
  const { chromium } = loadPlaywright(path.resolve(repo));
  const isDir = !/^https?:\/\//.test(url);
  const server = isDir ? await serveStatic(url) : null;
  const root = (server?.url ?? url).replace(/\/$/, "");
  let browser;
  try {
    try {
      browser = await chromium.launch();
    } catch (e) {
      throw new ExitError(3, `chromium を起こせない: ${String(e.message).split("\n")[0]}`);
    }
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const dest = path.join(out, label);
    fs.mkdirSync(dest, { recursive: true });
    for (const id of ids) {
      const page = await context.newPage();
      try {
        const res = await page.goto(`${root}/iframe.html?id=${encodeURIComponent(id)}&viewMode=story`, { waitUntil: "networkidle" }).catch((e) => {
          throw new ExitError(3, `Storybook に届かない: ${root}（${String(e.message).split("\n")[0]}）`);
        });
        if (!res?.ok()) throw new ExitError(3, `iframe.html を開けない: ${res?.status()}`);
        await page.evaluate(() => document.fonts.ready);
        const file = path.join(dest, `${id}.png`);
        await page.locator("#storybook-root").screenshot({ path: file, animations: "disabled" });
        log(`SHOT\t${label}\t${id}\t${file}`);
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser?.close();
    server?.close();
  }
}

async function cmdShoot(argv, log) {
  const args = parseArgs(argv, ["url", "ids", "out", "label", "repo"]);
  need(args, ["url", "ids", "out", "label"]);
  if (!["before", "after"].includes(args.label)) throw new ExitError(2, "--label は before か after");
  const ids = args.ids.split(",").map((s) => s.trim()).filter(Boolean);
  if (ids.length === 0) throw new ExitError(2, "--ids が空");
  await shoot({ url: args.url, ids, out: path.resolve(args.out), label: args.label, repo: args.repo }, log);
  return 0;
}

async function cmdBaseWorktree(argv, log) {
  const args = parseArgs(argv, ["repo", "base", "out"]);
  need(args, ["repo", "base", "out"]);
  const repo = path.resolve(args.repo);
  const out = path.resolve(args.out);
  const wt = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "story-capture-base-")), "wt");
  let added = false;
  const cleanup = () => {
    if (added) {
      added = false;
      spawnSync("git", ["-C", repo, "worktree", "remove", "--force", wt], { stdio: "ignore" });
    }
    fs.rmSync(path.dirname(wt), { recursive: true, force: true });
  };
  const onSignal = (sig) => () => {
    cleanup();
    process.exit(128 + (sig === "SIGINT" ? 2 : 15));
  };
  process.once("SIGINT", onSignal("SIGINT"));
  process.once("SIGTERM", onSignal("SIGTERM"));
  try {
    git(repo, ["worktree", "add", "--detach", wt, args.base]);
    added = true;
    const nm = path.join(repo, "node_modules");
    if (!fs.existsSync(nm)) throw new ExitError(3, `${nm} が無い。依存の導入は人がやる`);
    fs.symlinkSync(nm, path.join(wt, "node_modules"), "dir");
    const bin = path.join(nm, ".bin", "storybook");
    if (!fs.existsSync(bin)) throw new ExitError(3, "node_modules/.bin/storybook が無い");
    fs.mkdirSync(out, { recursive: true });
    const built = spawnSync(bin, ["build", "-o", out], { cwd: wt, stdio: ["ignore", "ignore", "inherit"] });
    if (built.status !== 0) throw new ExitError(3, `storybook build が落ちた（終了 ${built.status}）`);
    log(`BUILT\t${out}`);
    return 0;
  } catch (e) {
    if (e instanceof ExitError) throw e;
    throw new ExitError(3, `主ブランチの build に失敗: ${String(e.message).split("\n")[0]}`);
  } finally {
    cleanup();
  }
}

export async function main(argv, log = console.log) {
  const [cmd, ...rest] = argv;
  try {
    if (cmd === "select") return await cmdSelect(rest, log);
    if (cmd === "shoot") return await cmdShoot(rest, log);
    if (cmd === "base-worktree") return await cmdBaseWorktree(rest, log);
    throw new ExitError(2, "使い方: story_capture.mjs select|shoot|base-worktree …");
  } catch (e) {
    if (e instanceof ExitError) {
      console.error(e.message);
      return e.code;
    }
    throw e;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  process.exitCode = await main(process.argv.slice(2));
}
