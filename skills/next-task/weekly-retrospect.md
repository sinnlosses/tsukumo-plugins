# 週ごとの振り返り

選ぶ前に `tsukumo-workflow:retrospect` を Skill ツールで呼び、その副ファイル `detail.md` の節「週ごとに振り返る」**だけ**を読んで1回行う。`weekly.py` は retrospect の置き場から打つ（`/loop` から
   回っているときも。出力はドラフトの課題と記録だけなので人がいなくてよい）。記録を1コミットにして
   （ドラフトは Beads に積むのでコミットに入らない）`tw ship` で送ってから、`tw status` を取り直して手順2へ。`tw ship` が `SHIPPED` 以外なら、出力を
   添えて終了する（先頭語の意味は `task-model` の `tw-reference.md` の終了コード表）
