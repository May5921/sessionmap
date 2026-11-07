# sessionmap 验证方法

验证分成四层：适配器契约、源库事实、语义黄金样本、页面输出。

## 运行

```powershell
python sync.py pi-mini --out sessionmap.db
python view.py sessionmap.db timeline.json --out project-view.json
python semantic.py sessionmap.db timeline.json --view project-view.json
python semantic.py sessionmap.db timeline.json --plan --plan-out semantic-incremental-plan.json
python extract.py timeline.md --incremental-from timeline.json --incremental-plan semantic-incremental-plan.json --out timeline.json
python eventline_dashboard.py project-view.json --out report.html
python verify.py
python -m unittest discover -s tests -t . -p "test_*.py"
python adapters.py --home $env:USERPROFILE
python consistency.py project-view.json
python watch.py pi-mini --once --timeline timeline.json --out output/watch-smoke
```

适配器契约覆盖 ZCode、Claude、Codex、Pi、DSH 和 OpenCode。真实本机探测结果：六种来源均为 `ready`。Codex 和 DSH 使用本机 `zstd.exe` 只读解压，游标是解压后的行号，因此刷新时仍需扫描压缩流。

Claude 的原始 `type` 会统一映射为 canonical event type，并在 payload 中保留 `raw_kind`；文件适配器遇到无法读取的会话会返回 `partial`、`skipped_sessions` 和 `errors`，不会把不完整导入报告为成功。

`verify.py` 只读访问 ZCode 源库，检查：

- 会话、主会话、子会话、工具调用、用户消息、Todo 和压缩事件是否与源库一致；
- 决策数量、顺序和 ID 是否稳定；
- `pi-mini` 黄金样本中的关键决策、状态、工作线和用户证据是否匹配；
- 每个决策是否至少有一条可回溯证据；
- 记忆断裂是否被正确合并；
- HTML 中事件和断裂标记的数量是否正确。
- 语义层是否按输入哈希幂等写入分析运行、决策和证据链接。

当前自动化单元测试为 **40 passed**，覆盖六种适配器的统一事件契约、幂等导入、内容指纹变化、部分导入告警、跨来源工作区合并与去重、合并语义备注保留、canonical timeline、facts-only fallback、子会话高信号文本提取、语义节点 manifest、增量语义计划与合并、分片语义切分与合并、超限输入拒绝、小事件归属与嵌入、未归属事实展示、证据深链、快照对比、矛盾检查、人工修正、搜索、脱敏导出、待提炼事实和首屏事件线状态。

自动合并 smoke 会在同一仓库根目录下发现 ZCode、Claude、Codex、Pi、DSH 和 OpenCode 的 canonical store，输出 `sessionmap-merged.db`、`merged-project-view.json`、`merged-canonical-timeline.json`、`report.html` 和 `evidence.html`。合并只读源库，并按 Git commit SHA 等稳定事实去重；跨工具相同事实会保留来源列表。

黄金样本位于 `tests/gold_pi_mini.json`。它只固定少量高价值节点，避免把整个模型输出锁死；新增或修改提炼逻辑时，先更新分析，再明确更新黄金样本。

## 第二工作区压力基线

2026-11-07 对 `D:\shop-transfer-mini-program` 做了只读事实层基线，结果保存在 `benchmarks/shop-transfer-2026-11-07.json`：

- 82 个会话（16 主 / 66 子）；19,043 个统一事件；225 条 Todo；8 次去重后的上下文压缩；
- 首次同步约 26.5 秒，重复同步约 8.9 秒；规则时间线约 12.3 秒；事实视图约 1.1 秒；
- `sessionmap.db` 约 37.1 MB；事件指纹和来源 ID 均无重复；
- Git 分支为 `frontend-compact`，258 个改动文件，其中 242 个未跟踪；
- 本次事实层与增量边界已验证；大型语义使用本机 Pi 做了有界提炼验收。

## 第二工作区可重复验收

用 `benchmarks/verify_shop_transfer.py` 对 `D:\shop-transfer-mini-program` 做事实层对账、父子会话完整性、事件 ID/指纹去重、Git 快照一致性和可选语义层验收。脚本只读源数据库和源仓库；`--measure-incremental` 只更新 sessionmap 本地输出库，不写源工作区。

```powershell
python benchmarks/verify_shop_transfer.py --measure-incremental `
  --out benchmarks/shop-transfer-acceptance-measured.json

python benchmarks/verify_shop_transfer.py --require-semantic `
  --measure-incremental --view output/shop-project-view.json `
  --out benchmarks/shop-transfer-acceptance-semantic-measured-final.json
```

事实验收本轮 19/19 通过，增量同步 **2.696 秒**，新增事件数为 0。使用本机 Pi 对 20,000 字符有界时间线做语义提炼后，`--require-semantic` 验收为 **26/26 通过**：12 个决策、16 条证据、输入哈希和模型版本均已持久化。语义输出保存在 `output/shop-project-view.json`，原始模型结果保存在 `output/shop-semantic-pi-full.json`。

完整 46,574 字符时间线在 180 秒窗口内仍未完成，记录在 `benchmarks/shop-transfer-semantic-pi-full-timeout.json`；当前产品因此保留“有界语义 + 增量重提炼”路径，不把完整输入的超时伪装成成功。

跨工具导入的只读实测：Codex 的 `pi-mini` 工作区导入 15 个会话、11,053 个事件；Pi 的 `pi-mini` 工作区导入 13 个会话、3,670 个事件；DSH 的 `pi-mini` 工作区导入 28 个会话、22,109 个事件；OpenCode 的本地 `C:/Users/Luke` 工作区导入 115 个会话、37,229 个事件。各适配器第二次刷新均不增长事件总数。
