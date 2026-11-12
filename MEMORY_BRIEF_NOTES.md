# Memory brief：实现决定记录

本文件记录 `sm brief` 第一步实现中，规格没有写死、由实现者自行决定的事项。

## 数据来源与 store 选择

- `sm brief <project>` 默认在仓库目录和当前目录扫描 `sessionmap-*.db` / `sessionmap.db`，选择**匹配项目的事件最多**的一个 store；`--store` 可显式覆盖。
- 对 `fangzi`，本机生成了 `sessionmap-fangzi.db`：先分别用现有 `sync.py` / `adapter_sync.py` 导入 ZCode、Codex、Pi、DSH、OpenCode，再用 `workspace_merge.py` 合并。未修改任何 adapter。
- 没有把所有来源直接写进同一个 store，因为 canonical `event` 表的 `UNIQUE(source_kind, source_id)` 会让同一仓库的 Git commit 在第二个来源导入时冲突；合并层已有跨来源 Git 去重。
- 生成库 `sessionmap-fangzi-*.db` 属于本机数据，已被 `.gitignore` 的 `*.db` 排除；中间源库已移到 `output/fangzi-sources/`，仓库根只保留合并库 `sessionmap-fangzi.db`。

## 摘要口径

- 不调用模型，纯确定性抽取：用户原话、Git commit、TODO 状态、semantic decision/evidence link。
- 时间窗口相对 store 中最新事件，而不是主机时钟：用户消息 3 天（不足则放宽到 14/90 天），未收口 TODO 30 天，已完成 TODO 14 天，error 14 天。
- 超过 30 天的 pending TODO 视为过期，不进入「卡住的问题」；这是为了避免把 10-12 的旧清单当成当前阻塞。
- 用户消息会过滤内部提示词、AGENTS.md 注入、命令回显、图片标签和包含密码/token 的文本；代价是正常讨论里出现「密码」等词的句子也会被跳过。
- 600 字上限按输出总字符数计算（含来源引用，不是只算汉字）。超限时先丢掉低优先级条目，再压缩每条文字；三个 section 至少保留一条有来源的结论。
- 引用格式：普通事件用完整 `event_id`，Git commit 用完整 sha（canonical store 的 `repository_commit` / git event 的 `source_id`）。完整引用同时写入 `memory.evidence_refs`。

## memory 表语义

- `status` 取 `active` / `superseded`；`superseded_by` 指向新记录 id。
- `claim_key` 由 `project + section + 稳定主体` 生成。像「最近用户要求」「已落地 commits」这种滚动结论用固定主体（`asks:recent`、`landed:recent`），所以新结论会覆盖旧结论；TODO 用内容作为主体，所以同一条 TODO 的状态变化会 supersede。
- 同一 `claim_key` 且 `content` 未变时是幂等复用，只刷新 `evidence_refs`，不新增行。
- FTS5 使用 `unicode61` 分词。中文没有分词器，连续中文会被当成一个 token；搜中文短语需要命中完整 token，英文/数字正常。没有为了中文引入任何依赖。

## CLI 入口

- 仓库内只保留 `sm.py`（`python sm.py brief fangzi`）；仓库根的 `sm.cmd` 是多余残留，已移入回收站，不提交。
- 为了让 `sm brief fangzi` 能直接运行，本机额外创建了 `C:\Users\Luke\bin\sm` 与 `sm.cmd` 两个 shim，指向 `D:\idea\sessionmap\sm.py`；`C:\Users\Luke\bin` 已在 PATH 中。该目录原本没有同名命令，未覆盖任何文件。
- 如果仓库移动位置，这两个 shim 需要同步改路径。
