#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sessionmap extract - 用本机已安装的 agent CLI 把时间线提炼成决策地图。

不接 API key, 不新建账号 —— 直接复用用户已有的订阅。

用法:
    python extract.py timeline.md
    python extract.py timeline.md --cli codex
    python extract.py timeline.md --cli pi --out decisions.json
    python extract.py timeline.md --dry-run > prompt.txt     # 只看 prompt, 不调模型
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 非交互调用语法, 各家不一样
# pi 加 --no-tools: 否则它是个完整 agent, 会自己去读项目文件而跑偏
CLIS = {
    "pi":       ["pi", "--no-tools", "-p"],
    "opencode": ["opencode", "run"],
    "codex":    ["codex", "exec", "--skip-git-repo-check"],
    "claude":   ["claude", "-p"],
}
AUTO_ORDER = ["pi", "opencode", "codex", "claude"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

PROMPT = """你是一个项目考古分析师。下面是一个软件项目的完整会话时间线，由规则从会话数据库自动提取。

任务：把这条时间线压缩成一张「决策地图」，让人在 30 秒内想起这个项目的来龙去脉。

严格要求：
0. 你只做文本分析。禁止使用任何工具，禁止读取任何文件，禁止执行任何命令。
1. 只使用时间线里出现的信息。禁止推测、禁止补充常识、禁止编造不存在的事实。
2. 每个决策必须有原文依据，引用用户原话里的关键词。
3. 决策数量上限 12 条。宁可少，不可凑。
   但时间线里每一个出现过的日期，至少要有一条决策对应。孤立在某一天的事不能略过。
4. 状态判定规则，逐条严格执行：
   a. abandoned 只有在用户明确说出「别做了 / 放弃 / 不搞 / 停掉 / 算了」这类否决词时才允许使用。
   b. 时间线里「待办」部分标记为 in_progress 或 pending 的事项，对应决策一律判 open，
      即使后来又出现别的讨论，也不许判 abandoned。
   c. 同一件事用户先说停、后来又说继续的，以最后一次表态为准，并在 why 里写出这个反转。
   d. 没有任何明确表态的，判 open，不要猜。
5. 特别关注三件事：反复出现的判断、被放弃的替代方案、上下文压缩之后新增的、未收口的工作。

输出严格的 JSON，不要 markdown 代码块，不要任何解释文字：
{
  "headline": "一句话项目现状，不超过 40 字",
  "decisions": [
    {
      "id": "D1",
      "when": "2026-11-03 04:37",
      "track": "主题线，2-5 个字",
      "stage": "项目阶段，2-8 个字；无法确认就填 null",
      "title": "不超过 10 字",
      "what": "决定了什么",
      "why": "为什么，引用原话",
      "alternative": "放弃了什么，没有就填 null",
      "status": "done|open|abandoned",
      "evidence": "时间线里的一小段原文"
    }
  ],
  "recurring": ["贯穿全程的判断，最多 3 条"],
  "unfinished": ["未完成的具体事项，最多 8 条"],
  "lost_after_compaction": "上下文断裂之后新增的未收口工作，没有就填 null"
}

track 字段是画图用的，规则极严：
  * 它代表项目里一条持续的主题线，不是单次任务。例如「DSH 对标」「opencode 适配」「品牌化」。
  * 同一个主题线必须用完全相同的字面，一个字都不能变。这是分组依据。
  * 一共只能有 3-6 条线。主线（贯穿项目身份的那条）用「主干」命名。
  * 如果一条决策只是主干的延续，就写「主干」，不要另开新线。

时间线如下：
---
{timeline}
---
"""


INCREMENTAL_PROMPT = """你是一个项目考古分析师，正在做一次增量语义修订。

只分析下面这次刷新里发生变化的事实和受影响的旧决策。不要重写未受影响的决策。
禁止使用工具、读取文件、执行命令、补充常识或编造事实。所有新判断必须能在 facts 中找到依据。

输出严格 JSON，不要 markdown：
{
  "decisions": [
    {
      "id": "已有决策 ID，或新的 D<数字>",
      "when": "YYYY-MM-DD HH:MM",
      "track": "已有主题线优先",
      "stage": "已有阶段优先，无法确认就填 null",
      "title": "不超过 10 字",
      "what": "决定了什么",
      "why": "为什么，引用 facts 中的原话",
      "alternative": "放弃了什么，没有就填 null",
      "status": "done|open|abandoned",
      "evidence": "facts 中的一小段原文"
    }
  ],
  "removed_ids": [],
  "headline": null,
  "recurring": null,
  "unfinished": null,
  "lost_after_compaction": null
}

状态规则：没有明确否决词就不能写 abandoned；待办为 pending/in_progress 就写 open；先停后续又继续时按最后表态并说明反转。若 facts 不足以确认，保留旧决策并不要猜。

旧决策与主题目录：
{previous}

本次变化范围与事实：
{plan}
"""


def build_incremental_prompt(previous: dict, plan: dict) -> str:
    """Create a bounded prompt containing only the previous context and facts."""
    previous_context = {
        "decisions": previous.get("decisions", []),
        "headline": previous.get("headline"),
        "recurring": previous.get("recurring", []),
        "unfinished": previous.get("unfinished", []),
        "lost_after_compaction": previous.get("lost_after_compaction"),
    }
    return INCREMENTAL_PROMPT.replace(
        "{previous}", json.dumps(previous_context, ensure_ascii=False, indent=2)
    ).replace("{plan}", json.dumps(plan, ensure_ascii=False, indent=2))


def merge_incremental_result(previous: dict, patch: dict, plan: dict) -> dict:
    """Merge a model patch while preserving all untouched semantic nodes."""
    if not isinstance(patch, dict) or not isinstance(patch.get("decisions"), list):
        raise ValueError("增量语义结果缺少 decisions 数组")
    merged = json.loads(json.dumps(previous, ensure_ascii=False))
    decisions = {}
    for item in merged.get("decisions", []):
        if isinstance(item, dict) and item.get("id"):
            decisions[str(item["id"])] = item
    for item in patch["decisions"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("增量语义决策缺少 id")
        decisions[str(item["id"])] = item
    for external_id in patch.get("removed_ids") or []:
        decisions.pop(str(external_id), None)
    merged["decisions"] = sorted(
        decisions.values(), key=lambda item: (str(item.get("when") or ""), str(item.get("id") or ""))
    )
    for key in ("headline", "recurring", "unfinished", "lost_after_compaction"):
        if key in patch and patch[key] is not None:
            merged[key] = patch[key]
    scope = plan.get("scope") or {}
    merged["incremental"] = {
        "mode": "changed",
        "model_called": True,
        "previous_run_id": plan.get("previous_run_id"),
        "affected_decision_ids": sorted(set(scope.get("decision_ids") or [])),
        "affected_event_ids": sorted(set(scope.get("event_ids") or [])),
    }
    return merged


def run_incremental(previous_path: str, plan_path: str, cli: str | None,
                    timeout: int, no_proxy: bool = False) -> tuple[dict, dict]:
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    mode = plan.get("mode")
    if mode == "matched":
        result = json.loads(json.dumps(previous, ensure_ascii=False))
        result["incremental"] = {"mode": "matched", "model_called": False,
                                  "previous_run_id": plan.get("previous_run_id"),
                                  "affected_decision_ids": [], "affected_event_ids": []}
        return result, result["incremental"]
    if mode != "changed":
        raise ValueError(f"增量语义包状态为 {mode!r}，请先做一次完整提炼")
    prompt = build_incremental_prompt(previous, plan)
    name = detect_cli(cli)
    print("[info] 增量语义 CLI: %s (%d 字符输入)" % (name, len(prompt)), file=sys.stderr)
    out, _err = run_cli(name, prompt, timeout, no_proxy=no_proxy)
    patch = parse_json(out)
    if patch is None:
        raise ValueError("无法解析增量语义 JSON")
    result = merge_incremental_result(previous, patch, plan)
    return result, result["incremental"]


def detect_cli(explicit):
    if explicit:
        if explicit not in CLIS:
            sys.exit("不支持的 CLI: %s (可选: %s)" % (explicit, ", ".join(CLIS)))
        return explicit
    for name in AUTO_ORDER:
        if which(name):
            return name
    sys.exit("本机没有可用的 agent CLI: %s" % ", ".join(AUTO_ORDER))


def which(cmd):
    return shutil.which(cmd)


def run_cli(name, prompt, timeout, no_proxy: bool = False):
    exe = shutil.which(CLIS[name][0])
    if not exe:
        sys.exit("找不到可执行文件: %s" % CLIS[name][0])
    argv = [exe] + CLIS[name][1:]
    # prompt 一律走 stdin: Windows 的 .cmd 垫片会在换行处截断命令行参数
    workdir = tempfile.mkdtemp(prefix="sessionmap-")
    env = None
    if no_proxy:
        env = os.environ.copy()
        for key in ("all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            env.pop(key, None)
    try:
        p = subprocess.run(argv, input=prompt.encode("utf-8"),
                           capture_output=True, timeout=timeout, cwd=workdir, env=env)
    except FileNotFoundError:
        sys.exit("找不到可执行文件: %s" % exe)
    except subprocess.TimeoutExpired:
        sys.exit("%s 超时 (%d 秒)" % (name, timeout))

    out = ANSI.sub("", p.stdout.decode("utf-8", "replace")).strip()
    err = ANSI.sub("", p.stderr.decode("utf-8", "replace")).strip()
    if p.returncode != 0 and not out:
        sys.exit("%s 退出码 %d\n%s" % (name, p.returncode, err[-1500:]))
    return out, err


def parse_json(text):
    """模型常带 markdown 围栏、前后废话、甚至多段输出。
    这里扫描第一个括号平衡的 JSON 对象, 而不是简单地首尾切片。"""
    t = ANSI.sub("", text).strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    start = t.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        start = t.find("{", start + 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline", help="scan.py 生成的 timeline.md")
    ap.add_argument("--cli", default=None, help="claude / codex / pi / opencode")
    ap.add_argument("--out", default=None, help="输出 json 路径, 默认与 timeline 同名 .json")
    ap.add_argument("--max-chars", type=int, default=20000)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--no-proxy", action="store_true",
                    help="只对本次本机 CLI 调用移除代理环境变量，不修改系统配置")
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt")
    ap.add_argument("--incremental-from", default=None,
                    help="上一轮完整 timeline.json；需配合 --incremental-plan")
    ap.add_argument("--incremental-plan", default=None,
                    help="semantic.py --plan 生成的增量语义包")
    a = ap.parse_args()

    if bool(a.incremental_from) != bool(a.incremental_plan):
        ap.error("--incremental-from 与 --incremental-plan 必须同时提供")
    if a.incremental_from and a.dry_run:
        ap.error("增量语义不支持 --dry-run，请直接查看 semantic-incremental-plan.json")

    if a.incremental_from:
        try:
            data, meta = run_incremental(a.incremental_from, a.incremental_plan, a.cli, a.timeout, a.no_proxy)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(str(exc))
        dest = a.out or a.incremental_from
        Path(dest).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[ok] 增量语义 %s -> %d 个决策 -> %s" %
              (meta.get("mode"), len(data.get("decisions", [])), dest), file=sys.stderr)
        return

    with open(a.timeline, encoding="utf-8") as f:
        timeline = f.read()
    if len(timeline) > a.max_chars:
        print("[warn] 时间线 %d 字符, 截断到 %d" % (len(timeline), a.max_chars), file=sys.stderr)
        timeline = timeline[:a.max_chars]

    prompt = PROMPT.replace("{timeline}", timeline)

    if a.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")
        print(prompt)
        return

    name = detect_cli(a.cli)
    print("[info] 使用 CLI: %s (%d 字符输入)" % (name, len(prompt)), file=sys.stderr)

    out, err = run_cli(name, prompt, a.timeout, no_proxy=a.no_proxy)
    data = parse_json(out)
    if data is None:
        sys.stderr.write("[error] 无法解析 JSON, 原始输出:\n")
        sys.stderr.write(out[:2000] + "\n")
        sys.exit(1)

    dest = a.out or (os.path.splitext(a.timeline)[0] + ".json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[ok] %d 个决策 -> %s" % (len(data.get("decisions", [])), dest), file=sys.stderr)


if __name__ == "__main__":
    main()
