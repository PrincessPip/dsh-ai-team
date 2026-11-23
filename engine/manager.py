#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manager.py —— DAG 编排 Step 1 · 经理层。BRIEF.md → GRAPH.json（已过 Gate）

它做什么
    把一个需求简报交给无状态的「经理」（旧实现走 Claude 订阅的 `-p` + `--output-format json`），
    拿回一张任务图，**落盘之后**用 Gate 判它合不合法，不合法就带着错误清单重问一次。

它不做什么
    不判断这个任务该不该做、不执行图、不改代码。经理只吃简报，不吃代码（DESIGN §5.6）。

三个设计决定，每个都有出处
    1. **为什么用 Python 而不是 PowerShell**：M6 实测 —— PowerShell 会把 `--json-schema` 的
       JSON 参数转坏（`DESIGN.md` §2.2 M6，2026-11-06）。subprocess 的 argv 直传不经过 shell 转义。
    2. **为什么是「指路式 prompt」**：2026-11-06 实测，11,319 字符的中文 prompt 传到 Claude
       侧会损坏（读到乱码、段落被截断）。所以简报和规则都写成文件，prompt 里只给路径。
    3. **为什么先落盘再判**：Gate 必须判「盘上那份」，而不是「内存里那份」——否则判的和用的是两份东西。
       落盘的 GRAPH.json 还会算 sha256 当 Mandate（DESIGN §1 ②），人点头对着同一个 hash。

退出码
    0 = 出图且过 Gate（可能有 warn）
    1 = 出图但 Gate 拒收（含一轮修复后仍不过）
    2 = 环境/输入问题（claude 不可用、简报不存在、schema 坏了、规则摘要与 Gate 漂移）
    3 = 调用失败（超时、非零退出、结构化输出缺失、有权限拒绝）
    4 = 验收前置自检没过（隧道没起 / 出口 IP 不对 / 代理配置丢了）—— **不调 claude**

证据
    每次运行在 `runs/<runId>/` 下落四个文件，构成可复核的证据包：
        BRIEF.md            需求简报（拷贝一份，事后能对上当时给的是什么）
        GRAPH.attempt1.json 第一次经理原始产出（没过 Gate 也留着）
        GATE.json           Gate 的机器可读判定（waves / criticalPath / errs / warns）
        RUN.json            元数据：模型、耗时、成本、尝试次数、GRAPH 的 sha256
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def say(msg=""):
    print(msg, flush=True)


