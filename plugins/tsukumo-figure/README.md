# tsukumo-figure

図の構造（節点と流れを書いた spec の JSON）から HTML を作り、図の部分だけを PNG に撮るプラグイン。使い方は `skills/figure-capture/SKILL.md`。

## 撮る段の前提

- PNG に撮る段（`figure_render.py --png`）は、Python の `playwright` と chromium が要る。
- 入れるのは人がやる。グローバルな導入にあたるので、エージェントは入れない。
- 入っていない環境では撮る段が終了コード 3 で止まる。`--png` を外せば HTML までは作れる。
