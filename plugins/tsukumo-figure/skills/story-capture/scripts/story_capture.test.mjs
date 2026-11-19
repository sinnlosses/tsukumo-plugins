import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { main, selectStories } from "./story_capture.mjs";

const index = {
  v: 5,
  entries: {
    "button--primary": { id: "button--primary", type: "story", importPath: "./src/Button.stories.tsx" },
    "button--disabled": { id: "button--disabled", type: "story", importPath: "./src/Button.stories.tsx" },
    "card--default": { id: "card--default", type: "story", importPath: "./src/ui/Card.stories.tsx" },
    "button--docs": { id: "button--docs", type: "docs", importPath: "./src/Button.stories.tsx" },
  },
};

test("importPath が変えたファイルと一致する story を選ぶ", () => {
  const r = selectStories(index, ["src/Button.stories.tsx"]);
  assert.deepEqual(r.ids, ["button--primary", "button--disabled"]);
  assert.deepEqual(r.noStory, []);
});

test("一致が無ければ同じディレクトリの story に戻る", () => {
  const r = selectStories(index, ["src/ui/Card.tsx"]);
  assert.deepEqual(r.ids, ["card--default"]);
});

test("どれにも当たらないファイルは NO_STORY になる", () => {
  const r = selectStories(index, ["src/lib/util.ts", "src/Button.stories.tsx"]);
  assert.deepEqual(r.noStory, ["src/lib/util.ts"]);
  assert.deepEqual(r.ids, ["button--primary", "button--disabled"]);
});

test("select の口が git の差分から ID と NO_STORY の行を出す", async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "sc-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "ignore" });
  g("init", "-q", "-b", "main");
  g("config", "user.email", "t@example.com");
  g("config", "user.name", "t");
  fs.mkdirSync(path.join(repo, "src/ui"), { recursive: true });
  fs.writeFileSync(path.join(repo, "src/ui/Card.tsx"), "a");
  g("add", ".");
  g("commit", "-q", "-m", "init");
  fs.writeFileSync(path.join(repo, "src/ui/Card.tsx"), "b");
  fs.writeFileSync(path.join(repo, "other.ts"), "c");
  const idx = path.join(repo, "idx.json");
  fs.writeFileSync(idx, JSON.stringify(index));
  const lines = [];
  const code = await main(["select", "--repo", repo, "--base", "main", "--index", idx], (l) => lines.push(l));
  assert.equal(code, 0);
  assert.ok(lines.includes("card--default"));
  assert.ok(lines.includes("NO_STORY\tother.ts"));
});

test("引数の誤りは終了コード 2", async () => {
  const origErr = console.error;
  console.error = () => {};
  try {
    assert.equal(await main(["shoot", "--url", "x"], () => {}), 2);
    assert.equal(await main(["nope"], () => {}), 2);
  } finally {
    console.error = origErr;
  }
});

test("playwright が無ければ shoot は終了コード 3 で止まる", async () => {
  const bare = fs.mkdtempSync(path.join(os.tmpdir(), "sc-bare-"));
  const origErr = console.error;
  console.error = () => {};
  try {
    const code = await main(["shoot", "--url", bare, "--ids", "a--b", "--out", path.join(bare, "o"), "--label", "before", "--repo", bare], () => {});
    assert.equal(code, 3);
  } finally {
    console.error = origErr;
  }
});

test("base-worktree は build が落ちても一時の worktree を残さない", async () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "sc-wt-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" });
  g("init", "-q", "-b", "main");
  g("config", "user.email", "t@example.com");
  g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "a.txt"), "a");
  g("add", ".");
  g("commit", "-q", "-m", "init");
  fs.mkdirSync(path.join(repo, "node_modules/.bin"), { recursive: true });
  const bin = path.join(repo, "node_modules/.bin/storybook");
  fs.writeFileSync(bin, "#!/bin/sh\nexit 1\n");
  fs.chmodSync(bin, 0o755);
  const origErr = console.error;
  console.error = () => {};
  try {
    const code = await main(["base-worktree", "--repo", repo, "--base", "main", "--out", path.join(repo, "../sc-out")], () => {});
    assert.equal(code, 3);
  } finally {
    console.error = origErr;
  }
  const trees = g("worktree", "list", "--porcelain").split("\n").filter((l) => l.startsWith("worktree "));
  assert.equal(trees.length, 1);
});

const searchFrom = process.env.STORY_CAPTURE_PLAYWRIGHT_REPO ?? process.cwd();
let playwrightReadable = true;
try {
  createRequire(path.join(searchFrom, "noop.js")).resolve("playwright");
} catch {
  playwrightReadable = false;
}

test("静的出力の iframe.html から #storybook-root だけが PNG に撮れる", { skip: !playwrightReadable && "playwright が読めない" }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sc-static-"));
  fs.writeFileSync(
    path.join(dir, "iframe.html"),
    '<!doctype html><body style="margin:20px"><div id="storybook-root" style="width:120px;height:40px;background:#c33"></div><p>外</p></body>',
  );
  const out = path.join(dir, "out");
  const lines = [];
  const code = await main(["shoot", "--url", dir, "--ids", "x--y", "--out", out, "--label", "before", "--repo", searchFrom], (l) => lines.push(l));
  assert.equal(code, 0);
  const png = path.join(out, "before", "x--y.png");
  assert.equal(lines[0], `SHOT\tbefore\tx--y\t${png}`);
  const buf = fs.readFileSync(png);
  assert.equal(buf.readUInt32BE(16), 120);
  assert.equal(buf.readUInt32BE(20), 40);
});