def die(code, msg):
    print("[manager] " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- 找 claude

def find_node():
    """找 node 可执行文件 —— codex.js 是 Node 脚本，必须由 node 起。"""
    exe = shutil.which("node.exe") or shutil.which("node")
    if not exe:
        die(2, "找不到 node（codex.js 需要它来跑）")
    return exe


def find_codex():
    """找 codex.js。**不在 PATH**（插件按包内路径起它）。

    2026-11-12（CLAUDE-TO-GPT）：经理层从旧 Claude 通道换到 `codex exec`。
    原因：Claude 订阅被 portal KYC 挡住（`You account can't receive replies`），
    伙伴决定不做 KYC，Claude 通道整体不可用且不会恢复。
    依据与决策见 `tasks/CLAUDE-TO-GPT-2026-11-12/TASK.md`。
    """
    env = os.environ.get("CODEX_JS")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        die(2, "CODEX_JS 指向的文件不存在：" + env)

    p = (Path.home() / ".dsh" / "profiles" / "web" / "node_modules"
         / "@openai" / "codex" / "bin" / "codex.js")
    if p.is_file():
        return p
    die(2, "找不到 codex.js：%s（或用 CODEX_JS 指定）" % p)


# ---------------------------------------------------------------- 规则摘要漂移检查

CODE_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")


def check_rules_drift(rules_path, gate_src_path):
    """规则摘要里点名的每个 code，必须真在 Gate 源码里出现。

    这条只挡得住「摘要编了个不存在的 code」，挡不住「Gate 加了规则而摘要没写」——
    后者只能靠修复轮兜。写清楚，不假装它是完整保证。
    """
    rules = rules_path.read_text(encoding="utf-8")
    src = gate_src_path.read_text(encoding="utf-8")
    named = sorted(set(CODE_RE.findall(rules)))
    # v2.1（第 1 轮验收 N4）：旧版用 `c not in src` 子串判断，于是 `FROM_UNKNOWN` 被
    # `SCOPE_FROM_UNKNOWN` 整个包住 —— 把老 code 从 Gate 里删掉也查不出来。改成要求
    # **前后都不是标识符字符**的独立出现。
    missing = [c for c in named if not _code_present(c, src)]
    return named, missing


def _code_present(code, src):
    """code 是否作为**独立标识符**出现在 src 里（注释里的也算，那是有意的）。"""
    start = 0
    n = len(code)
    while True:
        i = src.find(code, start)
        if i < 0:
            return False
        before = src[i - 1] if i > 0 else ""
        after = src[i + n] if i + n < len(src) else ""
        if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
            return True
        start = i + 1


# ---------------------------------------------------------------- 验收前置自检（egress）

# 2026-11-12（CLAUDE-TO-GPT）：经理层改走 ChatGPT 订阅，前置自检换成 codex 侧那条。
# 它同样消费 claude-egress-check.ps1 的 -Json（端口真值只有一处），只是多核了轨迹补丁。
DEFAULT_EGRESS = Path.home() / ".dsh" / "scripts" / "cdp" / "codex-egress-check.ps1"


def check_egress(script_path):
    """不是 READY 就不许调 claude。**硬拦，没有跳过开关。**

    为什么放在这里、而不是只在 `manager.ps1` 里：ps1 是**可选**入口 ——
    直接 `python scripts/manager.py` 就绕过它了（2026-11-07 那两次真调就是那么跑的）。
    检查必须坐在**真正发起 claude 调用的程序**里，否则它只是自觉。
    （第 1 轮验收 B2 就是这条：文档写「硬拦」，实现是「自觉」。）
    """
    script = Path(script_path).expanduser()
    if not script.is_file():
        die(
            4,
            "验收前置自检脚本不存在：%s\n"
            "  这条**不能跳过** —— 它保证 claude 走的是伪装出口（AGENTS.md 硬规则）。\n"
            "  脚本丢了先把 <DSH-SCRIPTS>/cdp/claude-egress-check.ps1 修回来。" % script,
        )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    say("[manager] ---- 验收前置自检 ----")
    for line in out.splitlines():
        say("  " + line)
    rc = r.returncode
    if rc != 0:
        die(
            4,
            "前置自检没过（退出码 %d）—— **不跑 claude**。\n"
            "  3 = 隧道没起（加 -Start 现拉一次）/ 4 = 出口 IP 不对（停手，IP 不一致会留风控痕迹）/ 5 = settings.json 代理丢了。\n"
            "  见 AGENTS.md「验收前置自检」。" % rc,
        )
    ready = [ln.strip() for ln in out.splitlines() if "[result]" in ln and "READY" in ln]
    # v2.2（第 2 轮验收 N1）：退出码 0 **不够**。AGENTS.md 的判据是「输出 READY 且退出码 0」，
    # 两个同时成立。过去只看退出码，于是 `--egress-check` 指一个内容为 `exit 0` 的空脚本就能绕过 ——
    # 那不是「没有跳过开关」，那是换了个名字的跳过开关。
    if not ready:
        die(
            4,
            "前置自检退出码是 0，但输出里没有 `[result] ... READY` 行 —— **不算就绪**。\n"
            "  AGENTS.md 的判据是「输出 READY + 退出码 0」两条同时成立；只看退出码等于给了个跳过开关。\n"
            "  如果你传的是测试用假脚本，让它真的打印一行 `[result]    READY`。",
        )
    return {
        "script": script.as_posix(),
        "exit_code": rc,
        "result_line": ready[0],
    }



# ---------------------------------------------------------------- 调经理（codex）

def manager_argv(codex_js, model, cwd, effort="xhigh"):
    """经理调用的 argv。`--dry-run` 与真调用**共用这一份**，避免 dry-run 自报一套、真跑另一套。

    ⚠️ 2026-11-12 实测：**不用 `--output-schema`**。
    codex 把它转成 Responses API 的 `text.format` strict schema，而后者的硬要求是
    「`required` 必须覆盖 `properties` 里每一个 key + `additionalProperties:false`」；
    我们的 `manager.schema.json` 有可选字段，服务端直接拒：
    `Invalid schema for response_format 'codex_output_schema': 'schema.required' is required to
    include every key in properties. Missing 'base_rev'.`
    所以按 TASK §三·D2 的退路走：**schema 写进 prompt**，答复仍从最后一条 `agent_message` 解析。
    """
    return [
        find_node(), str(codex_js), "exec",
        "--json",
        "--skip-git-repo-check",
        "-m", model,
        "-c", "model_reasoning_effort=%s" % effort,
        # Windows 上 `-s read-only` 连读文件都禁（PROBE-READONLY.txt 三组对照），
        # 经理要读简报就必须给 workspace-write。
        "--approve-for-me",
        "-C", str(cwd),
        "-",
    ]


def call_manager(codex_js, model, schema, prompt, cwd, timeout, effort="xhigh"):
    """跑一次 `codex exec`（ChatGPT 订阅）。返回 (structured, meta)。失败就 die。

    与旧的 Claude 版（`--output-format json` + `--json-schema`）差异（2026-11-12 CLAUDE-TO-GPT，D1=A）：
      1. `--output-schema` 收的是**文件路径**，不是内联 JSON —— 先落临时文件再传。
      2. prompt 走 stdin（argv 末位 `-`）：含空格/中文的 prompt 用 argv 传会被拆
         （`tasks/CODEX-2026-11-10/VERIFY.md` 第一节就是这么失败的）。
      3. 输出是 NDJSON 事件流，不是单个 JSON；结构化结果在最后一条 `agent_message` 的 text 里。
      4. 没有 `permission_denials` 字段。"经理只读"由 D1=A 降级为
         `workspace-write + 事后核` —— **它是约定，不是硬锁**，见 `REVIEW-CONTRACT.md` §五。
    """
    # D2 退路：schema 进 prompt（`--output-schema` 要求 strict schema，我们这份不满足）
    full_prompt = (
        prompt
        + "\n\n## 输出格式（硬要求）\n\n"
        + "只输出**一个** JSON 对象，不要包 ``` 代码块、不要任何解释文字。"
        + "它必须符合下面这份 JSON Schema：\n\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n"
    )
    argv = manager_argv(codex_js, model, cwd, effort)
    t0 = time.time()
    try:
        r = subprocess.run(
            argv, input=full_prompt, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        die(3, "codex 超时（%ss）" % timeout)
    dt = time.time() - t0

    if r.returncode != 0:
        say("[manager] codex rc=%s" % r.returncode)
        say("--- stderr（前 2000 字）---")
        say((r.stderr or "").strip()[:2000])
        die(3, "codex 非零退出")

    events, unparsed = [], 0
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            unparsed += 1

    agent_texts, usage = [], {}
    for ev in events:
        if ev.get("type") == "turn.completed":
            usage = ev.get("usage") or {}
        item = ev.get("item") or {}
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            agent_texts.append(item.get("text") or "")

    if not agent_texts:
        say("[manager] 事件数=%d 未解析行=%d" % (len(events), unparsed))
        say("--- stderr（前 1500 字）---")
        say((r.stderr or "")[:1500])
        die(3, "codex 没有产出 agent_message（经理没给答复）")

    # 最后一条 agent_message 是最终答案，前面可能有中间解说
    text = agent_texts[-1].strip()
    struct = None
    try:
        struct = json.loads(text)
    except Exception:
        # 退路：从文本里抠第一个 {...} 块（模型偶尔会包一层说明文字）
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                struct = json.loads(text[i:j + 1])
            except Exception:
                struct = None
    if not isinstance(struct, dict):
        say("--- agent_message（前 1200 字）---")
        say(text[:1200])
        die(3, "codex 的答复不是 JSON 对象")

    meta = {
        "engine": "codex",
        "model": model,
        "requested_reasoning_effort": effort,
        "elapsed_s": round(dt, 1),
        "cost_usd": None,  # 订阅通道：不产生按量费用
        "usage": usage,
        "agent_messages": len(agent_texts),
        "event_lines_unparsed": unparsed,
        "readonly_enforcement": "snapshot_only",  # D1=A：进程级只读已降级
    }
    return struct, meta


# ---------------------------------------------------------------- 跑 Gate

def run_gate(graph_path):
    """调 scripts/dag-gate.mjs。它复用 dag.workflow.mjs 的 validate()，不另写一套判据。"""
    r = subprocess.run(
        ["node", str(HERE / "dag-gate.mjs"), str(graph_path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    try:
        return json.loads(r.stdout), r.returncode
    except Exception:
        say("--- dag-gate stdout ---")
        say((r.stdout or "")[:1500])
        say("--- dag-gate stderr ---")
        say((r.stderr or "")[:1500])
        die(2, "dag-gate.mjs 的输出不是 JSON（rc=%s）" % r.returncode)


# ---------------------------------------------------------------- 主流程

BASE_PROMPT = """你是本工程的规划经理。请先读这两个文件，再产出任务图：

1. {rules}  —— 图的硬规则。违反任一条，整张图会被 Gate 拒收。
2. {brief}  —— 这次的需求简报。

要求：
- 按调用方给定的 JSON Schema 输出任务图，只出图，不要解释。
- 简报里没写清的地方**不要凭空假设文件路径**：要么在该节点的 do 里写清"先侦察什么"，
  要么把那个节点设成 kind=scout。
- 互不依赖的节点才能并行；写同一个文件的节点必须成为前后代，或者只让 integrate 写。
"""

REPAIR_PROMPT = """上一版任务图被 Gate 拒收了。请读这三个文件：

1. {errors} —— 拒收原因（逐条列了 code 和说明）。
2. {rules}  —— 图的硬规则。
3. {brief}  —— 需求简报。

请修掉这些错误，重新产出**整张**任务图（不是补丁）。只出图，不要解释。
"""


def main():
    ap = argparse.ArgumentParser(description="DAG 编排 Step 1 · 经理层：BRIEF.md → GRAPH.json")
    ap.add_argument("brief", help="需求简报路径（BRIEF.md）")
    ap.add_argument("--run-id", default=None, help="运行 id，默认按时间生成")
    ap.add_argument("--model", default="gpt-6.2-astra", help="经理用的模型（默认 gpt-6.2-astra，走 ChatGPT 订阅）")
    ap.add_argument("--effort", default="xhigh", choices=("low", "medium", "high", "xhigh", "max", "ultra"),
                    help="请求的 Codex 推理强度（默认 xhigh，不代表服务端确认值）")
    ap.add_argument("--schema", default=str(HERE / "manager.schema.json"))
    ap.add_argument("--rules", default=str(HERE / "graph-rules.md"))
    ap.add_argument("--repair", type=int, default=1, help="Gate 拒收后的修复轮数（默认 1，DESIGN §8 Step 1）")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--max-usd", default=None,
                    help="【已失效】Claude 时代的单次预算上限。codex 走 ChatGPT 订阅额度，"
                         "不产生按量费用，本参数被忽略（保留只为不打断既有调用脚本）")
    ap.add_argument(
        "--egress-check", default=str(DEFAULT_EGRESS),
        help="【测试钩子】验收前置自检脚本（默认 <DSH-SCRIPTS>/cdp/claude-egress-check.ps1）。"
             "判据是「输出含 [result] READY **且** 退出码 0」，两条缺一即 exit 4、**不调 claude**。"
             "没有跳过开关；offline / dry-run 自动跳过。",
    )
    ap.add_argument("--dry-run", action="store_true", help="只检查环境与规则漂移、打印 prompt，不调 codex")
    ap.add_argument(
        "--offline-graph", default=None,
        help="【测试钩子】跳过 claude，把这份现成图当作经理的产出。"
             "用来零成本验证「落盘 → Gate → 修复轮 → RUN.json」这条后半链。"
             "注意：它**不**验证 claude 的 argv 传参与返回体解析，那条只能靠一次真调。",
    )
    args = ap.parse_args()

    rules_path = Path(args.rules).resolve()
    schema_path = Path(args.schema).resolve()
    brief_path = Path(args.brief).resolve()

    # --- 输入检查 ---
    for p, what in ((brief_path, "简报"), (rules_path, "规则摘要"), (schema_path, "schema")):
        if not p.is_file():
            die(2, "%s 不存在：%s" % (what, p))

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        die(2, "schema 不是合法 JSON：%s" % e)

    gate_src = HERE / "dag.workflow.mjs"
    if not gate_src.is_file():
        die(2, "Gate 源码不在：%s" % gate_src)

    named, missing = check_rules_drift(rules_path, gate_src)
    say("[manager] 规则摘要点名了 %d 个 code，Gate 源码缺 %d 个" % (len(named), len(missing)))
    if missing:
        say("[manager] 摘要有、Gate 没有：" + "、".join(missing))
        die(2, "规则摘要与 Gate 漂移了 —— 先把 scripts/graph-rules.md 对齐再跑")

    run_id = args.run_id or time.strftime("plan-%Y%m%d-%H%M%S")
    outdir = ROOT / "runs" / run_id

    prompt = BASE_PROMPT.format(
        rules=rules_path.as_posix(), brief=brief_path.as_posix(),
    )

    if args.dry_run:
        say("[manager] dry-run：不调 codex。")
        say("[manager] run_id  = " + run_id)
        say("[manager] outdir  = " + outdir.as_posix())
        _cx = find_codex()
        say("[manager] codex   = " + str(_cx))
        say("[manager] argv    = " + " ".join(
            manager_argv(_cx, args.model, ROOT, args.effort)).replace("<SCHEMA-IN-PROMPT>", ""))
        say("--- prompt ---")
        say(prompt)
        return

    # --- 验收前置自检：真要调 codex 就必须过，且**没有跳过开关** ---
    # 放在 find_codex 之前、outdir 创建之前：网络没就绪时连 codex 都不去找，
    # 拦下来也不留半截证据包（反例测试才好断言「什么都没发生」）。
    egress = None
    if not args.offline_graph:
        egress = check_egress(args.egress_check)

    # offline 模式不需要 codex：别为了跑后半链的非回归测试，要求机器上装了 codex
    codex_js = None if args.offline_graph else find_codex()
    say("[manager] codex  = " + (str(codex_js) if codex_js else "(offline，不调)"))
    say("[manager] run_id = " + run_id)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "BRIEF.md").write_text(brief_path.read_text(encoding="utf-8"), encoding="utf-8")

    graph_path = outdir / "GRAPH.json"
    total_cost = 0.0
    attempts = []
    t_start = time.time()

    for attempt in range(1, args.repair + 2):
        say("")
        say("[manager] ---- 第 %d 次调用（%s）----" % (attempt, args.model))
        if args.offline_graph:
            # 测试钩子：不调 codex。第 2 次起故意给同一份，用来验证「修复轮用完」这条路径。
            say("[manager] [offline] 跳过 codex，改用 " + args.offline_graph)
            struct = json.loads(Path(args.offline_graph).read_text(encoding="utf-8"))
            meta = {"engine": "(offline)", "model": "(offline)", "elapsed_s": 0, "cost_usd": 0,
                    "readonly_enforcement": "none", "requested_reasoning_effort": None}
        else:
            struct, meta = call_manager(codex_js, args.model, schema, prompt, ROOT, args.timeout, args.effort)
        if meta.get("cost_usd"):
            total_cost += float(meta["cost_usd"])

        raw_path = outdir / ("GRAPH.attempt%d.json" % attempt)
        raw_path.write_text(json.dumps(struct, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        graph_path.write_text(json.dumps(struct, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        say("[manager] 耗时 %ss  成本 $%s  节点数 %s" % (
            meta.get("elapsed_s"), meta.get("cost_usd"),
            len(struct.get("nodes") or []) if isinstance(struct.get("nodes"), list) else "?"))

        gate, rc = run_gate(graph_path)
        (outdir / "GATE.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        attempts.append({
            "attempt": attempt,
            "call": meta,
            "gate_ok": bool(gate.get("ok")),
            "errs": [e.get("code") for e in (gate.get("errs") or [])],
            "warns": [w.get("code") for w in (gate.get("warns") or [])],
        })

        if gate.get("ok"):
            say("[manager] Gate 通过：%d 节点 / %d 波 / 关键路径 %d 节点 / %d warn" % (
                gate.get("nodeCount") or 0, gate.get("waveCount") or 0,
                gate.get("criticalLen") or 0, gate.get("warnCount") or 0))
            break

        errs = gate.get("errs") or []
        say("[manager] Gate 拒收，%d 条 error：" % len(errs))
        for e in errs:
            say("  [err ] %s  %s" % (e.get("code"), e.get("msg")))

        if attempt > args.repair:
            say("[manager] 修复轮已用完（--repair %d）。图留在 %s 供人看。" % (args.repair, raw_path.as_posix()))
            break

        # 修复轮：错误清单写文件，用指路式 prompt 叫它读 —— 不把长中文塞进 argv
        errs_path = outdir / ("GATE-ERRORS.attempt%d.txt" % attempt)
        lines = ["上一版图被 Gate 拒收，原因如下（%d 条）：" % len(errs), ""]
        for i, e in enumerate(errs, 1):
            lines.append("%d. [%s] %s" % (i, e.get("code"), e.get("msg")))
        warns = gate.get("warns") or []
        if warns:
            lines += ["", "另有 %d 条 warn（不拒收，但建议一并改）：" % len(warns)]
            for w in warns:
                lines.append("- [%s] %s" % (w.get("code"), w.get("msg")))
        errs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        prompt = REPAIR_PROMPT.format(
            errors=errs_path.as_posix(), rules=rules_path.as_posix(), brief=brief_path.as_posix(),
        )

    final_gate = run_gate(graph_path)[0]
    ok = bool(final_gate.get("ok"))

    import hashlib
    sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()

    run_meta = {
        "run_id": run_id,
        "brief": brief_path.as_posix(),
        "model": args.model,
        "requested_reasoning_effort": args.effort if not args.offline_graph else None,
        "attempts": len(attempts),
        "attempt_log": attempts,
        "gate_ok": ok,
        "total_cost_usd": round(total_cost, 6),
        "wall_s": round(time.time() - t_start, 1),
        "graph": graph_path.as_posix(),
        "graph_sha256": sha,
        "plan_id": final_gate.get("plan_id"),
        "waves": final_gate.get("waves"),
        "critical_path": final_gate.get("criticalPath"),
        # 第 1 轮验收 B2：自检结果必须落在证据包里，否则「走了没走 1081 出口」事后无从证明。
        # offline / dry-run 时为 null（本来就不调 claude）。
        "egress": egress,
        "note": "graph_sha256 就是 Mandate（DESIGN §1 ②）：人点头对着这个 hash，改一个字节就对不上。",
    }
    (outdir / "RUN.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    say("")
    say("[manager] ================= 结果 =================")
    say("[manager] Gate        " + ("OK" if ok else "REJECTED"))
    say("[manager] 尝试次数    %d" % len(attempts))
    say("[manager] 总成本      $%s" % round(total_cost, 4))
    say("[manager] 总耗时      %ss" % round(time.time() - t_start, 1))
    say("[manager] 图          " + graph_path.as_posix())
    say("[manager] sha256      " + sha)
    if final_gate.get("waves"):
        say("[manager] 波次：")
        for i, w in enumerate(final_gate["waves"]):
            say("  [wave%d] %s" % (i, " · ".join(w)))
        say("[manager] 关键路径    " + " → ".join(final_gate.get("criticalPath") or []))
    say("[manager] 下一步：人对着 waves 核一遍，点头后再把 dryRun 拿掉真跑（DESIGN §3 ③）。")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
