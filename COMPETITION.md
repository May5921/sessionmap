# 竞品核实与产品边界

核实时间：2026-11-07。这里区分了官方仓库/官方文档和搜索线索；没有把未找到一手资料的名称当成已确认事实。

## 已确认的相邻产品

| 产品 | 已确认能力 | 它解决的问题 | 与 sessionmap 的关系 |
|---|---|---|---|
| [Coding Agent Visualizer](https://github.com/everettjf/coding-agent-visualizer) | Claude Code、Codex、Gemini、Qwen、OpenCode、Cursor、Cline；执行图、span waterfall、token flame、文件热力图、统计、搜索、会话对比、实时 tail、导出 | 看清一次或多次 Agent 运行到底做了什么 | 最接近的相邻产品；我们不再竞争运行观测、成本和热力图 |
| [Agent Flow](https://github.com/patoles/agent-flow) | Claude Code/Codex 实时事件流、分支图、工具调用、时间线、转录、文件关注热力图、回放、VS Code 扩展 | 调试正在运行的多 Agent 编排 | 它看“现在这次运行怎么走”，我们看“这个项目长期怎么走到现在” |
| [Atlaslink](https://github.com/fworks-tech/atlaslink) | 可视化编排、实时 DAG、事件流、任务 API、HITL、事件溯源和协作房间 | 创建、编排、控制多 Agent 工作流 | 它是运行时和编排器；我们是被动读取项目历史的记忆层 |
| [AgentLens](https://developer.salesforce.com/blogs/2026/05/agentlens-debug-agentforce-with-interactive-visualizations) | Agentforce trace 的 DAG、FSM、逐步检查器和变量差异 | 调试一个 Agentforce 执行轨迹 | 框架专用的运行调试器，不是跨工具项目记忆 |

## 尚未独立核实的名称

`Trace Lens`、`Agent Observability Kit`、`Agent Observability Dashboard (Claude Code)` 这些名称在本轮搜索中没有找到足够明确的一手仓库或官方文档。搜索结果显示存在同类方向，例如 `agent-trace-lens`、Claude Code 可视化插件和 AutoGen 观测工具，但它们仍应按具体仓库逐个核实后再纳入竞品结论。

## 结论

“多 Agent 追踪、执行图、成本图、文件热力图”已经是拥挤赛道。Coding Agent Visualizer 和 Agent Flow 已经把这个方向做成了可用产品，继续堆更多图表不会形成优势。

sessionmap 的产品边界应明确为：

> **把多个编程工具、多个会话和 Git 事实，压成一条可恢复的项目记忆线。**

核心差异是跨运行、跨会话和跨工具的长期语义：

- 项目目标如何改变；
- 哪些决策真正改变了方向；
- 哪些方案被放弃，后来是否恢复；
- Git 实际落地和会话意图是否矛盾；
- 上下文压缩后有哪些工作悬空；
- 用户回来时下一步应该继续什么。

执行图、成本、文件热力图可以作为证据下钻或适配器能力，不能成为主产品叙事。

## 二次调研：只吸收能改变理解质量的能力

本轮补查了官方仓库、官方文档和成熟的项目记忆模型。结论不是“把面板搬过来”，而是把它们拆成输入、转换和输出三层：

| 来源 | 核实到的工程能力 | sessionmap 的最小吸收方式 |
|---|---|---|
| [Coding Agent Visualizer](https://github.com/everettjf/coding-agent-visualizer#readme) | 多源 adapter、统一会话节点、实时 tail、证据查看、导出 | 保留 adapter 契约和本地读取；运行图、成本火焰图、文件热力图留在相邻产品边界 |
| [Agent Flow](https://github.com/patoles/agent-flow#readme) | Claude/Codex 事件监听、回放、跨会话、分支和 transcript | 只吸收事件接入和回放语义；不做运行时编排画布 |
| [Atlaslink](https://github.com/fworks-tech/atlaslink#readme) | append-only 事件、稳定 ID、断点恢复、事件溯源 | canonical event + fingerprint + cursor；不引入实时协作、权限和任务编排 |
| [Langfuse 数据模型](https://langfuse.com/docs/observability/data-model) | observation / trace / session 分层、原始事件与投影分离、会话聚合 | 事实表、语义表、证据链接分层；展示只读投影 |
| [OpenTelemetry Traces](https://opentelemetry.io/docs/concepts/signals/traces/) | parent、event、link、status 的因果模型 | 用 parent_event_id 和 evidence link 表达跨会话继承、推翻和支撑关系 |
| [AgentLens](https://developer.salesforce.com/blogs/2026/05/agentlens-debug-agentforce-with-interactive-visualizations) | DAG → 子代理状态 → 步骤检查器的多层下钻 | 事件线 → 证据抽屉两级下钻；不复制框架专用 DAG/FSM |
| [Mem0 memory model](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx) | 从原文中提取 salient memory，并按 scope 管理 | 语义层保存“决定/状态/未收口事项”，不把 transcript 当记忆本身 |
| [Graphiti temporal graph](https://help.getzep.com/v2/understanding-the-graph) | episode、entity、edge 的时间有效性与覆盖关系 | 先用 SQLite 表表达 valid time 和 supersession；暂不引入图数据库 |
| [Linear Timeline / Project Graph](https://linear.app/docs/timeline) | 项目级时间线、里程碑、依赖和 scope 漂移 | 只保留项目转折、记忆断裂、当前枝头；不做完整 PM/Gantt |

由此确定三条工程准则：

1. **输入层**先保留不可变事实和稳定 ID，支持多工具接入并能重跑去重。
2. **转换层**把 canonical events 提炼成带证据的 decisions、notes 和状态覆盖；语义表是展示的正式来源。
3. **输出层**首屏只回答“现在在哪、为什么、哪里断了、下一步是什么”，复杂观测指标全部退到证据层。

当前实现已新增 `canonical_timeline.py`：它按 `workspace_id` 选择工作区，从 canonical event store 和最新 semantic run 生成工具无关的项目时间线。这样切换到 Claude/Codex 适配器时，展示层不需要再分叉一套逻辑。
