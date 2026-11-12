# sessionmap

> 这个项目曾经忘记自己做过什么，然后继续往下改。

sessionmap 把一个编程工作区的历史，压成一条能读的时间线。数据只来自本机的会话记录和 Git，它回答三个问题：**现在在哪、为什么停、下一步是什么**，然后让你一路往下读到项目是怎么一步步改出来的。

它不是运行观测面板（执行图、成本火焰图、文件热力图已有相邻产品在做），也不是架构图或依赖图。它只做一件事：**跨会话的项目记忆**。

## 一个页面

```powershell
python pipeline.py pi-mini
```

产出 `timeline.html`，单文件、无外部依赖，双击就能开。

页面结构是固定的三层，不再有第二层：

1. **当前枝头** —— 正在推进的那个工作项、它停在哪句原话、还没收口的下一步。这是回来时第一眼要看的东西。
2. **时间线** —— 按日期往下读。工作项和里程碑是骨架，结论挂在它所属的工作项下面。
3. **展开** —— 每一条都能内联展开：从哪句开始、改了哪些文件、停在哪、原话、来源会话。没有二级页。

唯一的控件是时间范围（全部 / 30 天 / 7 天）。没有搜索、没有来源筛选、没有阶段折叠 —— 那些是审计工具的形状，不是每天会打开的形状。

被数据证明已经放弃的结论会置灰并划掉；工作项只有在后续工作项改了**三成以上同一批文件**时才会标记"已被后续工作覆盖"，不做语义猜测。

## 数据从哪来

```text
source adapter → canonical events → 工作项/里程碑骨架 → 语义结论注解 → timeline.html
```

- `sync.py` / `adapters.py`：只读本机会话库（ZCode、Claude、Codex、Pi、DSH、OpenCode），归一化成 canonical event。
- `progress.py`：确定性骨架。工作项来自主会话（含子会话），里程碑来自 git 发布提交，状态来自 TODO 状态机。**不调用模型**，快、可复现。
- `view.py` / `semantic.py`：证据匹配和语义结论的持久化。结论永远带证据链接。
- `timeline_view.py`：唯一的渲染器，生成一个自包含页面。

`timeline.json` 是语义层的缓存。刷新事实不会重新调用模型：

```powershell
python pipeline.py pi-mini              # 复用已有语义，只刷新事实和页面
python pipeline.py pi-mini --extract --cli pi   # 显式重新提炼语义
```

## 关于隐私，说清楚

默认路径**不联网**：`pipeline.py` 不带 `--extract` 时只读本机文件和本机 SQLite，不调用任何模型。

`--extract` 会把会话原文交给本机 CLI。如果该 CLI 背后走云端 API，**内容就已经离开这台机器**，这一点不因为是"本机 CLI"而改变。运行时它会明确打印这条警告。

因此当前推荐两种用法：

- **不提炼**：只用确定性骨架 + 用户自己用一句 `override.py` 写下为什么。最诚实，零 token，零外发。
- **本机模型**：接本地小模型抽 `why`，质量会掉，但骨架不依赖它，注解错了不影响阅读。

## 命令

```powershell
python sm.py brief fangzi                        # 项目当前状态摘要（600 字内，带来源引用）
sm brief fangzi                                  # 已安装到 PATH 的同一命令
python pipeline.py <workspace>                    # 事实 + 视图 + 页面
python pipeline.py <workspace> --extract --cli pi # 额外重新提炼语义（会外发原文）
python watch.py <workspace> --interval 5          # 持续刷新事实和页面，不调模型
python adapter_pipeline.py pi --workspace pi-mini --store sessionmap-pi.db --out pi-timeline.html
python override.py sessionmap.db D11 --status done --why "已由 Git 和用户原话确认"
python search.py sessionmap.db "切换工作区"
python compare.py before-view.json project-view.json
python consistency.py project-view.json
```

`sm brief` 不调用模型：它从 canonical events 和已有 semantic 结论里抽取「在做什么 / 已确定决策 / 卡住的问题」，每条结论都带 event id 或 session id。结论写入 `memory` 表；同一 `claim_key` 的新结论会把旧记录标成 `superseded_by`，`memory.content` 另有 FTS5 索引。

多来源合并：同一个仓库根目录下有多个 canonical store 时，`pipeline.py` 会自动合并并保留来源；`--no-merge` 关闭。

其他工具可以走同一条事实流水线，渲染器不变：

```powershell
python adapter_pipeline.py claude --workspace pi-mini --store sessionmap-claude.db --out claude-timeline.html
```

## 边界

- 只读。不改任何源会话库、不写 Git 仓库、不需要用户改工作流。
- 工作项证据只有会话开始、文件改动和用户原话；**没有逐文件 diff**。说"改了什么文件"可以，说"怎么改的"要看 Git。
- 单机单人场景。团队协作、云同步、多人权限不在范围内。
- `extract.py` / `semantic.py` 内部仍保留增量与分片实现，但已经从 CLI 移除 —— 等到真的有大项目跑不动时再接回来。

验证方法见 [TESTING.md](TESTING.md)，相邻产品与产品边界见 [COMPETITION.md](COMPETITION.md)。
