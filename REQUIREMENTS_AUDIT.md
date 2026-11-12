# sessionmap 完成审计

更新时间：2026-11-10

## 已验证

- 六种适配器（ZCode、Claude、Codex、Pi、DSH、OpenCode）本机探测均为 `ready`。
- Claude 原始事件已映射到统一事件类型，并保留 `raw_kind`；未知类型降级为 `source_event`。
- 事件 upsert 会更新内容指纹；部分导入会返回 `partial`、跳过会话和错误。
- 两个真实工作区事实层对账通过。`pi-mini` 有 11 个语义决策和 20 条证据；`shop-transfer-mini-program` 有 82 个会话、19,043 个事件。
- 语义层保存输入哈希、模型、提示版本、运行时间、置信度、证据链接、节点 manifest 和人工修正。
- `semantic.py --plan`、`extract.py --incremental-from/--incremental-plan` 和 `pipeline.py --incremental` 已接通；`matched` 不调用模型，`changed` 只发送受影响事实和旧决策上下文。
- `watch.py` 只刷新本地事实和事件线；新增事实会显示为“待提炼”，不会冒充模型决策。
- 事件线支持自上次查看、工作线/来源/工具筛选、搜索、阶段折叠（语义节点提供阶段时）、证据抽屉、Git 事实和静态分享页。
- 新增 `progress.py` 确定性项目推进骨架：工作项（主会话 + 子会话，按文件重叠/提交链接/会话广度/来源广度/时间跨度合成信心度）、里程碑（git 发布提交）、阶段划分、未收口 TODO；只读事实、不调用模型、可复现。
- 事件线改为以工作项与里程碑为骨架，语义决策降为注解，小事件默认隐藏；移除首次查看的 NEW 徽标与来源/工具筛选，首屏更收敛。
- 44/44 单元测试、`pi-mini` 端到端 pipeline 通过（事件 5,089，11 个决策，3 个里程碑，10 个提交）。
- 30/30 单元测试、33/33 `pi-mini` 验收、一致性检查 0 warnings、Python/JavaScript 语法检查通过。
- 大工作区有界语义验收通过：20,000 字符输入、12 个决策、16 条证据；`--require-semantic` 实测 26/26 通过，增量同步 2.577 秒且无事件增长。

## 当前边界

- 大工作区完整 46,574 字符时间线在 180 秒窗口内未完成；当前采用有界语义和增量重提炼路径，记录在 `benchmarks/shop-transfer-semantic-pi-full-timeout.json`。
- 语义决策仍是“整条时间线压成 ≤12 条”的批量提炼，粒度偏粗、覆盖会挤占真里程碑；下一步应改成按工作项粒度、增量、每项一条 why。
- DSH/Codex 压缩流的逻辑游标支持增量，但物理读取仍需从压缩流开头扫描。
- 证据抽屉显示原始引用、会话和事实字段；`part:` 等本地源引用目前是可复制的文本标识，不是操作系统深链。
- 团队协作、云同步、用户自定义事件类型和通知服务属于后续范围，当前保持本地只读产品边界。

## 2026-11-12 · 砍到单页（分支 cut/mvp-reentry）

判断依据：产品价值 80% 来自「确定性骨架 + 证据锚点」，语义注解是锦上添花；而复杂度预算几乎全花在锦上添花那一侧。

### 已移除

- 6 个渲染器 → 1 个：`dashboard.py`、`eventline_dashboard.py`、`minimal_dashboard.py`、`story_dashboard.py`、`report.py`、`render.py` 全部移除，统一由 `timeline_view.py` 渲染。
- 分享层：`landing.py`、`landing.html`、`privacy.py`（本地优先产品不需要分享，云端又和只读边界冲突）。
- 二级证据页 `evidence.html`：证据改为时间线内联展开。
- 首屏控件：搜索、来源/工具筛选、阶段折叠、自上次查看的 delta 摘要、补充事件开关、未收口折叠，全部移除；只留时间范围。
- CLI 增量与分片：`pipeline.py` 的 `--incremental` / `--chunked` / `--chunk-chars` 移除。`extract.py` / `semantic.py` 内部实现保留，等真的有大项目跑不动时再接回。

### 已修正

- 工作项证据里原本没有一句用户原话（只有 session_start 和文件改动），导致"为什么开/为什么停"是空壳。`progress.py` 现在取该会话范围内首尾两条用户消息。
- `view.py` 的 `evidence_for` 兜底逻辑因为 `progress.py` 总是给 evidence_refs 而从未执行，这是上面那个空壳的根因，已由上一项绕过。
- 弱取代关系收紧：只有同一批文件重叠 ≥3 个且占较小一方 ≥30% 才标记"已被后续工作覆盖"，避免一条共享的 `app.js` 就把整条时间线划掉。

### 验收

- 45/45 单元测试通过。
- `pipeline.py pi-mini --no-merge` 端到端通过：5,092 事件、11 条结论、4 个工作项。
- `verify.py --page timeline.html` 33/33 通过，新增三项页面检查：单表面、反转标记、已退役控件不再出现。
- 页面在 900px 和 390px 下无横向溢出。

### 仍未处理

- adapter 6 → 2：不替主人猜哪两个是日常真实数据源，暂不删。
- 结论仍是"整条时间线压成 ≤12 条"的批量提炼，粒度偏粗；下一步应改为按工作项每条一个 why。
- 工作项证据没有逐文件 diff，只有文件名。
