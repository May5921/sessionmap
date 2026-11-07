# sessionmap

> This project lost most of its memory and kept going.

sessionmap turns a coding workspace into a chronological project story. It reads the local ZCode history, extracts meaningful decisions, marks context breaks, and lets you open the original evidence behind each event.

The product boundary is deliberate: sessionmap is a **cross-session project memory layer**, not another per-run agent observability dashboard. It explains how a project arrived at its current state, including abandoned decisions, context breaks, Git evidence, and what to resume next.

See [COMPETITION.md](COMPETITION.md) for the verified adjacent products and the resulting scope.

## Demo

Open `demo/index.html` or serve this directory locally:

```powershell
python -m http.server 8767
```

Then open `http://127.0.0.1:8767/demo/`.

The shareable product story is [landing.html](landing.html).

## Refresh the full pipeline

The fast path refreshes local facts, the project view, the persisted semantic
layer, and the event line without calling a model again:

```powershell
python pipeline.py pi-mini
```

Add `--extract --cli pi` when you intentionally want to re-run semantic
extraction through the local CLI.

在已有语义基线后，可以用 `--extract --incremental` 只把事实变化映射到受影响节点；`matched` 会直接复用旧结果，`baseline/unavailable` 会回退到完整提炼。

如果本机代理环境不被 CLI 接受，可加 `--no-proxy`；它只影响本次子进程，不修改系统配置。

For an existing semantic baseline, add `--incremental` to send only the
changed evidence and affected decision context to the local CLI. A matched
refresh reuses the previous result without a model call; a missing baseline
falls back to the explicit full extraction path.

持续刷新事实和事件线（不调用模型）可以运行：

```powershell
python watch.py pi-mini --interval 5 --timeline timeline.json
```

新事实会先以“待提炼”节点出现；需要改变项目语义时，再显式运行
`pipeline.py --extract`。这样自动刷新不会把未验证的模型推断写进项目故事。

## Current scope

The demo uses a real ZCode workspace (`pi-mini`) as the semantic gold sample. The same canonical event contract now has read-only adapters for Claude, Codex, Pi, DSH and OpenCode. Compressed Codex and DSH streams use the local zstd decoder and clearly report their scan cost. The event line is the product surface; raw events, evidence links, Git facts, and analysis diagnostics stay behind it.

The current story is generated from local records. It does not claim that a viral launch is guaranteed.

## 数据流

```text
source adapter → canonical events → semantic decisions/evidence → canonical timeline → event line
```

`canonical_timeline.py` 是工具无关的展示投影。它优先读取 `sessionmap.db` 里的最新语义运行，只有在语义层尚未建立时才兼容读取 `timeline.json`。这让不同工具可以共享同一条事实、语义和证据链。

语义层还会为每次运行保存事实指纹快照和决策节点 manifest。下一次刷新时，`semantic.py --plan` 会把新增、删除或指纹变化的事件映射回上一轮的证据决策，并输出受影响范围；节点 manifest 会标记 `reuse`、`refresh` 或 `removed`。`pipeline.py --extract --incremental` 使用这个只读计划，只把变化事实交给本机 CLI，再把返回的决策 patch 合并回旧结果；matched 时不调用模型。没有历史快照时会明确标记为 `unavailable`，并回退到完整提炼，不会假装完成增量判断。

```powershell
python canonical_timeline.py sessionmap.db --workspace-id <id>
```

其他工具可以走同一条事实流水线：

```powershell
python adapter_pipeline.py pi --workspace pi-mini --store sessionmap-pi.db --out pi-report.html
python adapter_pipeline.py dsh --workspace pi-mini --store sessionmap-dsh.db --out dsh-report.html
```

同一个仓库由多个 Agent 工具共同维护时，先把各来源的本地 canonical store 合并到一个工作区：

```powershell
python workspace_merge.py sessionmap.db sessionmap-codex.db sessionmap-pi.db sessionmap-dsh.db `
  --root C:\Users\Luke\projects\pi-mini --out output/pi-mini-merged.db
```

合并层保留来源、会话和原始引用；它不会修改任何源库。

事件线默认把语义决策作为主骨，再补充最多 18 条未被决策引用的高信号事实（用户消息、Todo、错误、Git、子 Agent 和记忆断裂）。补充事件使用低对比样式，可以一键隐藏。

项目快照和人工修正也保持在本地事实链上：

```powershell
python compare.py before-view.json project-view.json --out compare.json
python consistency.py project-view.json --out consistency.json
python search.py sessionmap.db "切换工作区" --limit 20
python override.py sessionmap.db D11 --status done --why "已由 Git 和用户原话确认"
python privacy.py project-view.json --out share-view.json
```

`compare.py` 只报告新增、删除、状态变化、事实变化和 Git 字段变化；`consistency.py` 只报告能由当前数据证明的缺口；`override.py` 只写语义层修正表，不改源会话和 Git 仓库。

没有语义运行时，统一事实流水线仍会生成少量可下钻的事实节点，并明确标记为 `facts-only`，不会把事实包装成模型决策。
