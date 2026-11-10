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

超长时间线可以使用 `extract.py --chunked --chunk-chars 12000` 按日期/会话边界分片提炼，再合并决策；任何分片失败都会明确失败，不会生成半截语义结果。非分片模式遇到超过 `--max-chars` 的输入会直接失败，避免静默丢失后半段；只有明确添加 `--allow-truncate` 才会截断。

分片结果会保存完整输入的 SHA-256、分片编号和原始决策 ID，便于核对每个判断来自哪一段事实。完整长时间线仍可能受本机 CLI 的上下文和网络配置限制；遇到超时应使用较小分片或增量提炼。

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

规则时间线保留主会话的叙事骨架，并从每个主会话的子 Agent 会话中追加少量按时间排序、去重的高信号用户文本；子会话的完整工具流水线仍留在 canonical store 和证据抽屉中。

## Current scope

The demo uses a real ZCode workspace (`pi-mini`) as the semantic gold sample. The same canonical event contract now has read-only adapters for Claude, Codex, Pi, DSH and OpenCode. Compressed Codex and DSH streams use the local zstd decoder and clearly report their scan cost. The event line is the product surface; raw events, evidence links, Git facts, and analysis diagnostics stay behind it.

The current story is generated from local records. It does not claim that a viral launch is guaranteed.

## 数据流

```text
source adapter → canonical events → progress spine → semantic decisions/evidence → canonical timeline → event line
```

`progress.py` 是确定性的项目推进骨架：工作项来自主会话边界与文件重叠，里程碑来自 git 发布提交，状态来自 TODO 状态机和会话结果。它只读事实、不调用模型，所以快、可复现、可回退。`canonical_timeline.py` 是工具无关的展示投影，优先读取 `sessionmap.db` 里的最新语义运行，只有在语义层尚未建立时才兼容读取 `timeline.json`。

事件线以工作项和里程碑为骨架，语义决策降为注解；低信心不等于事实，未收口事项和记忆断裂留在骨架上。

语义层还会为每次运行保存事实指纹快照和决策节点 manifest。下一次刷新时，`semantic.py --plan` 会把新增、删除或指纹变化的事件映射回上一轮的证据决策，并输出受影响范围；节点 manifest 会标记 `reuse`、`refresh` 或 `removed`。`pipeline.py --extract --incremental` 使用这个只读计划，只把变化事实交给本机 CLI，再把返回的决策 patch 合并回旧结果；matched 时不调用模型。没有历史快照时会明确标记为 `unavailable`，并回退到完整提炼，不会假装完成增量判断。

```powershell
python canonical_timeline.py sessionmap.db --workspace-id <id>
```

其他工具可以走同一条事实流水线：

```powershell
python adapter_pipeline.py pi --workspace pi-mini --store sessionmap-pi.db --out pi-report.html
python adapter_pipeline.py dsh --workspace pi-mini --store sessionmap-dsh.db --out dsh-report.html
```

`pipeline.py` 和 `watch.py` 默认会发现同一仓库根目录下的多个 canonical store 并自动合并；需要单源调试时加 `--no-merge`。也可以手动合并：

```powershell
python workspace_merge.py sessionmap.db sessionmap-codex.db sessionmap-pi.db sessionmap-dsh.db `
  --root C:\Users\Luke\projects\pi-mini --out output/pi-mini-merged.db
```

合并层保留来源、会话和原始引用；它不会修改任何源库。

事件线默认把语义决策作为主骨，再补充最多 18 条未被决策引用的高信号事实（用户消息、Todo、错误、Git、子 Agent 和记忆断裂）。每条补充事实会按会话、来源、文本相关性和时间距离归属到最近的主事件，并保留关联分数与理由；低于 0.55 的事实不伪装成主事件证据，首屏默认只展示已归属或高信号的补充节点，其余事实仍留在事实层。页面把补充节点嵌在对应主事件下面，可以一键隐藏。点击证据后可继续打开同目录的 `evidence.html`，查看原始引用和完整事实载荷。

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
