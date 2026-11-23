#!/usr/bin/env python3
"""runner.py —— DAG 编排 Step 2 · A1：把「复核」从引擎里搬到引擎外

为什么要有它（DESIGN §8 Step 2 / `tasks/DAG-2026-11-06/TASK-step2a1.md`）：
  `dag.workflow.mjs` 是 `workflow` 工具的脚本体，**没有文件系统**（`dsh-tool-workflow/lib/index.js:148`
  就是那条契约）。所以图里所有验收输入都只能是模型自报的：`changed` 是它自己报的，
  `cmd` 的退出码也是它自己报的。这个脚本住在引擎外，**手里有真文件系统**：

    R1  `changed` 自报  vs  节点起跑前后的 mtime+sha256 快照 diff
    R2  `accept.type=cmd` 由它真起子进程跑，退出码不是模型报的
    R3  只读节点在**沙箱层**设 `DSH_PERMISSION_MODE=read-only`（硬拦），事后再用快照核一遍
        ⚠️ **R3 只对 `headless` / `fixture` 成立**。`codex` 引擎上沙箱那一层不存在（见下面
        「只读执法层」那段），只读约束退到 R1 那条快照核 —— 强度降级，记进 `readonly_enforcement`。

它**不重写 Gate**：图合不合法一律问 `node scripts/dag-gate.mjs --json`；波次也用 Gate 的 dry-run 输出，
不自己算拓扑（两份调度逻辑一改就漂）。

执行模型（B1，2026-11-09）：**波内只读节点并发，写节点仍串行**。
  并发资格 = `kind ∈ READONLY_KINDS` **且**该引擎的只读执法是沙箱（`READONLY_ENFORCEMENT`），
  不认图里「writes 为空」这种声明 —— 声明是模型写的，沙箱是引擎层保证（`parallel_eligible`）。
  ⚠️ 2026-11-11（第 1 轮验收官 B1）：「沙箱是引擎层保证」只对 headless / fixture 成立；
  codex 的只读执法是 `snapshot_only`，所以**它在 codex 下压根不给并发资格**（整组降级串行，
  原因进 `RUN.json.parallel.demoted`）。
  为什么先只开只读：`compare()` 的四类判定都靠「起跑窗口内只可能这一个节点在动」，
  写节点并行会让兄弟的写入互相污染，**证据变脏**；只读节点没有写入可归因。
  并发组的归因是**波级**的：窗口内出现任何变化，组内**每个**参与节点都判 `readonly_violation`
  并标 `attribution="wave"` —— 不许栽给某一个节点（TASK-step2b K3）。代价是「连坐」，见 DELIVERY。
  证据：每 attempt 记 `started_at` / `finished_at`，`RUN.json.parallel` 给组与重叠时长 ——
  「都跑完了」串行也能满足，**时间区间重叠**才是真并发的证据。
  写节点的并发要等「工具轨迹归因」：`tool_calls` 已在 rec 里落盘，但目前**只记录、不作判据**。

A2-min（2026-11-07）在 A1 之上补了六项 **DAG 语义**，全部对齐 `scripts/dag.workflow.mjs`：
  `on_fail.retry`（同档重试）· `when`（eq/in/exists 三态）· `on_fail.then=replan`（标记，不重规划）
  `join`（all/any）· `budget.max_calls`（首发预留 + 重试吃余量）· `on_fail.escalate`（升档再跑一次）
  为什么要补：经理层产出的真图 7/7 节点都带 `retry=1`（`manager.schema.json` 把 `on_fail` 设成
  required），A1 一律拒收 —— 等于「经理能出图、但一张都跑不了」。

用法：
  python scripts/runner.py <GRAPH.json> [--run-id X] [--engine headless|fixture|codex]
                                         [--fixture-dir D] [--dry-plan] [--timeout N]

退出码：
  0 = 所有节点 ok          1 = 有节点 failed / skipped / need_info
  2 = 图不合法 / 超出能力边界 / 运行目录已存在
  3 = 环境问题（缺 node / 缺 dsh / 缺 codex CLI）
  4 = `--engine codex` 的 egress 自检不是 READY（判法同 manager.py：**硬拦，没有跳过开关**）

`--engine codex`（2026-11-11）：用 ChatGPT 订阅那侧的 codex CLI 当员工。它与 headless **共用**同一套判据：
`compare()` 一行没改 —— 它看的是快照 diff，与引擎无关。三处差别只在这层：
  1. 子进程 argv 不同（`codex exec --json ... -`，prompt 走 stdin —— 含空格的 prompt 用 argv 传会被拆）；
  2. 事件流形状不同（`thread.started` / `item.*` / `turn.completed`，见 `parse_events_codex`）；
  3. egress 换成 `codex-egress-check.ps1`（同一隧道、同一伪装出口），并把 `HTTP(S)_PROXY` 注入子进程。
⚠️ 一个曾经记错的事实已更正：codex 的 `-s` 取值**就是** `read-only` / `workspace-write` /
`danger-full-access`，与 DSH 的 `DSH_PERMISSION_MODE` 同名同义；`never` / `approve-for-me` /
`dangerously-bypass` 是**审批策略**那一维，不是沙箱档。

⚠️ **只读执法层（2026-11-11 真跑实测，本模块最容易被误读的一条）**：
  名字一样**不等于**强度一样。Windows 上 codex 的 `-s read-only` 实际是「禁止执行任何命令」——
  连 `Get-Content` 都被 `blocked by policy` 挡掉（`tasks/CODEX-ENGINE-2026-11-11/PROBE-READONLY.txt`
  三组对照，`-c sandbox_permissions` 也救不回来）。所以 codex 的只读节点**拿的是 workspace-write**，
  只读约束**退到事后快照核**（`compare()` 的 `readonly_violation`，判据一字未改）。
  这条降级在三个地方可核：`RUN.json.readonly_enforcement` / `RUN.json.nodes[].readonly_enforcement` /
  每次 attempt 的 `NODES/*.aN.json`。**不要**把「codex 与 headless 同名」读成「同一个强度」。

⚠️ 失效边界（写在这里，不只写在文档里）：
  `--engine fixture` 是**零成本测试钩子**：用一个假 dsh 扮演员工，不调任何模型。
  它会在 `runs/<id>/` 留下 `RUN.json.engine="fixture"` + `FAKE` 占位文件；
  **它产出的任何结论都不能当交付证据**。headless 员工的真实行为只认 `engine="headless"` 的运行。
  由来：`manager.py --offline-graph` 有同形状的钩子，当时只在帮助文本里写了用途、没有机械标记。

⚠️ `escalate` 的天花板（**2026-11-23 更新** · `tasks/DAG-MIXED-MODEL-2026-11-23/`）：
  历史上两次变化，别混：
  - 2026-11-12 之前：`strong` 档是 `deepseek-official/deepseek-v4-pro`，**不是 opus**。
  - 2026-11-12（`tasks/V4PRO-REMOVE-2026-11-12/`）：`strong` 整档移除 —— v4-pro 在本体系无价值
    （`DESIGN.md` §6 自己的 A/B 实测 flash 十项全胜、便宜 3.3 倍、还支持图片输入，而 v4-pro 纯文本），
    全档位统一 `deepseek-flash`，`on_fail.escalate` 只换档位名、不换模型。
  - **2026-11-23 起**：`strong` 以另一个含义回来 —— `codex-gw/gpt-6.2-astra`（**ChatGPT 订阅通道**，
    经本地网关转发）。这条路**不走** `claude -p --model opus`，所以原先那句「DESIGN 的逃生舱要 egress
    前置 + 另一套引擎分支」不再适用：`codex-gw` 是 pi-ai 的一个普通 provider 路由，`headless` 引擎
    照样用 `--patch` overlay 就能换过去（`headless/cordis.patch.yml` 补了这条路由）。
    于是 `on_fail.escalate` 到 `strong` **真的换模型**了（判据：本档模型 ≠ work 档模型）。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GATE = HERE / "dag-gate.mjs"
FAKE_WORKER = ROOT / "tasks" / "DAG-2026-11-06" / "fixtures" / "fake-worker" / "worker.py"

# `--engine codex` 的两个外部路径（都在 `~/.dsh`，不在工程树里）：
#   CODEX_BIN    —— codex CLI 本体。**不在 PATH**（插件按包内路径起它），裸 `codex` 命令不存在，
#                   所以只能按包路径定位；找不到就 die(3)，不许退化成「用 PATH 里那个」。
#   CODEX_EGRESS —— codex 侧的 egress 自检（与 claude 同一个隧道、同一个伪装出口，只是多核了
#                   patch 里 codex 的 HTTP(S)_PROXY 端口）。端口真值只在 claude 那个脚本里，
#                   这里**不写死端口**，端口从它的 `-Json` 输出里取（见 check_codex_egress）。
CODEX_BIN = (Path.home() / ".dsh" / "profiles" / "web" / "node_modules"
             / "@openai" / "codex" / "bin" / "codex.js")
CODEX_EGRESS = Path.home() / ".dsh" / "scripts" / "cdp" / "codex-egress-check.ps1"
# codex 引擎的**员工模型**（2026-11-12，CLAUDE-TO-GPT 单元）。
# 为什么显式写死、不吃 CLI 默认：默认模型随 codex 版本变（0.153.4 默认与 0.167.2 不同），
# 而 RUN.json 里的 model 字段是证据 —— 不写死就会出现「这张图到底用哪个模型跑的」说不清。
# 可用性前提：codex CLI >= 0.16x（0.153.4 的模型目录里没有 6.x 系列，服务端会 400 model_not_found）。
# 2026-11-23：6.1-sol → 6.2-sol（伙伴要求统一升 6.2；同日在网关实测 gpt-6.2-astra 可用）。
CODEX_WORKER_MODEL = "gpt-6.2-sol"
# `strong` 档（订阅通道 GPT）经本地网关 `codex-gw` 转发时，provider 解析需要一个
# `CODEX_GW_KEY` 环境变量存在（`apiKeyEnv: CODEX_GW_KEY`）。网关**不校验入站凭据**，
# 所以这个占位值没有任何权限；写进配置才是事故（见 `headless/cordis.patch.yml` 的说明）。
CODEX_GW_KEY_PLACEHOLDER = "gateway"
# Requested effort only: this records the CLI request, not a server acknowledgement.
CODEX_ROLE_EFFORT = {
    "scout": "medium", "contract": "high", "work": "high",
    "integrate": "high", "check": "high", "replan": "high",
}

# 只读节点：侦察 / 契约 / 核查 / 返工规划 —— 它们在**沙箱层**被设成 read-only
READONLY_KINDS = {"scout", "check", "contract", "replan"}
ALL_KINDS = {"scout", "contract", "work", "integrate", "check", "replan"}

# 档位表：**逐字对齐** `dag.workflow.mjs:33-51`。改一处必须两处一起改，
# 否则同一张图在解释器和 runner 下会用不同模型跑 —— 那种漂移没人看得见。
TIERS = {
    "scout":     {"provider": "deepseek-official", "model": "deepseek-flash"},
    "work":      {"provider": "deepseek-official", "model": "deepseek-flash"},
    "integrate": {"provider": "deepseek-official", "model": "deepseek-flash"},
    "check":     {"provider": "deepseek-official", "model": "deepseek-flash"},
    # ⚠️ 2026-11-23（`tasks/DAG-MIXED-MODEL-2026-11-23/`）：`strong` 档**以另一个含义**加回来。
    # 它 2026-11-12 被移除时指的是 deepseek-v4-pro（比 flash 更弱、更贵、纯文本，无存在价值，见
    # `tasks/V4PRO-REMOVE-2026-11-12/`）；现在它指**订阅通道的 GPT-6.2-Astra**，经本地网关
    # `codex-gw`（127.0.0.1:18765）转发到 ChatGPT 订阅。判断型节点（contract / integrate / check /
    # replan）走它，吞吐型（scout / work）留 deepseek-flash —— 一张图内混跑，引擎仍是同一个 headless。
    #
    # **通道性质**：subscription（订阅额度，非按量计费）。这是它与 v4-pro 那次最根本的区别。
    "strong":    {"provider": "codex-gw", "model": "gpt-6.2-astra"},
}
# ⚠️ 2026-11-12（`tasks/V4PRO-REMOVE-2026-11-12/`）：v4-pro 时代的 `strong` 全档位统一 `deepseek-flash`
# 的三个理由（flash 十项全胜 v4-pro / 便宜 3.3 倍 / flash 支持图片）**对 v4-pro 仍然成立**，
# 2026-11-23 加回的 `strong` 不是它 —— 别拿那段历史反驳这张表。
# `on_fail.escalate` 到 `strong` 时 `run_attempt()` 会**真的写 overlay 换模型**（判据是「本档模型 ≠
# work 档模型」，work 档是 deepseek-flash，strong 是 gpt-6.2-astra，条件成立）。
# kind → 默认档，对齐 `dag.workflow.mjs:54-61`
KIND_TIER = {
    "scout": "scout", "contract": "strong", "work": "work",
    "integrate": "strong", "check": "strong",
    # ⚠️ 2026-11-23：`replan` 从 `work`（flash）改回判断型 `strong`（GPT）。返工规划要读整张图的
    # 失败原因再决定改什么，是典型的判断活 —— 伙伴 2026-11-23 拍板的映射（DESIGN §9.3）。
    "replan": "strong",
}

# 「只读」这个约束在**哪一层**执法（2026-11-11 真跑暴露的能力边界，证据 `PROBE-READONLY.txt`）：
#   headless / fixture —— 引擎层沙箱硬拦（`DSH_PERMISSION_MODE=read-only`，写不进去）。
#   codex            —— Windows 上 `-s read-only` 实际是「**禁止执行任何命令**」：连
#                        `Get-Content` 都被 `blocked by policy` 挡掉，`-c sandbox_permissions=
#                        ["disk-full-read-access"]` 也救不回来（三组对照见探针）。
#                        所以 codex 的只读节点只能拿 workspace-write；**只读约束退到事后快照核**
#                        （`compare()` 的 `readonly_violation`，判据一字未改）。
# 这是「强度换能力」的降级，必须在证据里看得见 —— 所以它进 `rec` 与 `RUN.json`，
# 不许只在文档里写一句了事。
READONLY_ENFORCEMENT = {"headless": "sandbox", "fixture": "sandbox", "codex": "snapshot_only"}
# 需要在请求层换模型时，用 `--patch` overlay 覆盖这个配置项（2026-11-07 实测）
DEFAULT_MODEL_ID = "agent-default-model"

# 快照跳过的目录：依赖树、git、证据包自身（否则每轮 diff 全是自己的证据文件）、py 缓存
# ⚠️ 2026-11-10（EVIDENCE-GUARD C3）：`runs` **仍然不进「全量哈希」**，但会进「轻量指纹」（只 stat）。
# 原因：员工子进程的 cwd 是工程根，`runs/<id>/NODES/*.json` 对它可读可写 —— 早先整个目录被跳过，
# 于是「员工改证据」在证据上**完全不可见**（F13）。现在 `runs/` 的变化单独进 `runs_changes`。
EXCLUDE_DIRS = {"node_modules", ".git", "runs", "__pycache__", ".pytest_cache", ".venv"}
RUNS_DIR = "runs"
EXCLUDE_SUFFIX = {".log"}  # 追加写入，每轮必变，进 diff 只是噪音

RESULT_RE = re.compile(r"<RESULT>\s*(.*?)\s*</RESULT>", re.S)
LIST_FIELDS = {"changed", "patch_targets", "allow_paths", "artifacts", "missing"}

# F1（2026-11-09）：`out` 里声明了 `patch` 的节点，按 `DESIGN.md:203`「员工不碰主树，只返回 diff；
# 唯一写手是集成节点」判 —— 它该交 diff，**不该落盘**。判据只看图的声明，不引入 `may_write` 之类的
# 自豁免：可自豁免的规则等于没有规则（同 v2 `CHECK_*` 那次，`readonly:true` 自声明就没做）。
PATCH_FIELD = "patch"
# 一次 attempt 的全部判定类别。**键集合固定**（不随节点变），否则「这次到底核了几项」说不清。
# 2026-11-10（EVIDENCE-GUARD C3/C4）追加两条，都是 F13 的机器判据：
#   evidence_write    —— 起跑窗口内 `runs/` 下出现了变化（员工在碰证据包；本 run 的 SANDBOX/ 不算）
#   evidence_tampered —— 已登记证据文件的 sha256 对不上（窗口外动手脚，或改完把 mtime 复原）
CHECK_KEYS = ("overclaim", "underclaim", "out_of_scope", "readonly_violation",
              "fs_write_forbidden", "patch_missing", "patch_targets_missing", "patch_out_of_scope",
              "evidence_write", "evidence_tampered",
              # 2026-11-10（SANDBOX-VERDICT K5–K7）核查节点的裁决判据，见 `VERDICT_FIELD` 那一段。
              "check_rejected", "check_protocol_error")
# 这两条属于**不可洗**的损害：它们不因重试消失，重试只会在被污染的证据上再叠一层。
HARM_KEYS = ("out_of_scope", "readonly_violation", "fs_write_forbidden",
             "evidence_write", "evidence_tampered")
# ⚠️ `check_rejected` / `check_protocol_error` **有意不进 HARM_KEYS**：它们不是「已经发生的损害」，
# 是「这次核查没通过」。语义差别有实际后果 —— harm 的措辞是「不可用重试洗掉」，而核查失败的正确
# 处置是「停手、把裁决留给人看」，不是把节点钉成被污染状态。两条在调度层同样是硬停（见两处停止条件）。

# N2（2026-11-10）：核查节点的裁决字段。
#   * **只对 `kind == "check"` 且 `out` 声明了 `verdict` 的节点生效**。`verdict` 不是 check 专属字段：
#     本工程的 fixture 与部分图里 scout 用它表达 go/stop，而 `when.ref` 读的正是这个字段
#     （`when_value()`）—— 判据一旦按「有 verdict 就算」扩到全节点，`when-yes` / `when-no` /
#     `join-any` 三个场景会当场改语义。另有 check 只出 `summary`（不担裁决职责），也不该被这条
#     判据连坐。kind 与 out 都取**图定义**，不信员工自报。
#   * 词表只认 `pass`。`ok`/`done` 本轮**有意不放宽**：真图的 accept 写死了「verdict 只能是 pass 或
#     fail」，在这里悄悄兼容别名等于在 runner 里改协议，而图与提示词都不知道。
VERDICT_FIELD = "verdict"
VERDICT_PASS = ("pass",)
VERDICT_REJECT = ("fail", "rework", "escalate")
CHECK_KIND = "check"

# Windows 上 Python 的 stdout 默认走 locale 编码（cp936），往下游管道一传中文就乱。
# 自己和调用方都按 UTF-8 对齐：证据包与 harness 都靠这个。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 基础

def say(msg=""):
    print(msg, flush=True)


def die(code, msg):
    say("[runner] FATAL " + msg)
    sys.exit(code)


def find_node():
    w = shutil.which("node") or shutil.which("node.exe")
    if not w:
        die(3, "找不到 node —— runner 要用它跑 dag-gate.mjs")
    return w


def find_dsh_bin():
    """找 dsh 的**真实入口** lib/bin.js，而不是 npm 的 .cmd shim。

    理由同 `manager.py:find_claude()`：.cmd 会再套一层 cmd.exe，argv 转义又回来了。
    `node <bin.js>` 是最干净的路（本机实测 `node lib/bin.js --version` → 0.2.1-rc.2）。
    """
    env = os.environ.get("DSH_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        die(3, "DSH_BIN 指向的文件不存在：" + env)
    cands = []
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        cands.append(Path(appdata) / "npm" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js")
    cands.append(Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js")
    for c in cands:
        if c.is_file():
            return c
    die(3, "找不到 dsh 入口（lib/bin.js）。用 DSH_BIN 指定，或先安装 dsh。")


def find_codex_bin():
    """找 codex CLI 本体，返回 `bin/codex.js` 的 Path。

    **为什么不能靠 PATH**：codex 装在 `~/.dsh/profiles/web/node_modules/@openai/codex`（插件按包内路径起它），
    裸 `codex` 命令在本机不存在。找不到就 die(3) —— 不回退 PATH，否则「用的是哪个 codex」说不清。
    """
    if CODEX_BIN.is_file():
        return CODEX_BIN
    die(3, "找不到 codex CLI：%s\n  它是插件包的副产物，缺了就先用 dsh 装上 "
           "`@deepseek-ai/dsh-subagent-codex`（版本对齐 dsh），别去猜 PATH。" % CODEX_BIN)


def judge_egress(rc, stdout):
    """判 `codex-egress-check.ps1 -Json` 的结论。返回 `(ok, info)`。

    判法沿用 `manager.py:check_egress` 那次修正（2026-11-07 第 2 轮 N1）：
    **退出码 0 不够，必须真的看到 `result == "READY"`。**
    只查退出码等于换名跳过开关 —— 一个内容为 `exit 0` 的空脚本就能让它变绿。

    fail-closed 的三处：JSON 解析不出来 / `result` 不是 READY / `port` 不合法。
    """
    info = {"rc": rc, "result": None, "port": None, "action": None,
            "egress": None, "raw_tail": (stdout or "").strip()[-600:]}
    if rc != 0:
        info["why"] = ("自检退出码 %s（0=READY / 3=隧道没起 / 4=出口 IP 不对 / 5=patch 配置错 / "
                       "6=缺脚本 / 7=trace 补丁缺失或漂移）" % rc)
        return False, info
    obj = None
    # `-Json` 是**单行** JSON；仍逐行从后往前找，防止以后脚本改成多行输出。
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            cand = json.loads(line)
        except Exception:
            continue
        if isinstance(cand, dict):
            obj = cand
            break
    if obj is None:
        info["why"] = "-Json 输出里没有可解析的 JSON 对象（fail-closed）"
        return False, info
    info.update({"result": obj.get("result"), "port": obj.get("port"),
                 "action": obj.get("action"), "egress": obj.get("egress")})
    if str(obj.get("result") or "").strip().upper() != "READY":
        info["why"] = "result 不是 READY（实际 %r）—— 代理/出口有问题，**不许起跑**" % (obj.get("result"),)
        return False, info
    port = obj.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        info["why"] = "port 不是合法端口（实际 %r）—— 端口是子进程代理的唯一真值，缺了就没法注入" % (port,)
        return False, info
    return True, info


def check_codex_egress(script=None):
    """跑 codex 侧 egress 自检，**不是 READY 就 die(4)**。返回 info（含 port）。

    为什么硬拦在 runner 里、不只在文档里：`--engine codex` 的流量走的是伪装出口，
    出口不对会留风控痕迹；而且这个脚本是**唯一**知道代理端口的地方（端口真值在
    `claude-egress-check.ps1`，这里不写死）。绕过它就是绕过整条出口约束。

    `script` 只给断言台用（指一个临时假脚本），**CLI 不暴露** —— 不留可跳过的开关。
    """
    p = Path(script or CODEX_EGRESS).expanduser()
    if not p.is_file():
        die(4, "codex egress 自检脚本不存在：%s\n"
               "  这条**不能跳过** —— 它保证 codex 走伪装出口（AGENTS.md 硬规则）。\n"
               "  脚本丢了先把 <DSH-SCRIPTS>/cdp/codex-egress-check.ps1 修回来。" % p)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(p), "-Json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
    except Exception as e:  # noqa: BLE001
        die(4, "起 egress 自检失败：%s\n  （powershell 不在 PATH 或缺执行权限时也走这条）" % e)
    ok, info = judge_egress(r.returncode, r.stdout or "")
    info["stderr_tail"] = (r.stderr or "")[-800:]
    info["script"] = str(p)
    say("[runner] egress   codex-egress-check -> %s (rc=%s port=%s)" % (
        "READY" if ok else "NOT READY", info.get("rc"), info.get("port")))
    if not ok:
        die(4, "codex egress 自检没过：%s\n  %s\n"
               "  修法见 AGENTS.md「验收前置自检」；隧道没起可以加 -Start 现拉一次（别做成常驻）。"
            % (info.get("why"), info.get("raw_tail") or ""))
    return info


_KEY_CACHE = {}


def resolve_llm_key(name="DEEPSEEK_API_KEY"):
    """给 headless 员工找 LLM key。返回 (value|None, source)。

    **为什么需要这一步**：`dsh-headless` 的 bundle patch 把 `credentials` 服务整个禁用了
    （`dsh-headless/cordis.patch.yml:36-37`），于是它只认**启动环境变量**里的 key，
    读不到 web profile 的凭据存储。不注入就会 `MISSING_CREDENTIAL` —— 本机实测过一次，
    见 `runs/probe-ro/NODES/s_probe.json` 的 stderr。

    安全边界：**这个函数只把值交给子进程环境；调用方只许记录 `source`，不许记录 value。**
    证据包里出现密钥 = 事故。
    """
    if name in _KEY_CACHE:
        return _KEY_CACHE[name]
    val = os.environ.get(name)
    if val:
        _KEY_CACHE[name] = (val, "env")
        return _KEY_CACHE[name]
    cred = Path.home() / ".dsh" / ".credentials.yaml"
    if cred.is_file():
        # 该文件是 YAML，但结构固定（version/records/refs）。只取 refs 段下的 `  KEY: value`，
        # 不为它引入一个 YAML 依赖。
        pat = re.compile(r"^\s{2}" + re.escape(name) + r":\s*(.+?)\s*$")
        for line in cred.read_text(encoding="utf-8").splitlines():
            m = pat.match(line)
            if m:
                v = m.group(1).strip().strip('"').strip("'")
                if v:
                    _KEY_CACHE[name] = (v, "credentials.yaml:refs")
                    return _KEY_CACHE[name]
    _KEY_CACHE[name] = (None, "missing")
    return _KEY_CACHE[name]


# ---------------------------------------------------------------- Gate（不重写判据）

def run_gate(graph_path):
    """把图丢给 `dag-gate.mjs --json`。返回 (data, rc, err)。"""
    node = find_node()
    r = subprocess.run(
        [node, str(GATE), str(graph_path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "").strip()
    if not out:
        return None, r.returncode, ("dag-gate 无输出；stderr=" + (r.stderr or "")[:800])
    try:
        data = json.loads(out[out.index("{"):])
    except Exception as e:
        return None, r.returncode, ("dag-gate 输出不是 JSON：%s；前 500 字=%s" % (e, out[:500]))
    return data, r.returncode, None


def plan_check(graph, gate):
    """A2-min 的能力边界。返回 unsupported 列表（空 = 能跑）。

    与 A1 的差别：过去 `when` / `then=replan` / `retry>0` 一律拒收 —— 现在**实现了**，所以不再拒收。
    **拒收项只是换了一批，不是消失了**；任何本实现做不到的图字段仍然退出码 2 + 点名：
      - 未知 `kind` / 未知档位（`tier` 与 `on_fail.escalate` 两个位置）
      - `when.op` 不是 eq / in / exists
      - `join` 不是 all / any
      - `on_fail.retry` 不是非负整数
    对齐基准：`dag.workflow.mjs:283-300`（Gate 的 NODE_ON_FAIL / NODE_WHEN 规则）与 `:63` 的 KINDS。

    A1 的那段注释在这里保留下来，因为它解释的是**为什么当初要拒收**：
      「静默忽略比没有更坏 —— 图写 `retry: 1` 会看起来跑了，实际一次失败就 abort」。
    这次就是把这句兑现：不是删掉拒收、而是把语义补上。
    """
    bad = []
    for n in graph.get("nodes", []):
        nid = n.get("id", "?")
        kind = n.get("kind")
        if kind not in ALL_KINDS:
            bad.append("UNSUPPORTED_KIND: %s kind=%r（已知：%s）" % (nid, kind, ", ".join(sorted(ALL_KINDS))))
        tier = n.get("tier")
        if tier is not None and tier not in TIERS:
            bad.append("UNSUPPORTED_TIER: %s tier=%r（已知档位：%s）" % (nid, tier, ", ".join(sorted(TIERS))))
        w = n.get("when")
        if isinstance(w, dict):
            if w.get("op") not in ("eq", "in", "exists"):
                bad.append("UNSUPPORTED_WHEN_OP: %s op=%r（只支持 eq / in / exists）" % (nid, w.get("op")))
        on_fail = n.get("on_fail") or {}
        esc = on_fail.get("escalate")
        if esc is not None and esc not in TIERS:
            bad.append("UNSUPPORTED_TIER: %s on_fail.escalate=%r（已知档位：%s）" % (
                nid, esc, ", ".join(sorted(TIERS))))
        retry = on_fail.get("retry")
        if retry is not None and (isinstance(retry, bool) or not isinstance(retry, int) or retry < 0):
            bad.append("UNSUPPORTED_RETRY_VALUE: %s on_fail.retry=%r（必须是非负整数）" % (nid, retry))
        join = n.get("join")
        if join is not None and join not in ("all", "any"):
            bad.append("UNSUPPORTED_JOIN: %s join=%r（只支持 all / any）" % (nid, join))
    waves = gate.get("waves") or []
    flat = [x for w in waves for x in w]
    ids = [n.get("id") for n in graph.get("nodes", [])]
    if sorted(flat) != sorted(ids):
        bad.append("WAVES_MISMATCH: Gate 的波次没覆盖全部节点（waves=%r nodes=%r）" % (flat, ids))
    return bad


# ---------------------------------------------------------------- 语义：when / join / 档位
# 这一段的每一行都对应 `dag.workflow.mjs` 的一处。**不另立语义** —— 同一张图在两个执行器下
# 行为不同，是比「缺功能」更难查的问题。

def cmp_v(x):
    """对齐 `dag.workflow.mjs:827` 的 `cmp()`：只对字符串做 trim + lower。

    一个 `"Pass"` 不该把分支断掉；但措辞差异（`pass` vs `通过`）治不了 —— 那是 Gate 打
    `WHEN_BRITTLE` 提醒的领域，不是这里能修的。
    """
    return x.strip().lower() if isinstance(x, str) else x


def eval_when(w, val):
    """对齐 `dag.workflow.mjs:829-833`。

    ⚠️ `exists` 的判据是「不是 None、不是空串」—— **空数组 `[]` 算存在**（JS 那边
    `val !== undefined && val !== null && val !== ''` 同理）。真图 `w_docs.when` 引用的
    `s_code.allow_paths` 正是数组，侦察交空数组时它照样成立。
    """
    op = w.get("op")
    if op == "exists":
        return val is not None and val != ""
    if op == "in":
        want = w.get("val")
        return isinstance(want, list) and any(cmp_v(x) == cmp_v(val) for x in want)
    return cmp_v(val) == cmp_v(w.get("val"))


def when_value(w, results, status):
    """取出 `when.ref` 指向的值。返回 `(value, why_unavailable)`。

    对齐 `dag.workflow.mjs:918-931`：`status` 字段直接取节点状态；其它字段要求源节点
    **status == ok**，否则算「取不到」→ 下游 skipped（而不是拿 None 去比出个假结果）。
    """
    ref = w.get("ref")
    if not isinstance(ref, str) or "." not in ref:
        return None, "when.ref 不是 <节点id>.<字段> 形状：%r" % (ref,)
    nid, field = ref.split(".", 1)
    if nid not in status:
        return None, "when 引用的 %s 没有结果（不在已执行节点里）" % nid
    if field == "status":
        return status[nid], None
    if status[nid] != "ok":
        return None, "when 依赖的 %s 不是 ok（%s），取不到 %s" % (nid, status[nid], field)
    return (results.get(nid) or {}).get(field), None


def node_tier(node):
    """节点实际用的档位：显式 `tier` 覆盖，否则按 kind 取默认。对齐 `:933`。"""
    return node.get("tier") or KIND_TIER.get(node.get("kind")) or "work"


def tiers_for(node):
    """要依次尝试的档位。对齐 `dag.workflow.mjs:936-937`：base + 一个 escalate，同档不重复。"""
    base = node_tier(node)
    out = [base]
    esc = (node.get("on_fail") or {}).get("escalate")
    if esc and esc in TIERS and esc != base:
        out.append(esc)
    return out


def parallel_eligible(node, engine="headless"):
    """波内并发的**唯一**资格：沙箱层会被硬设成 read-only 的 kind（TASK-step2b K1/K6）。

    为什么不认「writes 为空」：`writes` 是模型自己填的图字段，声明与行为不是一回事。
    沙箱 `DSH_PERMISSION_MODE=read-only` 是引擎层保证，模型骗不了它。

    **2026-11-11 更正（第 1 轮验收官 B1）**：上面那句「引擎层保证」只对 `headless` / `fixture` 成立。
    `codex` 的只读执法是 `snapshot_only`（实测见 `PROBE-READONLY.txt`：Windows 上 `-s read-only`
    禁一切命令），沙箱那一层**并不存在** —— 这时让只读节点并发，兄弟节点就会读到「本来不该被写」的内容。
    所以资格判据里必须带引擎这一维，不能只看 kind。

    `writes` / `allow_from` / `allow_under` 非空时**降级串行**：
    `CHECK_WRITES` 只覆盖 `check`，一个带写集的 `scout` 在 Gate 层不会被拦（TASK-step2b §五）。
    """
    if node.get("kind") not in READONLY_KINDS:
        return False
    if READONLY_ENFORCEMENT.get(engine) != "sandbox":
        return False
    if node.get("writes"):
        return False
    for acc in (node.get("accept") or []):
        if acc.get("type") == "scope" and (acc.get("allow_from") or acc.get("allow_under")):
            return False
    return True


def max_attempts(node):
    """这个节点最坏会用掉几次调用：首档 1+retry 次 + 每个升档各 1 次。"""
    retry = int((node.get("on_fail") or {}).get("retry") or 0)
    return (1 + retry) + (len(tiers_for(node)) - 1)


def demoted_reason(node, engine="headless"):
    """「本该有资格、但因为带写权限被降级成串行」的原因；写节点返回 None（它们本来就没资格）。

    为什么要单独记：只读 kind 带 `writes` 时，Gate 的 `CHECK_WRITES` 只覆盖 `check`（TASK-step2b §五），
    这种节点会被静默降级。静默降级和「本来就不并发」在证据上长得一样 —— 记下来才分得清。

    2026-11-11（B1）：**引擎**本身也能把资格拿走。`codex` 的只读执法不是沙箱，所以只读节点在它下面
    一律不并发 —— 这条原因必须先于 `writes` 判，否则会被误报成「它带写权限」。
    """
    if node.get("kind") not in READONLY_KINDS:
        return None
    enf = READONLY_ENFORCEMENT.get(engine)
    if enf != "sandbox":
        return "引擎 %s 的只读执法是 %s（不是沙箱硬拦），不并发" % (engine, enf)
    if node.get("writes"):
        return "带 writes（Gate 的 CHECK_WRITES 只覆盖 check）"
    for acc in (node.get("accept") or []):
        if acc.get("type") == "scope":
            if acc.get("allow_from"):
                return "带 scope.allow_from（动态写权限）"
            if acc.get("allow_under"):
                return "带 scope.allow_under（路径前缀写权限）"
    return None


def split_wave(wave, nodes, cap, engine="headless"):
    """把一波按「连续的可并发节点」切段。返回 `[[nid,...], [nid], ...]`，**保持波内顺序**。

    段长 > 1 才真并发；段长 1 走原来的串行路径（行为与 A2-min 一模一样）。
    `cap <= 1`（`--no-parallel`）时全部退化成单元素段 —— 这是对照实验用的。
    `engine`（2026-11-11，B1）：并发资格依赖「沙箱层真 readonly」，所以引擎必须参与判定。
    """
    units, buf = [], []
    for nid in wave:
        node = nodes.get(nid) or {}
        if cap > 1 and parallel_eligible(node, engine):
            buf.append(nid)
        else:
            while buf:
                units.append(buf[:cap])
                buf = buf[cap:]
            units.append([nid])
    while buf:
        units.append(buf[:cap])
        buf = buf[cap:]
    return units


def stamp(t0):
    """把 epoch 秒包成「机器可比 + 人可读」两条。时间戳是**本轮新增的证据面**（K7）。"""
    return {
        "started_at": round(t0, 3),
        "started_iso": datetime.fromtimestamp(t0).isoformat(timespec="milliseconds"),
    }


def overlap_seconds(spans):
    """一组 `(start, end)` 区间的**最大重叠时长**。`> 0` 才叫真并发（K2）。

    不重叠时返回 `0.0`（不是负数）—— 负数会让「串行对照」看起来像某种更奇怪的东西。
    """
    spans = [(a, b) for a, b in spans if a is not None and b is not None]
    if len(spans) < 2:
        return 0.0
    return round(max(0.0, min(b for _, b in spans) - max(a for a, _ in spans)), 3)


def deps_block_reason(node, status, nodes):
    """`join` 传播。对齐 `dag.workflow.mjs:909-916`。返回阻断原因或 None。

    A1 完全没有这一段 —— 上游失败了下游照跑。那是**静默偏差**，不是简化。
    """
    deps = [d for d in (node.get("after") or []) if d in nodes]
    if not deps:
        return None
    okflags = [status.get(d) == "ok" for d in deps]
    line = "、".join("%s=%s" % (d, status.get(d)) for d in deps)
    if (node.get("join") or "all") == "any":
        if not any(okflags):
            return "上游没有一个 ok（join=any）：" + line
    elif not all(okflags):
        return "上游未全 ok（join=all）：" + line
    return None


def from_block_reason(node, status, results):
    """`ctx.from` 的**硬依赖**（F10，2026-11-09）。返回阻断原因或 None。

    病根（E2E 第 1 轮验收官 B1 的后半段，比 F1 更危险）：`w_script` 失败后它的 patch 被丢弃，
    下游 `integ` 的 prompt 里 `w_script.patch` 显示「（上游没有给这个字段）」，于是 `integ`
    **自己现写了 122 行**，交付物来源偏离了图的规划；`join=any` 又让它在上游全败时照跑，
    两个核查节点最后全 skip —— **这份代码没有任何独立复核**。

    规则：`ctx.from` 里的 `上游.字段`，若上游**没有 ok** 且那个字段确实没有值 → 本节点不许起跑。
    落 `need_info`（缺输入），不是 `skipped`（被闸门挡下）：两者对下游的 join 语义一样（都算没 ok），
    但人读原因时能分清「它自己缺料」和「上游把它挡了」。

    为什么不用「失败节点也把 result 塞进 results」：那会让下游拿到一份**没通过验收**的产物当成正常输入，
    等于把「上游失败」这个事实抹掉。宁可停，也不要下游在坏输入上继续干。
    """
    refs = (node.get("ctx") or {}).get("from") or []
    missing = []
    for ref in refs:
        if not isinstance(ref, str) or "." not in ref:
            continue
        up = ref.split(".", 1)[0]
        if up not in status:          # 上游还没轮到跑（Gate 有祖先校验，这里只防呆）
            continue
        if status.get(up) == "ok":
            continue
        if ref_value(results, ref) is None:
            missing.append("%s（上游 status=%s）" % (ref, status.get(up)))
    if missing:
        return ("ctx.from 的上游产物缺失：" + "、".join(missing)
                + " —— 不许自己发挥顶替，落 need_info 等上游重跑")
    return None


def fail_lines(rec, checks, cmd_results, rst):
    """把一次失败的 attempt 压成几行人能读的原因 —— 直接喂进**下一次**的 prompt（F11）。"""
    out = []
    if rec.get("timed_out"):
        out.append("超时（只拿到 %d 字符输出，轨迹可能不完整）" % (rec.get("stdout_chars") or 0))
    elif rec.get("exit_code") not in (0, None):
        out.append("退出码 %s（子进程非正常结束）" % rec.get("exit_code"))
    if rec.get("error") and not rec.get("timed_out"):
        out.append("错误：%s" % rec.get("error"))
    for key in CHECK_KEYS:
        if (checks or {}).get(key):
            out.append("%s：%s" % (key, "、".join(str(x) for x in checks[key])))
    for c in (cmd_results or []):
        if not c.get("ok"):
            tail = (c.get("stderr_tail") or c.get("stdout_tail") or "").strip().splitlines()
            head = tail[-1][:200] if tail else ""
            out.append("验收命令失败：`%s` → exit=%s%s"
                       % (c.get("run"), c.get("exit_code"), ("（" + head + "）") if head else ""))
    if rec.get("result") is None and not rec.get("timed_out"):
        out.append("没有给出可解析的 <RESULT> 块（块外的话机器读不到你做了什么）")
    if (rst or "") not in ("", None, "ok", "pass", "done"):
        out.append("自报 status=%s" % rst)
    return out or ["未通过判定（原因未归类）"]


# ---------------------------------------------------------------- 快照（R1/R3 的事实来源）

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root):
    """工程根全量快照（哈希）+ `runs/` 的**轻量指纹**（只 stat）。

    全量：本单元首跑实测 153 文件 / ≈1.43 MB（见 `runs/g3-live/FS-BEFORE.json`），成本可忽略。
    规模会随工程增长漂 —— 别把这个数当承诺。

    EVIDENCE-GUARD C3（2026-11-10）：`runs/**` 只取 `mtime_ns`/`size`，**不算 sha256**。
    不这么做的代价：`runs/_harness` 已有 900+ 个文件，每次 attempt 要快照 2～3 次，
    全量哈希会把真跑拖慢一个量级；而这一区需要回答的问题只有「**有没有人动过**」，
    内容级篡改由 `EVIDENCE-MANIFEST.json`（C4）负责。
    """
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        segs = rel.split("/")
        if any(seg in EXCLUDE_DIRS for seg in segs):
            # 只有工程根的 `runs/` 要轻量纳入（员工可写、且装着证据）；其余排除目录照旧完全跳过。
            if segs[0] != RUNS_DIR:
                continue
            st = p.stat()
            out[rel] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": None, "light": True}
            continue
        st = p.stat()
        out[rel] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size,
                    "sha256": sha256_file(p), "light": False}
    return out


# 员工子进程的临时区（C5）：放在证据包内、且**显式豁免** `evidence_write` ——
# 写临时文件是正常行为，写证据不是。
_SANDBOX_SEG = "SANDBOX"
# 证据包里**不算「碰证据」**的辅助区（都在 `runs/<run_id>/` 下）：
#   SANDBOX       —— 员工临时区（C5），单独记进 `sandbox_writes`
#   FAKE-COUNTERS —— fixture 替身数「被调了几次」，runner 授意它写（重试测试靠它）
#   OVERLAYS      —— 升档时 runner **在 attempt 内**现写的 model overlay（`write_model_overlay`）
# 后两个是 runner 自己在窗口里写的；不豁免的话每一次升档都会被判成「碰证据」。
_runner_aux_segs = (_SANDBOX_SEG, "FAKE-COUNTERS", "OVERLAYS")


def diff_snapshot(before, after, run_id=None):
    """两份快照之差。

    EVIDENCE-GUARD C2（原 F14）：`modified` **只看 sha256**。原先还 `or mtime_ns`，
    于是一次「内容字节相同、只是重新落盘」的幂等重写会被算成改动 —— E2E 里 `integ` a2
    重写了一份 sha 相同的 `INDEX.md`，它自己没报（自报为空），于是被判 `underclaim`。
    那份情况现在落进 `touched`：sha 相同、mtime/size 变了，**单独记录、不进 `changed_paths`**。

    EVIDENCE-GUARD C3（原 F13）：`runs/` 下的变化从 `changed_paths` 里摘出来，单独进：
      `runs_changes`    —— `runs/` 下的一切变化
      `evidence_changes`—— 其中**不是**本 run `SANDBOX/` 的部分 ⇒ `evidence_write`（harm）
      `sandbox_writes`  —— 本 run `SANDBOX/` 下的部分（员工临时文件，合法，只记录）
    """
    added_raw = sorted(set(after) - set(before))
    removed_raw = sorted(set(before) - set(after))
    modified_raw, touched_raw = [], []
    for k in sorted(set(before) & set(after)):
        b, a = before[k], after[k]
        if b.get("sha256") != a.get("sha256"):
            modified_raw.append(k)
        elif b.get("mtime_ns") != a.get("mtime_ns") or b.get("size") != a.get("size"):
            touched_raw.append(k)

    def _is_runs(p):
        return p.split("/")[0] == RUNS_DIR

    runs_changes = sorted(p for p in (added_raw + removed_raw + modified_raw + touched_raw) if _is_runs(p))
    # ⚠️ `run_id` 允许带子目录（harness 就用 `_harness/honest`），所以这里**不能按段数硬编**：
    # 第一版写成 `parts[1] == run_id` 时，`runs/_harness/honest/SANDBOX/x` 的 `parts[1]` 是
    # `_harness`、不等于 `_harness/honest` —— 豁免全部失效，所有 fixture 场景当场判「碰证据」。
    rid = str(run_id).replace("\\", "/").strip("/") if run_id else None
    run_prefix = ("%s/%s/" % (RUNS_DIR, rid)) if rid else None

    def _rel_in_run(p):
        return p[len(run_prefix):] if run_prefix and p.startswith(run_prefix) else None

    sandbox_prefix = (run_prefix + _SANDBOX_SEG + "/") if run_prefix else None
    sandbox_writes = sorted(p for p in runs_changes if sandbox_prefix and p.startswith(sandbox_prefix))

    def _is_aux(p):
        """是不是本 run 辅助区（SANDBOX / FAKE-COUNTERS / OVERLAYS）里的东西。"""
        rel = _rel_in_run(p)
        return bool(rel) and rel.split("/")[0] in _runner_aux_segs

    evidence_changes = sorted(p for p in runs_changes if not _is_aux(p))
    return {
        "added": [p for p in added_raw if not _is_runs(p)],
        "removed": [p for p in removed_raw if not _is_runs(p)],
        "modified": [p for p in modified_raw if not _is_runs(p)],
        "touched": [p for p in touched_raw if not _is_runs(p)],
        "changed_paths": sorted(p for p in (added_raw + removed_raw + modified_raw) if not _is_runs(p)),
        "runs_changes": runs_changes,
        "evidence_changes": evidence_changes,
        "sandbox_writes": sandbox_writes,
    }


# ---------------------------------------------------------------- 证据包登记（C4 / F13-b）

class EvidenceManifest:
    """证据包登记册：每写一个证据文件就记下它的 sha256，每次起跑前复核一遍。

    为什么需要它（C4）：C3 只能抓「起跑窗口**内**」`runs/` 的变化。员工若**先改、再把 mtime
    复原**（`Set-ItemProperty LastWriteTime`），窗口内的轻量指纹就看不出来 ——
    `mtime_ns` 是可以被随手改写的，这一点在 `ledger-audit` 的已知盲区里已经记过一次。
    把已登记文件的内容钉死在历史里，这种绕法就失效了。

    它**不**解决「员工能读证据」（DSH 沙箱没有目录级 ACL，读不可拦）—— 那条只能靠「写有检测」，
    见 `DELIVERY.md` 的「弱在哪」。

    **已判定为篡改的条目不再重复报**（2026-11-10 第 1 轮验收官 B2）：第一版没有这层隔离，于是
    一次篡改会被**之后每个节点**在起跑前复核时再次命中、记成它们各自的 `evidence_tampered` ——
    不可洗的 harm 栽给完全无辜的节点（验收官用 `x_indep` 复现：它一动没动却报
    `evidence_tampered: GRAPH.json`）。现在只报**新发现**的那一次，并把它们落盘到
    `EVIDENCE-TAMPERED.json` 留证。
    """

    def __init__(self, out_dir, name="EVIDENCE-MANIFEST.json"):
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / name
        self.bad_path = self.out_dir / "EVIDENCE-TAMPERED.json"
        self.entries = {}
        self.bad_log = {}
        if self.path.is_file():
            try:
                self.entries = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.entries = {}
        if self.bad_path.is_file():
            try:
                self.bad_log = json.loads(self.bad_path.read_text(encoding="utf-8"))
            except Exception:
                self.bad_log = {}

    def record(self, p):
        """登记一个刚写完的证据文件（相对 `out_dir`）。返回相对路径。"""
        p = Path(p)
        rel = p.relative_to(self.out_dir).as_posix()
        self.entries[rel] = {"sha256": sha256_file(p), "size": p.stat().st_size,
                             "at": round(time.time(), 3)}
        self.path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8")
        return rel

    def verify(self):
        """复核已登记文件，**只返回这次新发现的**篡改（空列表 = 干净）。

        处理方式（2026-11-10 第 2 轮验收官 N1 改法）：发现失配后**把登记哈希更新成当前内容**，
        而不是把这个文件放进永久跳过的黑名单。两条理由：
          - 不更新就只能「永远跳过」，于是**同一个文件第二次被篡改就再也抓不到**
            （验收官第 2 轮实测：第二次篡改后节点 ok、checks 全空、只剩一条旧记录 ——
            等于「改了证据且没有留痕」，比不修还糟）；
          - 更新之后，第一层目标（别让一个坏节点连累后面所有人）仍然成立：每次失配**只报一次**，
            只有真正在这个窗口里动过它的那次会被记上。
        失配记录追加到 `EVIDENCE-TAMPERED.json`，作为「谁在什么时候动过」的审计痕迹。
        """
        fresh = []
        for rel, meta in sorted(self.entries.items()):
            p = self.out_dir / rel
            if not p.is_file():
                fresh.append({"path": rel, "why": "missing", "registered": meta.get("sha256")})
                # 缺失的没法「更新成当前内容」，从登记表里摘掉 —— 否则每个后续节点都要再报一遍
                self.entries.pop(rel, None)
                continue
            now = sha256_file(p)
            if now != meta.get("sha256"):
                fresh.append({"path": rel, "why": "sha256_mismatch",
                              "registered": meta.get("sha256"), "now": now})
                self.entries[rel] = {"sha256": now, "size": p.stat().st_size,
                                     "at": round(time.time(), 3)}
        if fresh:
            self.path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            for b in fresh:
                self.bad_log.setdefault(b["path"], []).append(
                    {"why": b.get("why"), "at": round(time.time(), 3),
                     "registered": b.get("registered"), "now": b.get("now")})
            self.bad_path.write_text(json.dumps(self.bad_log, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        return fresh


def norm_path(p):
    """路径归一：只做分隔符与前缀。**刻意不做**大小写折叠或 `a/../b` 归约 ——
    多做的每一步都是「自报 vs 实际」之间的一道和稀泥。"""
    s = str(p).strip().strip('"').strip("'")
    s = s.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


# ---------------------------------------------------------------- 员工临时区（C5 外置 · N1）

# N1（2026-11-10）：C5 原先把 TMP/TEMP/TMPDIR 指到 `runs/<id>/SANDBOX/TEMP` —— **在 workspace 里**。
# 2026-11-10 真跑证明这条路走不通：dsh 的 Windows ACL 沙箱硬拒 workspace 内的 temp root
# （`containsDirectory(workspaceRoot, tempRoot)`，见 `dsh-sandbox-windows-acl/lib/types-Cl_DXjhk.js:650-662`），
# `workspace-write` 档 3 个节点 17 次 pwsh 里 12 次撞这条，`integ` 因此跑不了重生成脚本。
#
# 现在的口径（订阅通道 GPT 研判的 D 方案，2026-11-10）：
#   * 临时区在 **workspace 外**的受管根下，**每次 attempt 一个子目录**（升档/重试不复用）；
#   * 目录用**真实路径**做双向不重叠校验（`_real` + `_overlaps`）—— 字符串前缀校验会被 `..` 和
#     junction 绕过去，而 dsh 那边用的就是 realpath；
#   * 窗口关闭后由 runner **串行**采集：清单（path/size/sha256）+ **必要原件选择性归档**
#     （小文件原样归档进证据包，大的只记清单并标 `archived:false` + 原因）；
#   * **不自动删除**：清理由人工执行（`RUN.json.sandbox.cleanup = "manual"`），
#     自动删是不可逆操作，本单元不引入。
SANDBOX_ARCHIVE_MAX_BYTES = 64 * 1024      # 单文件归档上限；超过只留清单
SANDBOX_ARCHIVE_MAX_FILES = 200         # 一次 attempt 最多归档多少个原件
SANDBOX_SCAN_MAX_FILES = 2000          # 清单最多扫描多少个文件（防病态目录拖死）


def _real(p):
    """真实路径（解析 junction / symlink）。dsh 的 ACL 判据用的就是 realpath，校验必须同口径。"""
    try:
        return Path(os.path.realpath(str(p)))
    except OSError:
        return Path(os.path.abspath(str(p)))


def _overlaps(a, b):
    """`a` 与 `b` 是否同一目录或互为祖先。**双向**判 —— 只判一个方向会放过「temp 是 workspace 的父目录」。"""
    ra, rb = _real(a), _real(b)
    return ra == rb or ra in rb.parents or rb in ra.parents


def pick_sandbox_root(requested, run_id, workspace=None):
    """决定本次 run 的外部临时区根。返回 (Path, 说明串)；与 workspace 重叠时 `die(2)`。

    `requested` 来自 `--sandbox-root`；默认 `%LOCALAPPDATA%\\ai-team-sandbox`。
    ⚠️ 为什么默认不放在 `%TEMP%`：workspace 常常**就在** `%TEMP%` 下（一次性副本就是这么跑的），
    放 `%TEMP%\\ai-team-sandbox` 虽然与 workspace 是兄弟、能过 dsh 的判据，但 `%TEMP%` 顶层每 run
    多一项会把 C6 的逃逸指纹变成噪音。`%LOCALAPPDATA%` 两者都不占。
    """
    ws = Path(workspace) if workspace else ROOT
    rid = str(run_id).replace("/", "_").replace("\\", "_").strip() or "run"
    if ".." in rid:
        die(2, "run_id 里不许有 `..`：" + rid)
    if requested:
        base = Path(str(requested)).expanduser()
        if not base.is_absolute():
            base = (Path.cwd() / base)
        why = "--sandbox-root 指定"
    else:
        la = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = Path(la) / "ai-team-sandbox"
        why = "默认 %LOCALAPPDATA%"
    cand = _real(base) / rid
    if _overlaps(cand, ws):
        die(2, "临时区不能与 workspace 重叠（dsh 的 ACL 沙箱会拒收）：root=%s workspace=%s"
               % (cand, _real(ws)))
    # 唯一化：同 run_id 重跑时**不覆盖、也不删**旧目录，改用 `<name>-2`、`-3`…。
    # 为什么不用 rmtree：自动删是不可逆操作；而「复用旧目录」会让**上一轮留下的临时文件**
    # 混进这一轮的清单 —— 2026-11-10 变异台就是靠这个把「TEMP 不重定向」那格放过的
    # （新目录里本来就有上一轮写的同名探针，断言看着是绿的）。
    if cand.exists():
        for i in range(2, 102):
            alt = cand.with_name(cand.name + "-%d" % i)
            if not alt.exists():
                cand = alt
                why += "；同名目录已存在，改用 %s" % cand.name
                break
        else:
            die(2, "sandbox root 的 %s-2..-101 都已被占用，先手工清理：%s" % (cand.name, base))
    return cand, why


def sandbox_attempt_dir(root, nid, attempt):
    """一次 attempt 独占的临时目录。**升档也换目录** —— 复用会把两次 attempt 的临时文件搅在一起。"""
    safe = str(nid).replace("/", "_").replace("\\", "_")
    return Path(root) / safe / ("a%d" % int(attempt or 1))


def collect_sandbox(adir, archive_dir, max_bytes=SANDBOX_ARCHIVE_MAX_BYTES,
                    max_files=SANDBOX_ARCHIVE_MAX_FILES, scan_max=SANDBOX_SCAN_MAX_FILES):
    """采集一次 attempt 的外部临时区：清单 + 选择性归档。返回可直接进 `RUN.json` 的摘要。

    ⚠️ **调用时机**：必须在**该 attempt 的快照窗口关闭之后**（`node_diff` 已经算完）。归档会往
    `runs/<id>/SANDBOX/ARCHIVE/` 写文件，那是本 run 的辅助区（`_runner_aux_segs` 豁免），
    所以在窗口外写才不会被算成「碰证据」。

    ⚠️ **它证明不了「完整临时文件历史」**：员工在两次扫描之间创建又删除的文件抓不到。这条写进交付说明，
    不许把它说成「全量收编」。
    """
    adir = Path(adir)
    files, archived, skipped = [], [], []
    total = 0
    if not adir.is_dir():
        return {"dir": str(adir), "exists": False, "files": files, "archived": archived,
                "skipped": skipped, "total_files": 0, "total_bytes": 0, "incomplete": False}
    truncated = 0
    for p in sorted(adir.rglob("*")):
        if not p.is_file():
            continue
        total += 1
        if total > scan_max:
            truncated += 1
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        rel = p.relative_to(adir).as_posix()
        item = {"path": rel, "size": st.st_size, "sha256": None, "archived": False, "why_not": None}
        try:
            item["sha256"] = sha256_file(p)
        except OSError:
            item["why_not"] = "unreadable"
        if item["why_not"] is None:
            if st.st_size > max_bytes:
                item["why_not"] = "too_large(>%d)" % max_bytes
            elif len(archived) >= max_files:
                item["why_not"] = "archive_file_limit(%d)" % max_files
            else:
                dst = Path(archive_dir) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(p, dst)
                    item["archived"] = True
                    archived.append(rel)
                except OSError as e:
                    item["why_not"] = "copy_failed:%s" % e.__class__.__name__
        if item["why_not"]:
            skipped.append({"path": rel, "why": item["why_not"]})
        files.append(item)
    return {
        "dir": str(adir), "exists": True, "files": files, "archived": archived,
        "skipped": skipped, "total_files": len(files),
        "total_bytes": sum(f["size"] for f in files),
        "incomplete": bool(truncated),
        "truncated_files": truncated,
    }


# ---------------------------------------------------------------- 逃逸面顶层指纹（C6 / F12-b）

def escape_fingerprint(skip=None):
    """员工能逃出副本的两个地方的**顶层**指纹。

    只取顶层，不递归：真实 `%TEMP%` 下几百个条目、别的进程一直在写，递归指纹既贵又吵。
    顶层**新增项**足以抓住 E2E 里实测到的两类逃逸 —— `$USERPROFILE\\_dsh_probe.ps1`（integ a1）
    与 `%TEMP%\\mkpatch-wdocs.py`（w_docs），两个都是**新文件**。

    `HOME` / `USERPROFILE` **不重定向**（C6 的设计取舍）：headless 员工要读 `~/.dsh` 下的
    配置与凭据，把 USERPROFILE 改到沙箱里会让它自己起不来。所以这一面只能「看见」，不能「拦住」。

    N1（2026-11-10）追加 `LOCALAPPDATA`：临时区外置到 `%LOCALAPPDATA%\\ai-team-sandbox\\` 之后，
    原先只看 `USERPROFILE`/`TEMP` 的两项**看不见它** —— 那是修法自己开出来的新盲区。补上看顶层，
    并把 runner 自己那一项（`ai-team-sandbox`）用 `skip` 排掉，否则每次 run 都会给自己记一条「逃逸」。
    """
    out = {}
    skip = set(skip or ())
    for name in ("USERPROFILE", "TEMP", "LOCALAPPDATA"):
        base = os.environ.get(name)
        if not base:
            continue
        items = {}
        try:
            for p in Path(base).iterdir():
                if (name, p.name) in skip:
                    continue
                try:
                    items[p.name] = p.stat().st_mtime_ns
                except OSError:
                    continue
        except OSError:
            continue
        out[name] = items
    return out


def escape_skip_for(sandbox_dir):
    """`escape_fingerprint(skip=…)` 的入参：排掉 runner 自己在 `%LOCALAPPDATA%` 顶层建的那一项。"""
    if not sandbox_dir:
        return ()
    la = os.environ.get("LOCALAPPDATA")
    if not la:
        return ()
    try:
        rel = Path(sandbox_dir).resolve().relative_to(Path(la).resolve())
    except (ValueError, OSError):
        return ()
    if len(rel.parts) < 2:
        return ()
    return (("LOCALAPPDATA", rel.parts[0]),)


def diff_fingerprint(before, after):
    """只报**新增**的顶层项。改已有文件不算（目录 mtime 不跟着变，抓不到也误报少）。"""
    out = []
    for name, items in (after or {}).items():
        prev = (before or {}).get(name) or {}
        for fn in sorted(set(items) - set(prev)):
            out.append({"where": name, "path": fn, "why": "added"})
    return out


# ---------------------------------------------------------------- 事件流解析

def parse_events(stdout):
    """把 headless `--json` 的 NDJSON 拆成 (tool_calls, tool_results, final_text, unparsed)。

    对格式保持宽松，但**不丢**：认不出的行原样计数。
    理由：格式变了也要能看出「解析器没咬住」，而不是安静地得到 0 条工具调用、
    让人误以为员工什么都没干。
    """
    calls, results, final_text, unparsed = [], [], "", 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            unparsed += 1
            continue
        t = ev.get("type")
        if t == "tool_call":
            calls.append(ev)
        elif t == "tool_result":
            results.append(ev)
        elif t == "final":
            final_text = ev.get("text") or final_text
        else:
            unparsed += 1
    return calls, results, final_text, unparsed


def parse_events_codex(stdout):
    """把 `codex exec --json` 的 NDJSON 拆成与 headless **同构**的三件 + 两类附加信息。

    返回 `(calls, results, final_text, unparsed, extra)`，其中 `calls` / `results` / `final_text`
    的键集合与 `parse_events()`（headless）**完全一致** —— 下游的 `compare()`、证据脚本、
    `ARTIFACTS/*.txt` 都按那个形状读，形状一变它们会静默读到 0（这正是本函数要防的事）。

    形状（`tasks/CODEX-2026-11-10/run.jsonl` 真实样本）：
      `{"type":"thread.started","thread_id":"..."}`
      `{"type":"item.started","item":{"id","type":"command_execution","command","aggregated_output","exit_code","status"}}`
      `{"type":"item.completed","item":{...同上...,"status":"completed"}}`
      `{"type":"item.completed","item":{"id","type":"agent_message","text":"..."}}`
      `{"type":"turn.completed","usage":{...}}`

    两处**故意**与 headless 不同：
      1. 一次工具调用在 codex 侧是 `item.started` + `item.completed` 两个事件，**只在 completed 时配对产出**
         call+result 各一条（started 只计 `items_started`），否则每次调用会被数成两次。
         中断时（有 started 没 completed）仍补一条 call、result 标 `no_result` —— **不许静默丢**。
      2. `item.type=="file_change"` 不进 `tool_calls`：它的字段形状本机**一条真数据都没见过**（E0），
         硬塞进同构形状等于编。原样收进 `extra["file_changes"]`，等真样本回来再定。
    """
    calls, results, final_text, unparsed = [], [], "", 0
    extra = {"thread_id": None, "usage": None, "file_changes": [],
             "other_events": [], "items_started": 0}
    pending = {}   # item id -> item.started 的原件（等 completed 配对）
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            unparsed += 1
            continue
        if not isinstance(ev, dict):
            unparsed += 1
            continue
        t = ev.get("type")
        if t == "thread.started":
            extra["thread_id"] = ev.get("thread_id") or extra["thread_id"]
        elif t in ("item.started", "item.completed"):
            it = ev.get("item") or {}
            it_type = it.get("type")
            iid = it.get("id") or "item_?"
            if it_type == "command_execution":
                if t == "item.started":
                    extra["items_started"] += 1
                    pending[iid] = it
                else:
                    pending.pop(iid, None)
                    calls.append({"type": "tool_call", "callId": iid, "tool": "shell",
                                  "input": {"command": it.get("command")}})
                    results.append({"type": "tool_result", "callId": iid,
                                    "status": it.get("status") or "unknown",
                                    "result": it.get("aggregated_output") or "",
                                    "exit_code": it.get("exit_code")})
            elif it_type == "agent_message":
                if t == "item.completed":
                    # 取**最后一个** agent_message：它可能先输出一段解释再给答案（真实样本就有两条）。
                    final_text = it.get("text") or final_text
            elif it_type == "file_change":
                extra["file_changes"].append(it if t == "item.completed" else {"_started": it})
            else:
                # 未知 item 类型：不算解析失败（事件被认出来了），但原样留档。
                extra["other_events"].append(ev)
        elif t == "turn.started":
            # 纯生命周期事件，不产内容。**显式认它**：真样本（`tasks/CODEX-2026-11-10/run.jsonl`）
            # 第 2 行就是它，不认它会让 `unparsed` 常态 >=1 —— 那个计数器一旦恒 >0 就失去意义，
            # 真正没咬住的事件会被噪音盖掉（F3 那次「超时与空转分不清」是同一个病）。
            extra["turns_started"] = extra.get("turns_started", 0) + 1
        elif t == "turn.completed":
            extra["usage"] = ev.get("usage") or extra["usage"]
        else:
            # 认不出的事件类型（如将来的 turn.failed）：计 unparsed 但**留原文**。
            # `unparsed>0` 是「解析器没咬住」的信号，不是「员工没干活」。
            unparsed += 1
            extra["other_events"].append(ev)
    for iid, it in pending.items():
        calls.append({"type": "tool_call", "callId": iid, "tool": "shell",
                      "input": {"command": it.get("command")}})
        results.append({"type": "tool_result", "callId": iid, "status": "no_result",
                        "result": it.get("aggregated_output") or "", "exit_code": None})
    return calls, results, final_text, unparsed, extra


def parse_stdout(engine, stdout):
    """按引擎挑解析器，统一返回 `(calls, results, final_text, unparsed, extra)`。

    包一层是为了让 `run_node` 里「成功」与「超时」两条路径**调同一个入口**——
    上次 F3 的病根就是那两条路径各写了一份，超时那份把 stdout 整个丢了。
    """
    if engine == "codex":
        return parse_events_codex(stdout)
    c, r, f, u = parse_events(stdout)
    return c, r, f, u, None


def extract_result(text):
    """从员工最终回答里取**最后一个**可解析的 <RESULT> 块。取不到 → None。"""
    if not text:
        return None
    for blk in reversed(RESULT_RE.findall(text)):
        try:
            val = json.loads(blk)
        except Exception:
            continue
        if isinstance(val, dict):
            return val
    return None


# ---------------------------------------------------------------- prompt / allowlist

def ref_value(results, ref):
    if not isinstance(ref, str) or "." not in ref:
        return None
    nid, field = ref.split(".", 1)
    return (results.get(nid) or {}).get(field)


def allow_set(node, results):
    """该节点被允许改的文件：静态 `writes` ∪ 各 scope 的 `allow_from` 引用到的数组。"""
    s = {norm_path(p) for p in (node.get("writes") or [])}
    for acc in (node.get("accept") or []):
        if acc.get("type") != "scope":
            continue
        for ref in (acc.get("allow_from") or []):
            got = ref_value(results, ref)
            if isinstance(got, list):
                s |= {norm_path(x) for x in got if isinstance(x, (str, int))}
    return s


def build_prompt(node, results, prev=None):
    nid = node.get("id")
    kind = node.get("kind")
    readonly = kind in READONLY_KINDS
    L = []
    L.append("你是 DAG 图里的一个执行节点。只做下面这一件事，不要顺手做别的。")
    L.append("")
    L.append("## 节点")
    L.append("id: %s     kind: %s（%s）" % (nid, kind, "只读，禁止写任何文件" if readonly else "可写，但只许改允许清单内的文件"))
    L.append("")
    L.append("## 要做的事")
    L.append(str(node.get("do") or "").strip())
    L.append("")
    if prev:
        # F11（2026-11-09）：重试/升档的 prompt 原先与首次**逐字相同**，上一次为什么失败一个字都不传，
        # 于是同因再败（E2E 的 `integ` 三次全挂在同一个执行策略错误上，白烧 25 分钟）。
        # 现在把上一轮的失败原因直接摆在最前面 —— 它比任何「请仔细检查」都管用。
        L.append("## 上一次尝试为什么失败（第 %s 次，档位 %s）" % (prev.get("attempt"), prev.get("tier")))
        for line in (prev.get("lines") or []):
            L.append("- " + str(line))
        L.append("")
        L.append("**针对上面这几条改**：同样的做法不要再来一次。缺输入就报 `need_info` 并列 `missing`，不要猜、不要自己另写一份顶替。")
        L.append("")
    ctx = node.get("ctx") or {}
    if ctx.get("read"):
        L.append("## 可以先读这些文件（路径相对工程根）")
        for r in ctx["read"]:
            L.append("- " + str(r))
        L.append("")
    if ctx.get("from"):
        L.append("## 上游产物（原样摘录，可能被截断）")
        for ref in ctx["from"]:
            v = ref_value(results, ref)
            if v is None:
                L.append("### %s\n（上游没有给这个字段）" % ref)
            else:
                txt = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                if len(txt) > 3000:
                    txt = txt[:3000] + "\n…（截断）"
                L.append("### %s\n%s" % (ref, txt))
        L.append("")
    outs = node.get("out") or []
    L.append("## 必须产出的字段")
    L.append(", ".join(str(o) for o in outs) if outs else "（图里没写 out，至少给 summary）")
    L.append("")
    if PATCH_FIELD in [str(o) for o in outs]:
        L.append("### 字段语义（按 DESIGN v1「并行读算、单点写入」）")
        L.append("- `%s`：你要交给下游的 diff 文本。**本节点不许碰主树** —— 落盘会被判 `fs_write_forbidden`。" % PATCH_FIELD)
        L.append("- `patch_targets`：这个 patch 会改到的文件（相对工程根，数组），必须落在下面的允许清单内。")
        L.append("- `changed`：**你本次实际落盘**改了哪些文件。只交 patch 的节点这里必须是 `[]`。")
        L.append("- 把 patch 目标填进 `changed` 会被判 `overclaim`：那是**下游落地之后**才会发生的变化，不是你改的。")
        L.append("")
    if node.get("accept"):
        L.append("## 验收规则（交之前自己逐条核一遍）")
        for a in node["accept"]:
            t = a.get("type")
            if t == "cmd":
                L.append("- [cmd] 会由外部**真的执行**：`%s`（期望 %s）" % (a.get("run"), a.get("expect", "exit0")))
            elif t == "scope":
                L.append("- [scope] 你改动的文件必须落在允许清单内：%s" % (a.get("rule") or ""))
            else:
                L.append("- [%s] %s" % (t, a.get("rule") or ""))
        L.append("")
    allow = allow_set(node, results)
    if allow:
        L.append("## 允许改动的文件（机器按这份清单核对，rule 文字不参与判定）")
        for p in sorted(allow):
            L.append("- " + p)
        L.append("")
    elif not readonly:
        L.append("## 允许改动的文件：**空** —— 这个节点不该写任何文件。")
        L.append("")
    L.append("## 输出格式（硬要求）")
    L.append("最后必须输出一个 <RESULT> 块，块里是**合法 JSON 对象**，字段名与上面「必须产出的字段」完全一致：")
    L.append("<RESULT>")
    example = {}
    for o in (outs or ["summary"]):
        example[str(o)] = ["<路径>"] if str(o) in LIST_FIELDS else "<字符串>"
    L.append(json.dumps(example, ensure_ascii=False))
    L.append("</RESULT>")
    L.append("不要加注释、不要尾逗号；<RESULT> 之外可以有简短说明，但机器只认块里的 JSON。")
    return "\n".join(L)


# ---------------------------------------------------------------- 员工执行

def escalate_ignored_reason(engine, tier):
    """请求的档位在这个引擎上有没有模型映射。返回 `None`（有）或原因串（没有）。

    为什么单拎出来：`on_fail.escalate` 曾被**静默忽略**过一次（A2-min 之前，不拒收也不升级），
    那次判了 B 级。现在 codex 引擎**能换模型了**（`-m CODEX_WORKER_MODEL`），但档位表 `TIERS`
    仍然是 DeepSeek 的、不映射到 codex —— 「请求升档」在 codex 下依然不换模型，所以必须把
    这件事记成事实，而不是让它无声发生。台子直接咬这个函数。
    """
    if engine != "codex":
        return None
    if TIERS[tier]["model"] == TIERS["work"]["model"]:
        return None
    return ("codex 引擎没有档位映射：请求的 %s（%s）不适用；本 attempt 用 %s"
            % (tier, TIERS[tier]["model"], CODEX_WORKER_MODEL))


def escalate_log(engine, cur_tier, next_tier):
    """「升档」那一行日志的措辞。

    codex 引擎的档位表不映射到 codex（见 `escalate_ignored_reason`），所以**不许**在这里印
    `TIERS` 的模型名 —— 那会让屏幕上的日志看起来像「真升到了 deepseek-flash」。控制台与证据要一致。
    2026-11-12（CLAUDE-TO-GPT）：codex 员工的模型已是确定的 `CODEX_WORKER_MODEL`，文案直接点它的名。

    ⚠️ 2026-11-12（V4PRO-REMOVE）：上面那条约束原来**靠 why 非空来保证**（codex 下必有原因串）。
    全档位统一 flash 之后 `escalate_ignored_reason` 恒返回 None，于是这条约束失去了屏障 ——
    codex 下会印出「升 integrate 档（deepseek-flash）」，而实际跑的是 codex 默认模型。
    这正是 `CODEX-ENGINE-2026-11-11` 第 1 轮 B2 点过的那类谎。所以现在**显式按引擎分岔**，
    不再依赖 `why` 的副作用。
    """
    why = escalate_ignored_reason(engine, next_tier)
    if why:
        return "%s 档没成 → 请求升 %s 档，但%s" % (cur_tier, next_tier, why)
    if engine == "codex":
        return "%s 档没成 → 升 %s 档（codex 引擎：档位不换模型，仍用 %s）" % (cur_tier, next_tier, CODEX_WORKER_MODEL)
    return "%s 档没成 → 升 %s 档（%s）" % (cur_tier, next_tier, TIERS[next_tier]["model"])


def proxy_url_for(egress):
    """从 egress 自检结果算出代理 URL。没有合法 port → die(4)。

    端口来自 `check_codex_egress()` 的 `-Json` 输出，**不写死**：端口真值只在
    `claude-egress-check.ps1` 一处（2026-11-08 那次 1081 -> <PROXY-PORT-TUNNEL> 就是没单点才漂的）。
    """
    port = (egress or {}).get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        die(4, "codex 引擎没有可用的 egress 端口（实际 %r）—— 不起跑。"
               "出口不对会在账号侧留风控痕迹，这条不许降级成警告。" % (port,))
    return "http://127.0.0.1:%d" % port


def write_model_overlay(tier, node, overlay_dir):
    """写一个只声明 `agent-default-model` 的 overlay，返回 `(path, text)`。

    **为什么需要它**：`dsh --profile headless` 没有 `--model` 参数（2026-11-07 实测，
    `dsh --profile headless --help` 只有 `--json` / `--session-id` / task）。换模型的唯一
    入口是 launcher 的 `--patch <yml>` overlay。

    ⚠️ `--patch` 必须排在 `--profile` **之前**：放后面会被当成 app 参数报
    `unknown option '--patch'`。这不是风格问题 —— 放错了子进程直接起不来。

    overlay 真的会走到请求层，有反证（2026-11-07 实测，当时 `strong` 档还是 v4-pro）：
    把 model 改成不存在的名字，`dsh` 回
    `INVALID_REQUEST: The supported API model names are deepseek-flash, deepseek-v4-pro,
    but you passed ...`。⚠️ 2026-11-12 起全档位都是 flash，**这个条件分支不再触发**
    （见 `run_attempt()` 的「模型 ≠ work 档」判据），overlay 机制保留给以后复活 `strong` 用。
    """
    t = TIERS[tier]
    text = (
        "# runner.py 生成：给 on_fail.escalate 换档用（派生物，别手改）\n"
        "- id: %s\n  config:\n    provider: %s\n    model: %s\n"
        % (DEFAULT_MODEL_ID, t["provider"], t["model"])
    )
    p = Path(overlay_dir) / ("overlay-%s-%s.yml" % (node.get("id"), tier))
    p.write_text(text, encoding="utf-8")
    return p, text


def worker_argv(engine, dsh_bin, node, fixture_dir, tier, overlay, codex_bin=None):
    """`overlay` 是 `(path, text)` 或 None。默认档（flash）不带 `--patch`。

    `codex_bin`：只给断言台覆盖用，生产路径走 `find_codex_bin()`。

    ⚠️ 这里**没有** `readonly` 参数了（2026-11-11 第 1 轮验收官 B2 点名的那处反话）：曾经有过一个
    「把只读写进 argv 沙箱档」的设计，但实测 `-s read-only` 在 Windows 上禁一切命令，
    所以 codex 分支对只读与写节点使用相同权限参数，只读执法改由快照核承担。
    2026-11-13 起岗位 effort 独立：scout=medium，其余角色=high（不改变权限）。
    """
    patch_args = ["--patch", str(overlay[0])] if overlay else []
    if engine == "headless":
        # ⚠️ 顺序：--patch 在 --profile 之前（见 write_model_overlay 的说明）
        return [find_node(), str(dsh_bin)] + patch_args + ["--profile", "headless", "--json"]
    if engine == "fixture":
        # 一个场景一个 spec 文件，按 `nodes.<id>` 分节 —— 比「每节点一个文件」好读好审。
        # `--patch` 也塞进去：假 dsh 会忽略它，但**argv 形状与真 headless 一致**，
        # 于是「升档有没有把 --patch 带上」在零成本场景里就咬得住（真跑再验一次它真生效）。
        return [sys.executable, str(FAKE_WORKER)] + patch_args + [
            "--spec", str(Path(fixture_dir) / "spec.json"),
            "--node", str(node.get("id"))]
    if engine == "codex":
        # 三处与 headless 不同，都在 TASK K2 里点了名：
        #   1. `-` 结尾 = prompt 走 stdin。**含空格的 prompt 不能用 argv 传**（会被拆成多个参数，
        #      `tasks/CODEX-2026-11-10/VERIFY.md` 第一节就是这么失败的）。
        #   2. **`--approve-for-me` 对只读和写节点都给**。这不是「放弃了只读」——
        #      Windows 上 `-s read-only` 实测是「禁止执行任何命令」（连读文件都被 blocked by policy，
        #      见 `PROBE-READONLY.txt` 三组对照），给了它 scout / check 就一个字节都读不到。
        #      所以只读约束改由**事后快照核**执法，强度降级如实记在 `readonly_enforcement`。
        #   3. `-s read-only` **一个字都不要加回来** —— 加回来等于让只读节点彻底不能干活，
        #      台子里有一条断言专门咬这个（"codex argv：只读节点不许带 -s read-only"）。
        #   `--patch` 不适用：overlay 是 dsh launcher 的东西，codex 换模型走 `-m`（本单元不换，见 TASK 二·2）。
        cb = codex_bin or find_codex_bin()
        return ([find_node(), str(cb), "exec", "--json", "--skip-git-repo-check",
                 "-m", CODEX_WORKER_MODEL,
                 "-c", "model_reasoning_effort=%s" % CODEX_ROLE_EFFORT[node["kind"]],
                 "-C", str(ROOT), "--approve-for-me", "-"])
    die(2, "未知 engine：" + str(engine))


def run_node(node, engine, dsh_bin, fixture_dir, results, timeout, tier="work", overlay_dir=None,
             attempt=1, counter_dir=None, prev=None, sandbox_dir=None, run_id=None,
             egress=None, codex_bin=None):
    """起一个子进程扮演员工。**不在这里判 ok** —— 判定在 compare()。

    `prev`（F11）：上一次失败的原因摘要；非空时 prompt 里会多一节「上一次尝试为什么失败」。
    `sandbox_dir`（C5 / N1）：员工子进程的 `TMP`/`TEMP`/`TMPDIR` 都指到这里 —— **workspace 外**的
    受管目录（`sandbox_attempt_dir()` 建的，一次 attempt 一个）。⚠️ 不能指到 workspace 里：dsh 的
    Windows ACL 沙箱会拒收（`containsDirectory(workspaceRoot, tempRoot)`），2026-11-10 真跑撞过 12 次。
    **不改** `HOME`/`USERPROFILE`（那会让你自己找不到 `~/.dsh`）。
    `run_id`：写进 `DSH_RUN_ID`，让 fixture 替身能定位证据包（C3/C4 那两个场景要用）。
    `egress`（2026-11-11，仅 codex）：`check_codex_egress()` 的返回值，**端口从它取**（不写死 <PROXY-PORT-TUNNEL>）。
    `codex_bin`：只给断言台覆盖用。
    """
    nid = str(node.get("id"))
    kind = node.get("kind")
    readonly = kind in READONLY_KINDS
    prompt = build_prompt(node, results, prev)
    env = dict(os.environ)
    # K1（TASK §三）：只读节点的硬保证在**沙箱层**，不在 prompt 里。
    # ⚠️ 只对 `headless` / `fixture` 成立（2026-11-11 第 2 轮验收官 N3）：codex 子进程不读这个变量，
    # 它的只读执法是快照核（`READONLY_ENFORCEMENT`）。这里照样设，是为了让 `permission_mode`
    # 这个字段记录「**请求**的模式」——它与 `readonly_enforcement` 一起读才不会误会。
    env["DSH_PERMISSION_MODE"] = "read-only" if readonly else "workspace-write"
    if run_id:
        env["DSH_RUN_ID"] = str(run_id)
    # fixture 替身自己数「被调用了几次」（重试测试要靠它），计数文件不能落在快照范围里：
    # 落进 `runs/`（快照排除区）而不是 spec 目录 —— 否则只读节点的 node_diff 里会多出这个文件，
    # 被判成 `readonly_violation`（第一次实现就栽在这里，harness 当场红了 9 条）。
    if engine == "fixture" and counter_dir:
        env["DSH_FAKE_COUNTER_DIR"] = str(counter_dir)

    # C5 / N1（2026-11-10）：把临时区指到 workspace 外的受管目录。三个变量都要改 —— PowerShell 读
    # `$env:TEMP`、Python 的 `tempfile` 认 `TMPDIR`/`TEMP`，只改一个等于没改。
    #
    # ⚠️ **目录必须在这里建**（第 1 轮验收官 B1）：dsh 的 Windows ACL runner 要求 `--temp` 是**已存在**
    # 的目录（不存在 → 子进程 exit 127 / `realpathSync` ENOENT），而 Python 的 `tempfile` 在 TEMP 不存在时
    # 会**静默回退**到真实 `%TEMP%` —— 两个后果都很危险：前者让真跑全挂，后者让员工临时文件又逃出去、
    # 而清单看起来只是「空」。这条 fixture 测不出来（假 worker 原先自己 `mkdir`），现在替身也改成
    # 「TEMP 不存在就 exit 127」，与真 dsh 同款。
    sandbox_existed = None
    if sandbox_dir:
        sd = Path(sandbox_dir)
        try:
            sd.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(3, "临时区目录建不出来：%s（%s）" % (sd, e))
        # 起跑前的**事实**（不是自报）：子进程真正要用时它是否已存在
        sandbox_existed = sd.is_dir()
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = str(sd)

    # headless 的 credentials 服务被 patch 关掉了，key 只能由这里注入。
    # 只把「来源」写进证据包，**永不写值**。
    key_source = None
    codex_gw_key_injected = False
    if engine == "headless":
        key, key_source = resolve_llm_key()
        if not key:
            die(3, "找不到 LLM key（环境变量与 ~/.dsh/.credentials.yaml 的 refs 都没有）—— headless 员工跑不起来。")
        env["DEEPSEEK_API_KEY"] = key
        # 2026-11-23（DAG-MIXED-MODEL）：`strong` 档要走 `codex-gw` 路由，而 headless **同样**不会把
        # `~/.dsh/.credentials.yaml` 的 refs 注入 provider 解析链（见上一条注释与
        # `headless/cordis.patch.yml` 的「跨通道」段），所以变量必须由这里给。
        # 网关**不校验入站凭据**（`headers.authorization: Bearer gateway` 是写死的），值是占位符；
        # 但它不是"密钥"，不属于「证据包里出现密钥=事故」那条红线的对象 —— 值本身没有任何权限。
        # 用 setdefault 而不是直接赋值：宿主若真给了值，别被占位符盖掉。
        env.setdefault("CODEX_GW_KEY", CODEX_GW_KEY_PLACEHOLDER)
        codex_gw_key_injected = True

    # codex 的流量必须走伪装出口（K6）。端口**从 egress 自检里取**，不写死 —— 端口真值只在
    # `claude-egress-check.ps1` 一处（AGENTS.md 2026-11-08 那次 1081 -> <PROXY-PORT-TUNNEL> 的教训）。
    proxy_url = None
    if engine == "codex":
        proxy_url = proxy_url_for(egress)
        # 四个都设：codex 本体（Rust）读大写，Node 的 fetch/undici 与 curl 都认小写那一对。
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[k] = proxy_url

    # 档位 ≠ 默认档时，写 overlay 让子进程真的用那个模型。
    # **codex 上没有 overlay 这回事**：`--patch` 是 dsh launcher 的参数，codex 换模型走 `-m`。
    # 本单元不换（TASK 二·2），但**必须留痕** —— `escalate` 曾被静默忽略过一次，那次判了 B 级；
    # 所以这里记 `escalate_ignored`，进 RUN.json.nodes[]，不许当没发生过。
    overlay = None
    tier_ignored = escalate_ignored_reason(engine, tier)
    if TIERS[tier]["model"] != TIERS["work"]["model"]:
        if tier_ignored:
            pass          # codex：留痕，不写 overlay（见 escalate_ignored_reason）
        else:
            if not overlay_dir:
                die(3, "升档到 %s 需要 overlay_dir" % tier)
            overlay = write_model_overlay(tier, node, overlay_dir)

    argv = worker_argv(engine, dsh_bin, node, fixture_dir, tier, overlay, codex_bin=codex_bin)
    rec = {
        "node": nid,
        "kind": kind,
        "attempt": attempt,
        "engine": engine,
        "tier": tier,
        # codex 走的是订阅通道，档位表（DeepSeek 的 provider/model）在这里**是假的**：
        # 记成 deepseek-flash 会让证据读起来像「用 flash 跑的」。
        # 2026-11-12（CLAUDE-TO-GPT）：模型已经显式钉住，所以这里写**真模型名**，
        # 不再写「(codex 默认模型)」—— 那句话在写了 `-m` 之后就是谎报。
        "provider": "codex" if engine == "codex" else TIERS[tier]["provider"],
        "model": CODEX_WORKER_MODEL if engine == "codex" else TIERS[tier]["model"],
        "requested_reasoning_effort": CODEX_ROLE_EFFORT[kind] if engine == "codex" else None,
        # 只读约束在这一引擎上靠什么执法（sandbox / snapshot_only），见 READONLY_ENFORCEMENT
        "readonly_enforcement": READONLY_ENFORCEMENT.get(engine, "unknown"),
        "patch_overlay": overlay[1] if overlay else None,
        "permission_mode": env["DSH_PERMISSION_MODE"],
        "llm_key_source": key_source,
        # 2026-11-23（DAG-MIXED-MODEL）：`strong` 档要过 `codex-gw`，而 headless 不注入凭据 refs ——
        # 这条记录「占位 key 有没有真送进子进程 env」。不记值（占位值没有信息量），只记布尔。
        "codex_gw_key_injected": codex_gw_key_injected,
        # codex（2026-11-11）：代理端口是「出口对不对」的唯一可核字段；tier_ignored 是升档的留痕
        "proxy": proxy_url,
        "escalate_ignored": tier_ignored,
        # 起跑前那一刻临时区是否真的存在（第 1 轮验收官 B1 的守护）：None = 本 attempt 没分配临时区
        "sandbox_dir_existed_at_launch": sandbox_existed,
        "prompt": prompt,
        # 这个字段必须**从渲染出来的 prompt 内容推导**，不能写 `bool(prev)`。
        # 2026-11-09 变异台抓到的：写成 `bool(prev)` 时，把 `build_prompt` 里那段整块删掉，
        # 这个字段仍然是 True —— 于是 harness 里「重试 prompt 有」那条断言测的不是那行代码。
        # （同 `LESSONS.md` 第 1 类错：**断言读的是自报，不是事实**。）
        "prompt_has_prev": "## 上一次尝试为什么失败" in prompt,
        "argv": argv,
        "timed_out": False,
    }
    t0 = time.time()
    fp_before = escape_fingerprint(skip=escape_skip_for(sandbox_dir))   # C6：起跑前记下逃逸面的顶层项
    rec.update(stamp(t0))
    try:
        r = subprocess.run(
            argv, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT), env=env,
        )
        rec["exit_code"] = r.returncode
        rec["stderr_tail"] = (r.stderr or "")[-2000:]
        calls, tres, final_text, unparsed, extra = parse_stdout(engine, r.stdout or "")
        rec["tool_calls"] = calls
        rec["tool_results"] = tres
        rec["event_lines_unparsed"] = unparsed
        if extra is not None:
            rec["codex"] = extra          # thread_id / usage / file_changes / other_events
        rec["final_text"] = final_text
        rec["result"] = extract_result(final_text)
        rec["result_found"] = rec["result"] is not None
    except subprocess.TimeoutExpired as e:
        # F3（2026-11-09）：超时也**必须保留已捕获的输出**。原先这里只记 `exit_code=None`，
        # 把 `stdout` 整个丢掉 —— 于是超时 attempt 的 `tool_calls` / `tool_results` / `final_text` /
        # `stderr_tail` 四个键**根本不存在**，「超时」与「空转」在证据上无法区分
        # （E2E 第 1 轮验收官判为 blocking B2：那是记录缺失，不是模型没干活）。
        # `subprocess.run(capture_output=True)` 超时会把**已经读到**的管道内容挂在异常对象上。
        raw_out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        raw_err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
        calls, tres, final_text, unparsed, extra = parse_stdout(engine, raw_out or "")
        res = extract_result(final_text)
        rec.update({
            "exit_code": None, "timeout": True, "timed_out": True, "timeout_s": timeout,
            "error": "超时 %ss（已保留超时前捕获的 %d 字符 stdout）" % (timeout, len(raw_out or "")),
            "stderr_tail": (raw_err or "")[-2000:],
            "tool_calls": calls, "tool_results": tres, "event_lines_unparsed": unparsed,
            "final_text": final_text, "result": res, "result_found": res is not None,
            "stdout_chars": len(raw_out or ""),
        })
        if extra is not None:
            rec["codex"] = extra
    except Exception as e:
        rec.update({"exit_code": None, "error": "起子进程失败：%s" % e})
    t1 = time.time()
    rec["finished_at"] = round(t1, 3)
    rec["finished_iso"] = datetime.fromtimestamp(t1).isoformat(timespec="milliseconds")
    # C6：逃逸面（`$USERPROFILE` / 真实 `%TEMP%`）的顶层新增项。**只记录，不进 harm** ——
    # 别的进程也在写 TEMP，把它当 blocking 会让真图永久失败（TASK「验收要点」第 3 条）。
    rec["escape_writes"] = diff_fingerprint(fp_before, escape_fingerprint(skip=escape_skip_for(sandbox_dir)))
    if rec["escape_writes"]:
        say("  [escape] %s 写到副本外：%s" % (
            nid, ", ".join("%s/%s" % (e["where"], e["path"]) for e in rec["escape_writes"])))
    # `duration_s` 是老字段（A1 起就有人读），`wall_s` 是同值的语义名 —— 两个都留，别让人猜。
    rec["wall_s"] = round(t1 - t0, 2)
    rec["duration_s"] = rec["wall_s"]
    return rec


def run_cmd_accept(node, timeout):
    """R2：`accept.type=cmd` 由 runner 真跑。**模型自报的退出码一律不采信。**

    `expect` 只支持 `exit0`；其它值标 `CMD_EXPECT_UNSUPPORTED` 而不是猜 —— 猜错了没人看得见。
    """
    out = []
    for acc in (node.get("accept") or []):
        if acc.get("type") != "cmd":
            continue
        run = acc.get("run")
        expect = acc.get("expect", "exit0")
        item = {"run": run, "expect": expect}
        if expect != "exit0":
            item.update({"ok": False, "reason": "CMD_EXPECT_UNSUPPORTED", "exit_code": None})
            out.append(item)
            continue
        try:
            t_cmd0 = time.time()
            r = subprocess.run(
                str(run), shell=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT),
            )
            t_cmd1 = time.time()
            item.update({
                "exit_code": r.returncode,
                "ok": r.returncode == 0,
                "stdout_tail": (r.stdout or "")[-1500:],
                "stderr_tail": (r.stderr or "")[-800:],
                # K4：cmd 阶段的时间戳 —— 用来证明它**没有**和 agent 阶段/兄弟 cmd 重叠
                "started_at": round(t_cmd0, 3),
                "finished_at": round(t_cmd1, 3),
                "wall_s": round(t_cmd1 - t_cmd0, 3),
            })
        except subprocess.TimeoutExpired:
            item.update({"exit_code": None, "ok": False, "reason": "TIMEOUT"})
        out.append(item)
    return out


def compare(rec, node_diff, cmd_results, allow, attribution="attempt", node=None):
    """R1 + R2 + R3 的判定。返回 (ok, checks)。

    **自报只当线索，实际变化才是事实。** 这是整个 A1 的核心。
    `node_diff` 只含**本节点**起跑窗口内的变化（串行执行保证了归因）；
    `cmd accept` 造成的变化单独记账（`cmd_effects`），不进这里的判定 ——
    否则 runner 自己跑的命令会把自己的节点判成越界。

    `attribution="wave"`（B1 新增）：波内并发时，窗口内的变化**无法归属**到具体节点。
    此时不判 `overclaim` / `underclaim` / `out_of_scope` —— 那三条都要求「这变化是我的」；
    只把窗口内变化记成 `readonly_violation`，组内**每个**参与者共同承担（K3，连坐是有意的）。

    F1（2026-11-09）新增三条，只在 `out` 声明了 `patch` 的节点上生效：
      `fs_write_forbidden` —— 它落了盘。DESIGN.md:203「员工不碰主树，只返回 diff」是**全称规则**，
                              旧实现只把落盘变化当 `actual`，于是照设计做（只交 patch）的节点被判
                              `overclaim`，违规直接写主树的反而判 ok —— **奖励违规、惩罚守规矩**。
      `patch_missing`      —— 声明了 patch 却交了个空（下游会拿到「上游没有给这个字段」）。
      `patch_out_of_scope` —— patch 目标跑出允许清单（它落地时一样会越界，不能等他落地才判）。
    """
    checks = {k: [] for k in CHECK_KEYS}
    declared = set()
    res = rec.get("result")
    if isinstance(res, dict):
        ch = res.get("changed")
        if isinstance(ch, list):
            declared = {norm_path(x) for x in ch if isinstance(x, (str, int))}
        elif isinstance(ch, str) and ch.strip():
            declared = {norm_path(ch)}
    actual = set(node_diff["changed_paths"])
    outs = [str(o) for o in ((node or {}).get("out") or [])]
    patch_out = PATCH_FIELD in outs

    if attribution == "wave":
        checks["readonly_violation"] = sorted(actual)
    else:
        checks["overclaim"] = sorted(declared - actual)
        checks["underclaim"] = sorted(actual - declared)
        checks["out_of_scope"] = sorted(actual - set(allow))
        if rec.get("permission_mode") == "read-only":
            checks["readonly_violation"] = sorted(actual)

    # EVIDENCE-GUARD C3/C4（原 F13）：两条**与归因无关** —— 它们问的是「有人碰了证据包吗」，
    # 不需要「这个变化是我的」这个前提，所以两条路径都判（同 F1 那三条的教训：写在 else 里就漏一半）。
    checks["evidence_write"] = sorted((node_diff or {}).get("evidence_changes") or [])
    checks["evidence_tampered"] = [b.get("path") for b in (rec.get("evidence_tampered") or []) if b.get("path")]

    # `out` 含 patch 的四条判据。**与归因无关** —— 它们只读 RESULT（自报的 patch / patch_targets）
    # 与 allowlist，不需要「这个变化是我的」这个前提，所以**两条路径都必须跑**。
    # 第 1 轮验收官 B3 抓的就是「这整段原先写在 else 分支里，wave 归因根本走不到」：
    # 同一张图并行 PASS、串行 FAIL，直接违反 C6。
    if patch_out:
        pv = res.get(PATCH_FIELD) if isinstance(res, dict) else None
        patch_ok = isinstance(pv, str) and pv.strip()
        if not patch_ok:
            checks["patch_missing"] = [PATCH_FIELD]
        # patch_targets 与 changed 一样支持字符串归一化 —— 第 1 轮 B1 的洞就是
        # 「只认 list，写成字符串时整条 patch_out_of_scope 被静默跳过」。
        tg_raw = res.get("patch_targets") if isinstance(res, dict) else None
        tg = set()
        if isinstance(tg_raw, list):
            tg = {norm_path(x) for x in tg_raw if isinstance(x, (str, int))}
        elif isinstance(tg_raw, str) and tg_raw.strip():
            tg = {norm_path(tg_raw)}
        if patch_ok and not tg:
            # 交了 patch 却说不出会改到哪 → `patch_out_of_scope` 没有输入，等于一条空判据。
            checks["patch_targets_missing"] = ["patch_targets"]
        outside = sorted(tg - set(allow))
        if outside:
            checks["patch_out_of_scope"] = outside
        # 这一条依赖 `actual`（本节点真落了盘才算），所以**只在 attempt 归因下判**：
        # wave 归因的 `actual` 是整轮窗口，栽给单个节点就是冤枉。
        # 只读 kind / integrate 声明 patch 的情况已由 Gate 的 `PATCH_REQUIRES_WORK` 拒收，
        # 所以并发路径上根本不会出现该判的节点。
        if attribution != "wave" and actual:
            checks["fs_write_forbidden"] = sorted(actual)

    # N2（2026-11-10）：核查节点的裁决参与判定。**只对 kind == "check"**，理由见常量区 `VERDICT_FIELD`。
    #
    # 为什么写在 `compare()` 里、而不是等 `RUN.json` 汇总时再改终态：汇总时才改，下游节点**已经跑完了**
    # （`when` / `ctx.from` 都读 `results`）—— 那是「事后声明失败」，不是「拦住」。判据必须落在
    # 「这个 attempt 判 ok 不 ok」这一层，两条调度路径才会一起吃到它。
    #
    # 三种结果的处置（研判 2026-11-10）：
    #   pass               → 不额外否决（原有的 exit/checks/cmd 一条不少）
    #   fail/rework/escalate → `check_rejected`，调度层硬停（核查节点没有修复职责，重抽模型等于挑答案）
    #   缺字段/空串/未知词  → `check_protocol_error`，同样硬停（fail-safe：读不懂的裁决不放行）
    # 真实坏样本形态见 `runs/e2e-20261110-2005/NODES/k_hash.json` 的 `result`：`{"verdict":"fail","notes":…}`。
    #
    # 生效范围 = `kind == "check"` **且 `out` 里声明了 `verdict`**。后一个条件不是宽容，是分工：
    # 老图里有 check 只出 `summary`（`fixtures/graphs/g-parallel.json` 的 `k_sum`、`g-parallel-write.json`
    # 的 `k_both`）—— 它们不承担裁决职责，按「缺 verdict 就协议失败」判会把它们连同整图一起打红。
    # 这与 F1 的 `out` 含 `patch` 才判 patch 那一组判据**完全同构**：范围由图的声明定，不看模型自报。
    _out_fields = [str(o) for o in ((node or {}).get("out") or [])]
    if (str((node or {}).get("kind") or "") == CHECK_KIND
            and VERDICT_FIELD in _out_fields and res is not None):
        has_v = isinstance(res, dict) and VERDICT_FIELD in res
        raw_v = res.get(VERDICT_FIELD) if isinstance(res, dict) else None
        norm_v = raw_v.strip().lower() if isinstance(raw_v, str) else None
        if not has_v:
            decision, reason = "protocol_error", "missing"
            checks["check_protocol_error"] = ["missing（RESULT 里没有 %s）" % VERDICT_FIELD]
        elif norm_v in VERDICT_PASS:
            decision, reason = "pass", None
        elif norm_v in VERDICT_REJECT:
            decision, reason = "rejected", norm_v
            checks["check_rejected"] = [norm_v]
        else:
            decision, reason = "protocol_error", "bad:%r" % (raw_v,)
            checks["check_protocol_error"] = [reason]
        rec["check_verdict"] = {
            "raw": (raw_v if isinstance(raw_v, (str, int, float, bool)) or raw_v is None
                    else json.dumps(raw_v, ensure_ascii=False)[:200]),
            "normalized": norm_v,
            "decision": decision,
            "reason": reason,
            "attempt": rec.get("attempt"),
        }
    rec["attribution"] = attribution

    ok = True
    if rec.get("exit_code") != 0:
        ok = False
    if res is None:
        ok = False
    if any(checks[k] for k in checks):
        ok = False
    if any(not c.get("ok") for c in cmd_results):
        ok = False
    return ok, checks


def harm_of(checks):
    """把一次 attempt 的判定压成「不可洗的损害」集合。键表在 `HARM_KEYS` —— 加一条判据只改那一处。"""
    out = set()
    for k in HARM_KEYS:
        out |= set((checks or {}).get(k) or [])
    return out


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="DAG 编排 Step 2 · A1：引擎外调度 + 真实文件系统复核")
    ap.add_argument("graph", help="GRAPH.json 路径")
    ap.add_argument("--run-id", default=None, help="运行 id，默认 run-<时间戳>")
    ap.add_argument("--engine", choices=["headless", "fixture", "codex"], default="headless")
    ap.add_argument("--fixture-dir", default=None, help="--engine fixture 时的预设目录")
    ap.add_argument("--timeout", type=int, default=900, help="单节点超时秒数（默认 900）")
    ap.add_argument("--cmd-timeout", type=int, default=600)
    ap.add_argument("--dry-plan", action="store_true", help="只跑 Gate + 打印波次与 prompt，不起员工（零成本）")
    ap.add_argument("--max-parallel", type=int, default=4,
                    help="波内并发上限（默认 4；只对只读节点生效 —— 写节点永远串行）")
    ap.add_argument("--no-parallel", action="store_true",
                    help="关掉波内并发（对照实验用；只改调度，不改任何判定口径）")
    ap.add_argument("--sandbox-root", default=None,
                    help="员工临时区的外部受管根（默认 %%LOCALAPPDATA%%\\ai-team-sandbox）；"
                         "每个 run 在其下建 <run_id>/，必须与 workspace 真实路径互不重叠")
    args = ap.parse_args()

    graph_path = Path(args.graph)
    if not graph_path.is_absolute():
        graph_path = (Path.cwd() / graph_path).resolve()
    if not graph_path.is_file():
        die(2, "图文件不存在：" + str(graph_path))

    say("[runner] graph   " + str(graph_path))
    say("[runner] engine  " + args.engine)

    # 1) Gate —— 不重写判据，只问它
    gate, rc, err = run_gate(graph_path)
    if err:
        die(2, err)
    if gate is None:
        die(2, "Gate 没有返回结果（rc=%s）" % rc)
    say("[runner] gate    %s (%d err / %d warn)" % (
        "OK" if gate.get("ok") else "REJECTED", gate.get("errCount", 0), gate.get("warnCount", 0)))
    if not gate.get("ok"):
        for e in (gate.get("errs") or []):
            say("  [err ] %s  %s" % (e.get("code"), e.get("msg")))
        die(2, "图被 Gate 拒收 —— 不执行。")

    raw = json.loads(graph_path.read_text(encoding="utf-8-sig"))
    bad = plan_check(raw, gate)
    if bad:
        for b in bad:
            say("  [unsupported] " + b)
        die(2, "这张图超出 A2-min 能力边界（明确拒收，不静默降级）")

    waves = gate.get("waves") or []
    nodes = {str(n.get("id")): n for n in raw.get("nodes", [])}
    say("[runner] waves   %d 波：%s" % (len(waves), " | ".join(",".join(w) for w in waves)))

    # codex 的 egress 前置（K5）：**放在建任何目录之前** —— 出口不对就 die(4)，不留半个证据包。
    # 位置也有讲究：Gate 已经被问过了（图不合法不该先烧一次自检），但 plan_check 之后立刻拦，
    # 因为这张图后面每个节点都会起 codex 子进程。`--dry-plan` 例外：它不起员工，就不该被隧道状态挡住
    # （干跑是排障用的，隧道没起时正是最需要能跑的时候）—— 所以 egress 在 dry-plan **之后**。
    egress_info = None

    if args.dry_plan:
        results = {}
        for wi, w in enumerate(waves):
            say("")
            say("== wave %d ==" % wi)
            for nid in w:
                node = nodes[nid]
                of = node.get("on_fail") or {}
                say("--- %s（%s / tier=%s / retry=%s / escalate=%s / then=%s / join=%s）---" % (
                    nid, node.get("kind"), node_tier(node), of.get("retry", 0),
                    of.get("escalate") or "-", of.get("then") or "replan", node.get("join") or "all"))
                if isinstance(node.get("when"), dict):
                    say("    when: " + json.dumps(node["when"], ensure_ascii=False))
                say(build_prompt(node, results))
        say("")
        say("[runner] dry-plan 结束（0 次员工调用）")
        return 0

    if args.engine == "codex":
        egress_info = check_codex_egress()
        say("[runner] proxy   %s" % ("http://127.0.0.1:%s" % egress_info.get("port")))

    run_id = args.run_id or ("run-" + time.strftime("%Y%m%d-%H%M%S"))
    out_dir = ROOT / "runs" / run_id
    if out_dir.exists():
        die(2, "运行目录已存在，不覆盖：" + str(out_dir))
    out_dir.mkdir(parents=True)
    nodes_dir = out_dir / "NODES"
    nodes_dir.mkdir()
    overlays_dir = out_dir / "OVERLAYS"
    overlays_dir.mkdir()
    counters_dir = out_dir / "FAKE-COUNTERS"
    counters_dir.mkdir()
    # N1（2026-11-10）：员工临时区移到 **workspace 外**（C5 原先把 TEMP 指到 `runs/<id>/SANDBOX/TEMP`，
    # 撞上 dsh 的 ACL 沙箱 —— 见 `pick_sandbox_root` 上面那段注释）。证据包这边改为：
    #   `SANDBOX-MANIFEST.json`（清单，全部 attempt 一份）+ `SANDBOX/ARCHIVE/<node>/a<N>/`（必要原件）。
    sandbox_root, sandbox_why = pick_sandbox_root(args.sandbox_root, run_id)
    try:
        sandbox_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        die(2, "临时区根建不出来：%s（%s）" % (sandbox_root, e))
    say("[runner] sandbox %s（%s；workspace 外）" % (sandbox_root, sandbox_why))
    sandbox_records = []            # 每次 attempt 一条（摘要，进 RUN.json）
    sandbox_manifest = []           # 每次 attempt 一条（含逐文件明细，进 SANDBOX-MANIFEST.json）
    sandbox_archive_root = out_dir / _SANDBOX_SEG / "ARCHIVE"
    sandbox_archive_root.mkdir(parents=True, exist_ok=True)
    # C4：证据包登记册。之后所有证据写盘都走 `manifest.record()`。
    manifest = EvidenceManifest(out_dir)

    def write_node(nid, rec):
        p = nodes_dir / (nid + ".json")
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.record(p)   # C4：一写就登记，供之后的 attempt 复核

    def write_out(name, text):
        """证据包顶层文件统一出口（C4）：写 + 登记，别绕过它。"""
        p = out_dir / name
        p.write_text(text, encoding="utf-8")
        manifest.record(p)
        return p

    def write_attempt(nid, rec, n):
        p = nodes_dir / ("%s.a%d.json" % (nid, n))
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.record(p)

    def record_sandbox(nid, attempt, adir):
        """采集一次 attempt 的外部临时区：清单 + 选择性归档 + C4 登记。

        **两处调度（串行 / 波内并发）共用这一份** —— 第 1 轮验收官 B1 的教训是「并发路径另写一份」，
        这次不再给那份教训留机会。
        """
        info = collect_sandbox(
            adir, sandbox_archive_root / str(nid).replace("/", "_") / ("a%d" % attempt))
        sandbox_manifest.append({"node": nid, "attempt": attempt, **info})
        sandbox_records.append({"node": nid, "attempt": attempt,
                                **{k: v for k, v in info.items() if k != "files"},
                                "files": len(info["files"])})
        for f in info["files"]:
            if f["archived"]:
                manifest.record(sandbox_archive_root / str(nid).replace("/", "_")
                                / ("a%d" % attempt) / f["path"])
        return info

    def verify_evidence():
        """C4：起跑前复核已登记的证据。返回 [] = 干净。"""
        bad = manifest.verify()
        if bad:
            say("  [tamper] 证据包已登记文件对不上（%d 项）：%s" % (
                len(bad), ", ".join("%s(%s)" % (b.get("path"), b.get("why")) for b in bad)))
        return bad

    if args.engine == "fixture":
        if not args.fixture_dir:
            die(2, "--engine fixture 必须给 --fixture-dir")
        fd = Path(args.fixture_dir)
        if not fd.is_absolute():
            fd = (Path.cwd() / fd).resolve()
        if not fd.is_dir():
            die(2, "fixture 目录不存在：" + str(fd))
        write_out("FAKE", "engine=fixture；本证据包不能当交付证据。\n")
        dsh_bin = None
    else:
        fd = None
        if args.engine == "codex":
            # codex 的出口自检在上面（`check_codex_egress`）已经硬拦过了，结果进了 RUN.json.egress。
            # 这里不再打印「headless 走 deepseek 路由」那行 —— 2026-11-11 第一次真跑时它就那么打了一句，
            # 与事实相反（codex 恰恰是走订阅通道 + 代理的那个引擎）。
            dsh_bin = None
            say("[runner] egress   codex 走订阅通道，自检结论见上面那行（READY 才会走到这里）")
        else:
            dsh_bin = find_dsh_bin()
            # headless 员工走 DeepSeek 官方路由，**不碰 claude 订阅通道**，所以这里不调 egress。
            # 升 opus（墙 2）才必须先过 `claude-egress-check.ps1`（判法同 manager.py:check_egress：
            # rc==0 且输出含 `[result] READY`）。本文件没有那条分支 —— 见文件头的天花板说明。
            say("[runner] egress   headless 员工走 deepseek 官方路由，不调 claude，跳过自检（升 opus 时才强制）")

    write_out("GRAPH.json", graph_path.read_text(encoding="utf-8-sig"))
    write_out("GATE.json", json.dumps(gate, ensure_ascii=False, indent=2))
    # 这里不敢再写 `serial: True` —— 第 1 轮 N4 指出它与 `RUN.json.serial`（= 这次有没有并发组）
    # 语义冲突，而且 PLAN 是在起跑前写的，本来就不可能知道结果。只声明「调度开关」。
    write_out("PLAN.json", json.dumps({
        "waves": waves,
        "parallel_enabled": not args.no_parallel,
        "max_parallel": max(1, args.max_parallel),
    }, ensure_ascii=False, indent=2))

    results, records, node_ok = {}, {}, {}
    status = {}                 # nid -> ok / fail / skipped / need_info
    skip_why = {}
    replan_needed = []
    budget_denied = []
    started = 0                 # 已起的子进程数 —— 对齐解释器的 `started`（`:952-964`）
    ran = set()                 # 已经为它做过「首发」判定的节点
    budget_cfg = raw.get("budget") or {}
    max_calls = budget_cfg.get("max_calls")
    if isinstance(max_calls, bool) or not isinstance(max_calls, int):
        max_calls = None
    say("[runner] budget  max_calls=%s（首发预留 + 重试只吃余量）" % ("-" if max_calls is None else max_calls))

    def mark_skip(nid, why, as_status="skipped"):
        status[nid] = as_status
        skip_why[nid] = why
        node_ok[nid] = False
        rec = {"node": nid, "kind": (nodes.get(nid) or {}).get("kind"), "status": as_status,
               "why": why, "attempts": []}
        records[nid] = rec
        write_node(nid, rec)
        say("  [%s] %s  %s" % (nid, "need_info" if as_status == "need_info" else "skip", why))

    def gate_status_of(why):
        """闸门原因 → 落哪个状态：缺料是 `need_info`，被别人挡下是 `skipped`。

        为什么要分：`skipped` 是「有人替我做了决定」（上游失败 / when 不成立 / abort / 预算），
        `need_info` 是「我该干，但料没到」—— 后者必须让上游重跑，前者的处置完全不同。
        """
        return "need_info" if str(why).startswith("ctx.from") else "skipped"

    # ------------------------------------------------------------ 波内并发（B1）

    def gate_node(nid, sim_started=None, sim_ran=None):
        """起跑前的硬闸门：abort → join → when → 首发预算。返回 skip 原因（None = 放行）。

        **串行与并发共用这一份。** 2026-11-09 第 1 轮验收官 B1 抓的就是「并发路径另写一份，
        却漏了 join/when」—— 于是「`--no-parallel` 只改调度、不改判定口径」这句话不成立：
        上游失败的下游在并行下照样起跑，还判 ok。顺序与措辞在这里冻结，两条路径都只认它。

        `sim_started` / `sim_ran` 只给并发组的「轮前预演」用：同一轮一次批准多个节点时，
        串行其实是**一个一个**起跑的（每起一个 pending 减 1、started 加 1），
        不按波内顺序逐个累加，预算就会多批（第 1 轮 B2）。
        """
        node = nodes[nid]
        started_v = started if sim_started is None else sim_started
        ran_v = ran if sim_ran is None else sim_ran
        # 0) abort
        if aborted:
            return "on_fail.then=abort 已触发，后续节点不再起跑"
        # 1) join：上游没达标 → skipped（A1 没有这一段，属于静默偏差）
        why = deps_block_reason(node, status, nodes)
        if why:
            return why
        # 2) when：确定性分支，只许对上游结构化字段做 eq / in / exists
        if isinstance(node.get("when"), dict):
            val, why_un = when_value(node["when"], results, status)
            if why_un:
                return why_un
            if not eval_when(node["when"], val):
                w = node["when"]
                return "when 不成立：%s %s %s，实际 %s" % (
                    w.get("ref"), w.get("op"), json.dumps(w.get("val"), ensure_ascii=False),
                    json.dumps(val, ensure_ascii=False))
        # 3) `ctx.from` 的硬依赖（F10）：引用的上游没 ok 且那个字段确实没值 → 不许起跑。
        #    放在首发预算之前 —— 它压根不该占一个名额。
        why = from_block_reason(node, status, results)
        if why:
            return why
        # 4) 首发名额（对齐 `:952-957`）
        if max_calls is not None:
            pending = len(nodes) - len(ran_v)
            if started_v + pending > max_calls:
                budget_denied.append({"node": nid, "kind": "first", "started": started_v,
                                      "pending": pending, "max_calls": max_calls})
                return ("budget.max_calls 不够给每个节点留首发名额（已用 %d，还要给 %d 个未起跑节点留，上限 %d）"
                        % (started_v, pending, max_calls))
        return None

    def group_parallel_safe(group_nids):
        """这一段只读节点**能不能真并发**。返回 (bool, 不能的原因)。

        ⚠️ **必须在「这一段起跑前」调用**（用那一刻的 `started`/`ran`）。第 3 轮 B5 抓的就是
        「在波开头把本波所有段一次性判完」：同一波有 4 个以上只读节点时会被切成多段，
        第一段用掉的重试名额不会从第二段的余量里扣掉 → 第二段静默分歧。

        第 2 轮验收官 B4 抓到的两类图，会让「并行与 `--no-parallel` 结果完全一致」不成立 ——
        这不是实现 bug，是**轮内并发本身**的性质，所以只能在起跑前把这类组退化成串行：

        1. 组里有 `on_fail.then=abort`：并行时同轮兄弟已经起跑（还判了 ok），串行时它们会被 skipped，
           `agent_calls` 也不同。代码里「已在跑的不取消」只能保证不杀进程，保证不了结果一致。
        2. 组内**重试**总需求超过预算余量：串行会把重试名额**按节点一个一个用完**，
           并行每轮给每个节点各一次 —— 余量的分配顺序不同，被拒的节点和最终状态都会变。

        第 3 轮 N9：`need` 只算**重试**（`max_attempts - 1`），不算首发 —— 首发已经由 `pending`
        预留过一次了，再算一遍会让降级过于保守（本可安全并发的组被退回串行），只损失并行收益、不影响一致性。

        **由此得到的推论（第 3 轮 B6 要求，第 4 轮验收官复核）：并发路径的「重试被拒」分支不可达。**
        记 `R = Σ(max_attempts-1)`、落位时 `slack = max_calls - started0 - pending0`。不降级 ⟹ `R ≤ slack`。
        并发轮里每次**首发**让 `started+1`、`pending-1`（和不变），只有**重试**让和 +1。
        判定第 j 次重试时最多已批准 `r ≤ R-1` 次重试，于是
        `sim_started + pending_now = started0 + pending0 + r ≤ max_calls - R + (R-1) < max_calls`，
        `>= max_calls` 永远不成立。`R = 0`（全员无重试、无升档）时那条分支根本不会执行。
        —— 所以「轮内预算不累加」那条变异被撤是成立的（`runner-mutate.py` 里有同一份推导）。
        """
        members = [nodes[n] for n in group_nids]
        for n in members:
            if (n.get("on_fail") or {}).get("then") == "abort":
                return False, "组内有 on_fail.then=abort：并行会让同轮兄弟在 abort 之前起跑"
        if max_calls is not None:
            slack = max_calls - started - (len(nodes) - len(ran))
            need = sum(max_attempts(n) - 1 for n in members)
            if need > slack:
                return False, ("组内重试总需求 %d 超过预算余量 %d：重试名额的分配顺序会随调度变化"
                               % (need, slack))
        return True, None

    def finish_parallel(nid, s, node_status, why_fail):
        """并发组的收尾：状态 / `on_fail.then` / 证据落盘。

        ⚠️ 与串行路径的收尾（本文件 §5「收尾：状态 + on_fail.then」）**同源**，
        改一处必须两处一起改。两份实现是**有意**的：串行的收尾耦合在它自己的 attempt 循环里，
        抽成共用函数反而要动那段已经过了 170 条断言的代码。
        """
        nonlocal aborted
        node = nodes[nid]
        of = node.get("on_fail") or {}
        then = of.get("then") or "replan"
        if node_status != "ok" and then == "skip":
            node_status = "skipped"
            skip_why[nid] = "on_fail.then=skip：" + (why_fail or "")
        elif node_status != "ok" and then == "abort":
            aborted = True
            say("        then=abort → 后续节点不再起跑（已在跑的不取消）")
        elif node_status != "ok" and then == "replan":
            replan_needed.append(nid)

        rec_out = dict(s["final"] or {"node": nid, "kind": node.get("kind")})
        rec_out.update({
            "status": node_status,
            "ok": node_status == "ok",
            "attempts": s["tick"],
            "attempt_count": len(s["tick"]),
            "final_tier": (s["final"] or {}).get("tier"),
            "harm": sorted(s["harm"]),
            "why": why_fail,
            "deterministic_fail": bool((s["final"] or {}).get("deterministic_fail")),
            # K9：codex 下请求了升档但引擎没有档位映射 —— 留痕（其它引擎恒为 None）
            "escalate_ignored": (s["final"] or {}).get("escalate_ignored"),
        })
        records[nid] = rec_out
        node_ok[nid] = node_status == "ok"
        status[nid] = node_status
        if node_status == "ok" and isinstance(rec_out.get("result"), dict):
            results[nid] = rec_out["result"]
        write_node(nid, rec_out)
        if node_status != "ok":
            say("  [%s] → %s%s" % (nid, node_status,
                                   ("（then=%s）" % then) if node_status != "need_info" else ""))

    def why_fail_of(final, checks, rst, prefix=None):
        """把失败原因拼成人能读的一行。对齐串行路径的那段 `；`.join 逻辑。"""
        if prefix:
            return prefix
        parts = []
        for key in CHECK_KEYS:
            if (checks or {}).get(key):
                parts.append("%s=%s" % (key, ",".join(checks[key])))
        if (final or {}).get("exit_code") not in (0, None):
            parts.append("exit_code=%s" % final.get("exit_code"))
        if final is not None and final.get("timed_out"):
            parts.append("超时")
        if final is not None and not final.get("result_found"):
            parts.append("没有 RESULT 块")
        if (rst or "") not in ("", None, "ok", "pass", "done"):
            parts.append("self_status=%s" % rst)
        return "；".join(parts) or "未通过判定"

    def run_parallel_unit(unit_nids, wave_i):
        """波内只读并发：**轮内并发、轮间栅栏**。

        每一轮 = 组内所有还没停的节点各起一次 agent（并发）→ 全组一次快照 diff →
        按波内顺序逐个跑 cmd 并判定。于是：
          * 每一轮都有干净的窗口（脏的东西只能落在**这一轮**里）；
          * retry / escalate / harm / budget 的语义与串行路径一致；
          * 唯一不同的是**归因粒度是轮（波）而不是 attempt**（`attribution="wave"`）。
        返回这一组的摘要（进 `RUN.json.parallel.groups`）。
        """
        nonlocal started
        gid = "w%d-%s" % (wave_i, unit_nids[0])
        say("")
        say("[parallel] wave %d：%d 个只读节点并发 → %s" % (wave_i, len(unit_nids), ", ".join(unit_nids)))
        stt = {}
        for nid in unit_nids:
            node = nodes[nid]
            retry_n = int((node.get("on_fail") or {}).get("retry") or 0)
            stt[nid] = {"ti": 0, "k": 0, "tries": 1 + retry_n, "attempt_total": 0,
                        "harm": set(), "final": None, "tick": [], "stop": None, "prev": None}
        group = {"wave": wave_i, "group": gid, "nodes": list(unit_nids),
                 "rounds": [], "wall_s": None, "serial_estimate_s": None,
                 "overlap_s": None, "demoted": []}
        active = list(unit_nids)

        while active:
            # --- 轮前：与串行**同一套闸门**（abort → join → when → 首发预算），按波内顺序逐个预演 ---
            # 为什么逐个预演：串行是**一个一个**起跑的（每起一个 pending 减 1、started 加 1）。
            # 一次批准整组而不累加，预算就会多批（第 1 轮 B2：两个 retry 同轮判定都读旧值，上限被突破）。
            launch = []
            sim_started = started
            sim_ran = set(ran)
            for nid in list(active):
                s = stt[nid]
                node = nodes[nid]
                if s["attempt_total"] == 0:
                    why_g = gate_node(nid, sim_started, sim_ran)
                    if why_g:
                        mark_skip(nid, why_g, gate_status_of(why_g))
                        active.remove(nid)
                        continue
                else:
                    if aborted:
                        finish_parallel(nid, s, "fail", "on_fail.then=abort 已触发（已在跑的收尸）")
                        active.remove(nid)
                        continue
                    if max_calls is not None:
                        pending = len(nodes) - len(sim_ran)
                        if sim_started + pending >= max_calls:
                            budget_denied.append({"node": nid, "kind": "retry", "started": sim_started,
                                                  "pending": pending, "max_calls": max_calls})
                            finish_parallel(nid, s, "fail",
                                            "预算余量不足，重试/升档被拒（已起 %d / 上限 %s）"
                                            % (sim_started, max_calls))
                            active.remove(nid)
                            continue
                tiers = tiers_for(node)
                if s["ti"] >= len(tiers):
                    finish_parallel(nid, s, "fail", "没有可用档位")
                    active.remove(nid)
                    continue
                sim_started += 1
                sim_ran.add(nid)
                launch.append((nid, tiers[s["ti"]]))
            if not launch:
                break
            started = sim_started

            # --- 轮内：并发起跑 ---
            # 轮前**不**复核证据（同串行：那一格的归因必错，见串行路径那段注释）
            s0 = snapshot(ROOT)
            t_r0 = time.time()
            got = {}
            with ThreadPoolExecutor(max_workers=len(launch)) as ex:
                futs = {}
                for nid, tier in launch:
                    futs[ex.submit(
                        run_node, nodes[nid], args.engine, dsh_bin, fd, results, args.timeout,
                        tier=tier, overlay_dir=overlays_dir,
                        attempt=stt[nid]["attempt_total"] + 1, counter_dir=counters_dir,
                        prev=stt[nid].get("prev"),
                        # N1：每次 attempt 独占一个外部临时目录（升档/重试都换目录）
                        sandbox_dir=sandbox_attempt_dir(sandbox_root, nid, stt[nid]["attempt_total"] + 1),
                        run_id=run_id, egress=egress_info,
                    )] = nid
                for fut in as_completed(futs):
                    nid = futs[fut]
                    try:
                        got[nid] = fut.result()
                    except SystemExit as e:
                        # 子进程起不来（缺 key / 缺 dsh）是环境问题，不是节点失败 —— 与串行一致地 die
                        die(int(e.code or 3), "并发起跑失败（%s）" % nid)
                    except Exception as e:
                        # 线程里抛的异常绝不能让证据包静默缺项
                        got[nid] = {"node": nid, "exit_code": None, "result": None,
                                    "result_found": False, "tool_calls": [], "tool_results": [],
                                    "error": "起子进程失败：%s" % e, "duration_s": 0.0, "wall_s": 0.0}
            s1 = snapshot(ROOT)
            wave_diff = diff_snapshot(s0, s1, run_id=run_id)
            # C4：轮后核一遍（与串行同一份判据、同一时机理由）
            tamper_round = verify_evidence()
            for nid, _ in launch:
                got[nid]["evidence_tampered"] = tamper_round
            t_r1 = time.time()
            spans = {nid: (got[nid].get("started_at"), got[nid].get("finished_at")) for nid, _ in launch}
            round_rec = {
                "round": len(group["rounds"]) + 1,
                "nodes": [nid for nid, _ in launch],
                "wall_s": round(t_r1 - t_r0, 3),
                "overlap_s": overlap_seconds(list(spans.values())),
                "changed_paths": wave_diff["changed_paths"],
                "spans": {nid: {"started_at": a, "finished_at": b} for nid, (a, b) in spans.items()},
            }
            group["rounds"].append(round_rec)

            # --- 轮后：按波内顺序逐个 cmd + 判定（cmd 串行，K4） ---
            for nid, tier in launch:
                s = stt[nid]
                rec = got[nid]
                s["attempt_total"] += 1
                # `started` 已在轮前用 sim 值算好（含本轮所有批准的节点），这里不再自增 ——
                # 否则同一轮会重复计数（第 1 轮 B2 的另一半）。
                ran.add(nid)
                # cmd 的前后快照：串行路径有 `cmd_effects`，并发路径原先漏了（第 1 轮 B3）。
                # cmd 是串行跑的，所以每次的 before 都包含上一个 cmd 的效果，窗口仍然干净。
                s_cmd0 = snapshot(ROOT)
                if rec.get("timed_out"):
                    # F9(a)：超时后不跑 cmd（与串行路径同一条判据）
                    cmd_results = []
                    s_cmd1 = s_cmd0
                    rec["cmd_skipped"] = "timeout"
                else:
                    cmd_results = run_cmd_accept(nodes[nid], args.cmd_timeout)
                    s_cmd1 = snapshot(ROOT)
                cmd_effects = diff_snapshot(s_cmd0, s_cmd1, run_id=run_id)
                allow = allow_set(nodes[nid], results)
                # N1：临时区采集（窗口在轮末已关闭，此刻归档不会落进任何 attempt 的窗口）
                adir = sandbox_attempt_dir(sandbox_root, nid, s["attempt_total"])
                rec["sandbox"] = record_sandbox(nid, s["attempt_total"], adir)
                ok, checks = compare(rec, wave_diff, cmd_results, allow, attribution="wave",
                                     node=nodes[nid])
                # F5：并行组的窗口是**轮**不是 attempt（`attribution="wave"`），所以「零落盘」只能按
                # 整轮算。保守方向是对的：只有整轮都没变化才判确定性失败，否则可能把别人的变化
                # 算进自己头上（那会冤枉一个其实写成功了的节点）。
                deterministic = ((not ok) and any(not c.get("ok") for c in cmd_results)
                                 and not wave_diff["changed_paths"])
                rst = None
                if isinstance(rec.get("result"), dict):
                    raw_s = str(rec["result"].get("status") or "").strip().lower()
                    rst = raw_s.replace("-", "_").replace(" ", "_") or None
                if rst == "need_info":
                    ok = False
                elif rst is not None and rst not in ("ok", "pass", "done"):
                    ok = False
                rec.update({
                    "checks": checks, "cmd_accept": cmd_results, "allow": sorted(allow),
                    "node_diff": wave_diff, "cmd_effects": cmd_effects, "self_status": rst, "ok": ok,
                    "sandbox_writes": wave_diff.get("sandbox_writes") or [],
                    "deterministic_fail": deterministic,
                    "parallel_group": gid,
                    "parallel_with": [x for x, _ in launch if x != nid],
                })
                s["harm"] |= harm_of(checks)
                s["tick"].append({
                    "attempt": s["attempt_total"], "tier": tier, "model": TIERS[tier]["model"],
                    "exit_code": rec.get("exit_code"), "ok": ok, "self_status": rst,
                    "checks": checks, "node_diff": wave_diff,
                    "cmd_accept": [{"run": c.get("run"), "exit_code": c.get("exit_code"), "ok": c.get("ok"),
                                    "started_at": c.get("started_at"), "finished_at": c.get("finished_at")}
                                   for c in cmd_results],
                    "duration_s": rec.get("duration_s"), "wall_s": rec.get("wall_s"),
                    "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
                    "timed_out": rec.get("timed_out"), "deterministic_fail": deterministic,
                    "cmd_skipped": rec.get("cmd_skipped"),
                    "parallel_group": gid, "attribution": "wave",
                })
                if not ok:
                    # F11：并行路径也要把失败原因留给下一轮（与串行同一条规则）
                    s["prev"] = {"attempt": s["attempt_total"], "tier": tier,
                                 "lines": fail_lines(rec, checks, cmd_results, rst)}
                s["final"] = rec
                write_node(nid, rec)
                write_attempt(nid, rec, s["attempt_total"])

                say("  [%s] %s  attempt=%d  tier=%s(%s)  exit=%s  %.1fs  tool_calls=%d  result=%s" % (
                    nid, "ok  " if ok else "FAIL", s["attempt_total"], tier, rec.get("model"),
                    rec.get("exit_code"), rec.get("duration_s") or 0,
                    len(rec.get("tool_calls") or []), "yes" if rec.get("result_found") else "NO"))
                if not ok:
                    for key in CHECK_KEYS:
                        if checks.get(key):
                            say("        %s: %s" % (key, ", ".join(checks[key])))
                    for c in cmd_results:
                        if not c.get("ok"):
                            say("        cmd failed (exit=%s): %s" % (c.get("exit_code"), c.get("run")))
                    if rst:
                        say("        self_status: %s" % rst)

                # --- 停止条件（顺序与串行路径一致：ok → harm → need_info → 同档重试 → 升档）---
                tiers = tiers_for(nodes[nid])
                harm_now = s["harm"]
                if ok:
                    finish_parallel(nid, s, "ok", None)
                    active.remove(nid)
                elif harm_now:
                    finish_parallel(nid, s, "fail",
                                    "越界/只读违规/写主树/碰证据（**不可用重试洗掉**）：" + ", ".join(sorted(harm_now)))
                    active.remove(nid)
                elif checks.get("check_rejected") or checks.get("check_protocol_error"):
                    # N2（2026-11-10）· 并发路径的同一条硬停。**两条路径必须都有** ——
                    # 第 1 轮验收官 B1 抓的就是「并发路径另写一份、漏了闸门」。
                    finish_parallel(nid, s, "fail",
                                    "核查节点未通过（不重试、不升档）："
                                    + why_fail_of(rec, checks, rst))
                    active.remove(nid)
                elif deterministic:
                    finish_parallel(nid, s, "fail",
                                    "cmd 确定性失败（非零退出且本轮无任何落盘变化，重试/升档救不回来）："
                                    + why_fail_of(rec, checks, rst))
                    active.remove(nid)
                elif rst == "need_info":
                    if s["ti"] + 1 < len(tiers):
                        say("        need_info → 跳过同档重试，直接升 %s 档" % tiers[s["ti"] + 1])
                        s["ti"] += 1
                        s["k"] = 0
                        s["tries"] = 1
                    else:
                        finish_parallel(nid, s, "need_info", None)
                        active.remove(nid)
                elif s["k"] + 1 < s["tries"]:
                    say("        %s 档第 %d 次失败，同档重试" % (tier, s["k"] + 1))
                    s["k"] += 1
                elif s["ti"] + 1 < len(tiers):
                    say("        " + escalate_log(args.engine, tier, tiers[s["ti"] + 1]))
                    s["ti"] += 1
                    s["k"] = 0
                    s["tries"] = 1
                else:
                    finish_parallel(nid, s, "fail", why_fail_of(rec, checks, rst))
                    active.remove(nid)

        all_spans = [(nid, r["spans"][nid]["started_at"], r["spans"][nid]["finished_at"])
                     for r in group["rounds"] for nid in r["nodes"]]
        starts = [a for _, a, _ in all_spans if a is not None]
        ends = [b for _, _, b in all_spans if b is not None]
        walls = [b - a for _, a, b in all_spans if a is not None and b is not None]
        group["started_at"] = round(min(starts), 3) if starts else None
        group["finished_at"] = round(max(ends), 3) if ends else None
        group["wall_s"] = round(max(ends) - min(starts), 3) if starts and ends else None
        group["serial_estimate_s"] = round(sum(walls), 3)
        # 组级重叠**只统计「本轮 ≥2 个节点」的轮**，取最大值。
        # 跨轮把单节点轮也算进去会算出负数（s1 重试那轮只有一个节点）——
        # 2026-11-09 第 1 轮自查在 budget 场景里就看到了 `最大重叠 -1.001s`。
        multi = [r["overlap_s"] for r in group["rounds"] if len(r["nodes"]) >= 2]
        group["overlap_s"] = round(max(multi), 3) if multi else 0.0
        say("[parallel] %s 结束：整组 %ss / 串行估算 %ss / 最大重叠 %ss" % (
            gid, group["wall_s"], group["serial_estimate_s"], group["overlap_s"]))
        return group

    fs_initial = snapshot(ROOT)
    write_out("FS-BEFORE.json", json.dumps(fs_initial, ensure_ascii=False, indent=1))
    say("[runner] snapshot 起跑前 %d 个文件" % len(fs_initial))

    t_start = time.time()
    # N3：起跑时刻必须在**起跑那一刻**取。原先 `RUN.json.started_at` 是收尾时 `strftime()` 出来的，
    # 与 `duration_s` 自相矛盾（B4：started_at=20:29 而 1830s 说明 19:58 起跑）。
    run_started_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t_start))
    aborted = False
    parallel_groups = []
    parallel_demoted = []
    for wi, wave in enumerate(waves):
        say("")
        say("== wave %d ==  (%s)" % (wi, ", ".join(wave)))
        # 只读 kind 但带写权限 → 静默降级？不行，记下来（`RUN.json.parallel.demoted`）。
        for nid in wave:
            why_d = demoted_reason(nodes[nid], args.engine)
            if why_d:
                parallel_demoted.append({"node": nid, "wave": wi, "reason": why_d})
        # 波内切段：**连续的可并发节点**成一段。段长 > 1 才真并发，段长 1 走原来的串行路径。
        # ⚠️ 第 3 轮 B5：**每段在起跑前才判安全**（用当时的 started/ran），不安全就当场拆成
        # 单元素段插回队列头。原来是在这一行把本波所有段一次性判完 —— 同一波第二段就不知道
        # 第一段真实吃掉了多少重试名额（8 节点 × retry=2 的图会静默分歧）。
        queue = [list(u) for u in
                 split_wave(wave, nodes, 1 if args.no_parallel else max(1, args.max_parallel),
                            args.engine)]
        while queue:
            unit = queue.pop(0)
            if len(unit) > 1:
                safe, why_not = group_parallel_safe(unit)
                if not safe:
                    for nid in unit:
                        parallel_demoted.append({"node": nid, "wave": wi, "reason": why_not})
                    queue[0:0] = [[nid] for nid in unit]   # 拆成单元素段，保持波内顺序
                    continue
                parallel_groups.append(run_parallel_unit(unit, wi))
                continue
            nid = unit[0]
            node = nodes[nid]

            # 0)–3) 起跑前的硬闸门（abort → join → when → 首发预算）。**与并发路径共用一份** ——
            # 第 1 轮验收官 B1 抓到的正是「两条路径各写一份，并发那份漏了 join/when」。
            why_g = gate_node(nid)
            if why_g:
                mark_skip(nid, why_g, gate_status_of(why_g))
                continue

            tiers = tiers_for(node)
            retry_n = int((node.get("on_fail") or {}).get("retry") or 0)

            # 4) attempts：同档 1+retry 次 → 升档各 1 次
            harm = set()        # 跨 attempt 累积的**不可洗**伤害（越界 / 只读违规）
            tick = []
            final = None
            stop = None         # ok / harm / deterministic / need_info / budget
            prev_fail = None    # F11：上一次失败的原因摘要，喂进下一次的 prompt
            attempt_total = 0
            for ti, tier in enumerate(tiers):
                if stop:
                    break
                tries = (1 + retry_n) if ti == 0 else 1
                for k in range(tries):
                    if stop:
                        break
                    # 重试/升档只能吃余量，不许挤掉别人的首发（对齐 `:958-963`）
                    if attempt_total > 0 and max_calls is not None:
                        pending = len(nodes) - len(ran)
                        if started + pending >= max_calls:
                            budget_denied.append({"node": nid, "kind": "retry", "started": started,
                                                  "pending": pending, "max_calls": max_calls})
                            stop = "budget"
                            break
                    attempt_total += 1
                    started += 1
                    ran.add(nid)

                    # ⚠️ 这里**不**做起跑前复核（2026-11-10 第 2 轮验收官 N2）：那一格抓的是
                    # 「上一个节点结束后、这一格起跑前」的改动 —— 而那个窗口里 **runner 自己在写证据**
                    # （`write_node` / `write_attempt`），所以它分不清「员工改的」与「runner 写的」。
                    # 结果是：要么永远为空（实测：删掉它全量断言台仍 378/0），要么把上一个节点留下的
                    # 改动栽给**这个毫无关系的节点**（与第 1 轮 B2 同一个形状）。
                    # 复核只保留**窗口后**那一处 —— 那是唯一能干净归因的时刻。
                    s0 = snapshot(ROOT)
                    adir = sandbox_attempt_dir(sandbox_root, nid, attempt_total)
                    rec = run_node(node, args.engine, dsh_bin, fd, results, args.timeout,
                                   tier=tier, overlay_dir=overlays_dir, attempt=attempt_total,
                                   counter_dir=counters_dir, prev=prev_fail,
                                   sandbox_dir=adir, run_id=run_id, egress=egress_info)
                    s1 = snapshot(ROOT)
                    node_diff = diff_snapshot(s0, s1, run_id=run_id)
                    # C4：窗口**之后**才复核 —— 这是唯一能干净归因的时刻（起跑前那一格的
                    # 归因必错，理由见上面那段注释）。
                    rec["evidence_tampered"] = verify_evidence()
                    # N1：临时区采集也必须在窗口**外** —— 归档会往 `runs/<id>/SANDBOX/ARCHIVE/`
                    # 写文件（辅助区，豁免 evidence_write），在窗口里写就会被算成「碰证据」。
                    rec["sandbox"] = record_sandbox(nid, attempt_total, adir)

                    allow = allow_set(node, results)
                    if rec.get("timed_out"):
                        # F9(a)：超时后**不跑** cmd —— `ok` 早已是 False，这一步只白耗最多
                        # `--cmd-timeout` 秒，而且它跑出来的变化会被算进下一个 attempt 的窗口。
                        cmd_results = []
                        s2 = s1
                        rec["cmd_skipped"] = "timeout"
                    else:
                        cmd_results = run_cmd_accept(node, args.cmd_timeout)
                        s2 = snapshot(ROOT)
                    cmd_diff = diff_snapshot(s1, s2, run_id=run_id)

                    ok, checks = compare(rec, node_diff, cmd_results, allow, node=node)
                    # F5（2026-11-09）：cmd 非零 **且** 本次 attempt 零落盘变化 ⇒ **确定性失败**
                    # （执行策略、缺依赖、脚本路径错那类）。重试和升档都改不了它 ——
                    # E2E 里 `integ` 就为同一个执行策略错误白烧了 900 + 594 秒。
                    deterministic = ((not ok) and any(not c.get("ok") for c in cmd_results)
                                     and not node_diff["changed_paths"])
                    # 员工自报的 status 要参与判定 —— 对齐解释器的 normStatus（`:881-887`）：
                    # 报了 need_info 就是 need_info，报了 fail 就别让 compare 的「四项全空」把它抬成 ok。
                    rst = None
                    if isinstance(rec.get("result"), dict):
                        raw_s = str(rec["result"].get("status") or "").strip().lower()
                        rst = raw_s.replace("-", "_").replace(" ", "_") or None
                    if rst == "need_info":
                        ok = False
                    elif rst is not None and rst not in ("ok", "pass", "done"):
                        ok = False

                    rec.update({
                        "checks": checks,
                        "cmd_accept": cmd_results,
                        "allow": sorted(allow),
                        "node_diff": node_diff,
                        "cmd_effects": cmd_diff,
                        "sandbox_writes": node_diff.get("sandbox_writes") or [],
                        "self_status": rst,
                        "deterministic_fail": deterministic,
                        "ok": ok,
                    })
                    harm |= harm_of(checks)
                    tick.append({
                        "attempt": attempt_total, "tier": tier, "model": TIERS[tier]["model"],
                        "exit_code": rec.get("exit_code"), "ok": ok, "self_status": rst,
                        "checks": checks, "node_diff": node_diff,
                        "cmd_accept": [{"run": c.get("run"), "exit_code": c.get("exit_code"), "ok": c.get("ok"),
                                        "started_at": c.get("started_at"), "finished_at": c.get("finished_at")}
                                       for c in cmd_results],
                        # 时间戳两条路径形状一致（第 1 轮 N3）：并行路径早就带，串行这边原先只藏在 `.aN.json` 里
                        "duration_s": rec.get("duration_s"), "wall_s": rec.get("wall_s"),
                        "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
                        "timed_out": rec.get("timed_out"), "deterministic_fail": deterministic,
                        "cmd_skipped": rec.get("cmd_skipped"),
                        "attribution": "attempt",
                    })
                    # F11：把这次的失败原因留给**下一次** attempt 的 prompt（成功就不留了）。
                    if not ok:
                        prev_fail = {"attempt": attempt_total, "tier": tier,
                                     "lines": fail_lines(rec, checks, cmd_results, rst)}
                    final = rec
                    write_node(nid, rec)
                    # 每次 attempt 的**完整**证据都单独留一份：`<nid>.json` 最后会被汇总覆盖，
                    # 不单独留档的话「第 1 次到底报了什么」就查不到了 —— 而失败那次正是最该看的那次。
                    write_attempt(nid, rec, attempt_total)

                    say("  [%s] %s  attempt=%d  tier=%s(%s)  exit=%s  %.1fs  tool_calls=%d  result=%s" % (
                        nid, "ok  " if ok else "FAIL", attempt_total, tier, rec.get("model"),
                        rec.get("exit_code"), rec.get("duration_s") or 0,
                        len(rec.get("tool_calls") or []), "yes" if rec.get("result_found") else "NO"))
                    if not ok:
                        for key in CHECK_KEYS:
                            if checks.get(key):
                                say("        %s: %s" % (key, ", ".join(checks[key])))
                        for c in cmd_results:
                            if not c.get("ok"):
                                say("        cmd failed (exit=%s): %s" % (c.get("exit_code"), c.get("run")))
                        if rst:
                            say("        self_status: %s" % rst)

                    # 停止条件（顺序对齐解释器 `:990-1000`；`deterministic` 是本文件新增的硬停）
                    if ok:
                        stop = "ok"
                    elif harm:
                        # 越界/只读违规是**已经发生的损害**，重试洗不掉 —— 不再浪费调用
                        stop = "harm"
                    elif checks.get("check_rejected") or checks.get("check_protocol_error"):
                        # N2（2026-11-10）：核查节点否决 / 协议失败 —— 硬停，不重试也不升档。
                        # 独立于 `harm`（它不是损害）也独立于 `deterministic`（它不是「cmd 跑不动」）：
                        # 三种原因的措辞与病根都不同，混进一个 stop 值会让失败原因说不清。
                        say("        check 裁决未通过（%s）→ 不重试、不升档"
                            % ((checks.get("check_rejected")
                                or checks.get("check_protocol_error"))[0],))
                        stop = "check_verdict"
                    elif deterministic:
                        say("        cmd 确定性失败（非零退出且本次零落盘）→ 不重试、不升档")
                        stop = "deterministic"
                    elif rst == "need_info":
                        # §4：信息不够时同档重试等于让它再猜一遍 → 直接升档
                        # ⚠️ 2026-11-09 修（B1 顺手修的既有偏差）：原先这里是 `stop = "need_info"`，
                        # 而外层循环开头是 `if stop: break` → **升档从来没发生过**，与解释器
                        # `dag.workflow.mjs:991-1000`（need_info → break 同档 → 外层继续升档）不一致。
                        # 现在只有「没有更高档」时才落 need_info，否则跳出同档循环让外层升档。
                        if ti < len(tiers) - 1:
                            say("        need_info → 跳过同档重试，直接升 %s 档" % tiers[ti + 1])
                            break
                        stop = "need_info"
                    elif k < tries - 1:
                        say("        %s 档第 %d 次失败，同档重试" % (tier, k + 1))
                if not stop and ti < len(tiers) - 1:
                    say("        " + escalate_log(args.engine, tier, tiers[ti + 1]))
                if stop == "harm":
                    break

            # 5) 收尾：状态 + on_fail.then（对齐 `:1014-1039`）
            of = node.get("on_fail") or {}
            then = of.get("then") or "replan"
            why_fail = None
            if stop == "ok":
                node_status = "ok"
            elif stop == "need_info":
                node_status = "need_info"
            else:
                if stop == "harm":
                    why_fail = "越界/只读违规/写主树/碰证据（**不可用重试洗掉**）：" + ", ".join(sorted(harm))
                elif stop == "budget":
                    why_fail = "预算余量不足，重试/升档被拒（已起 %d / 上限 %s）" % (started, max_calls)
                elif stop == "deterministic":
                    parts = []
                    for key in CHECK_KEYS:
                        if (final.get("checks") or {}).get(key):
                            parts.append("%s=%s" % (key, ",".join(final["checks"][key])))
                    for c in (final.get("cmd_accept") or []):
                        if not c.get("ok"):
                            parts.append("cmd=%r exit=%s" % (c.get("run"), c.get("exit_code")))
                    why_fail = ("cmd 确定性失败（非零退出且本次零落盘，重试/升档都救不回来）："
                                + ("；".join(parts) or "未归类"))
                elif stop == "check_verdict":
                    # N2：与上面两条**分开措辞**。研判 2026-11-10：核查失败不等于「产物已证明错误」
                    # （模型可能只是读错了文件），所以这里只说「核查未通过」，并把裁决原文带上。
                    fc = final.get("checks") or {}
                    why_fail = "核查节点未通过（不重试、不升档）：check_rejected=%s；check_protocol_error=%s" % (
                        ",".join(fc.get("check_rejected") or []) or "-",
                        ",".join(fc.get("check_protocol_error") or []) or "-")
                elif final is not None:
                    parts = []
                    for key in CHECK_KEYS:
                        if (final.get("checks") or {}).get(key):
                            parts.append("%s=%s" % (key, ",".join(final["checks"][key])))
                    if final.get("exit_code") not in (0, None):
                        parts.append("exit_code=%s" % final.get("exit_code"))
                    if not final.get("result_found"):
                        parts.append("没有 RESULT 块")
                    if (final.get("self_status") or "") not in ("", None, "ok", "pass", "done"):
                        parts.append("self_status=%s" % final.get("self_status"))
                    why_fail = "；".join(parts) or "未通过判定"
                else:
                    why_fail = "没有拿到任何 attempt"
                node_status = "fail"
                if then == "skip":
                    node_status = "skipped"
                    skip_why[nid] = "on_fail.then=skip：" + (why_fail or "")
                elif then == "abort":
                    aborted = True
                    say("        then=abort → 后续节点不再起跑（已在跑的不取消）")
                elif then == "replan":
                    replan_needed.append(nid)

            rec_out = dict(final or {"node": nid, "kind": node.get("kind")})
            rec_out.update({
                "status": node_status,
                "ok": node_status == "ok",
                "attempts": tick,
                "attempt_count": len(tick),
                "final_tier": (final or {}).get("tier"),
                "harm": sorted(harm),
                "why": why_fail,
                "deterministic_fail": bool((final or {}).get("deterministic_fail")),
                # K9：codex 下请求了升档但引擎没有档位映射 —— 留痕（其它引擎恒为 None）
                "escalate_ignored": (final or {}).get("escalate_ignored"),
            })
            records[nid] = rec_out
            node_ok[nid] = node_status == "ok"
            status[nid] = node_status
            if node_status == "ok" and isinstance(rec_out.get("result"), dict):
                results[nid] = rec_out["result"]
            write_node(nid, rec_out)
            if node_status != "ok":
                say("  [%s] → %s%s" % (nid, node_status,
                                       ("（then=%s）" % then) if node_status != "need_info" else ""))

    fs_final = snapshot(ROOT)
    write_out("FS-AFTER.json", json.dumps(fs_final, ensure_ascii=False, indent=1))
    write_out("DIFF.json", json.dumps(diff_snapshot(fs_initial, fs_final, run_id=run_id),
                                      ensure_ascii=False, indent=2))

    all_ok = bool(status) and all(v == "ok" for v in status.values()) and len(status) == len(nodes)
    # N2：核查节点的裁决摘要汇总（raw / normalized / decision / reason / attempt）。
    # 为什么还要这一份：节点 `status` 只回答「过没过」，这份回答「核查当时说了什么」——
    # 交付说明里写结论时必须能引用原词（`fail` 与 `protocol_error` 的处置相同、含义完全不同）。
    check_verdicts = {}
    for nid, node in nodes.items():
        if str(node.get("kind") or "") != CHECK_KIND:
            continue
        cv = (records.get(nid) or {}).get("check_verdict")
        if cv is None:
            # 没跑过（skipped / need_info 之前就停了）：如实记，不编一个 pass 出来
            check_verdicts[nid] = {"decision": "not_run", "reason": (skip_why.get(nid)
                                                                   or (records.get(nid) or {}).get("why"))}
        else:
            check_verdicts[nid] = cv
    run = {
        "run_id": run_id,
        "graph": str(graph_path),
        "engine": args.engine,
        "fake": args.engine == "fixture",
        # B1（第 1 轮验收官）：只读执法层必须在**顶层**看得见。原先只有 `NODES/*.a1.json` 里有 ——
        # 而复核者打开证据包先看的是 RUN.json，`grep snapshot_only RUN.json` 0 命中就等于没说。
        "readonly_enforcement": READONLY_ENFORCEMENT.get(args.engine, "unknown"),
        # codex（K5）：egress 自检的结论要能**事后核**，不只留在屏幕滚过的日志里。
        # 端口一起记 —— `RUN.json.egress.port` 与每个 attempt 的 `proxy` 对得上，才说明注入真生效。
        "egress": ({"checked": True, "port": egress_info.get("port"),
                    "result": egress_info.get("result"), "action": egress_info.get("action"),
                    "script": egress_info.get("script")}
                   if egress_info else
                   {"checked": False, "why": "--engine %s 不走 claude/codex 订阅通道" % args.engine}),
        # N3（2026-11-10）：`started_at` 原先在**收尾时**取时间，于是它与 duration_s 自相矛盾
        # （B4 实测 started_at=20:29 而 1830s 说明 19:58 起跑）。现在取起跑那一刻，并补 finished_at。
        "started_at": run_started_at,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(time.time() - t_start, 1),
        "gate": {"ok": gate.get("ok"), "errCount": gate.get("errCount"), "warnCount": gate.get("warnCount")},
        "waves": waves,
        "serial": not parallel_groups,
        "parallel": {
            "enabled": not args.no_parallel,
            "max_workers": max(1, args.max_parallel),
            "groups": parallel_groups,
            "demoted": parallel_demoted,
        },
        "sandbox": {
            "root": str(sandbox_root),
            "outside_workspace": not _overlaps(sandbox_root, ROOT),
            "why": sandbox_why,
            "attempts": sandbox_records,
            "total_files": sum(r.get("files") or 0 for r in sandbox_records),
            "total_bytes": sum(r.get("total_bytes") or 0 for r in sandbox_records),
            "archived": sum(len(r.get("archived") or []) for r in sandbox_records),
            # ⚠️ 不自动删除：自动清理是不可逆操作，本单元不引入。复核完请手工删 `<root>`。
            "cleanup": "manual",
            "manifest": "SANDBOX-MANIFEST.json",
        },
        "check_verdicts": check_verdicts,
        "aborted": aborted,
        "nodes": {nid: {"status": status.get(nid), "ok": node_ok.get(nid),
                        "attempts": (records.get(nid) or {}).get("attempt_count", 0),
                        "final_tier": (records.get(nid) or {}).get("final_tier"),
                        "exit_code": (records.get(nid) or {}).get("exit_code"),
                        "deterministic_fail": bool((records.get(nid) or {}).get("deterministic_fail")),
                        "escalate_ignored": (records.get(nid) or {}).get("escalate_ignored"),
                        # B1：逐节点也带一次 —— 将来若真做「同 run 混引擎」，顶层那个就不够用了
                        "readonly_enforcement": (records.get(nid) or {}).get("readonly_enforcement"),
                        "timeout_attempts": sum(1 for a in ((records.get(nid) or {}).get("attempts") or [])
                                                if (a or {}).get("timed_out")),
                        "why": (records.get(nid) or {}).get("why")} for nid in nodes},
        "skipped": skip_why,
        # F5 / F3 的汇总口径：确定性失败（白烧重试）与超时 attempt，一次就能在 RUN.json 里数清
        "deterministic_fail": sorted(nid for nid in nodes
                                     if (records.get(nid) or {}).get("deterministic_fail")),
        "timeouts": sorted(nid for nid in nodes
                           if any((a or {}).get("timed_out")
                                  for a in ((records.get(nid) or {}).get("attempts") or []))),
        "replan_needed": replan_needed,
        "budget": {"max_calls": max_calls, "agent_calls": started, "denied": budget_denied},
        "result": "pass" if all_ok else "fail",
    }
    # N1：临时区清单**必须落盘**。外置之后 `sandbox_writes` 会常态为空（员工临时文件不再落在
    # workspace 里），如果只靠那个字段，就变成「看不见 = 没写」—— 清单是这里的唯一输入。
    write_out("SANDBOX-MANIFEST.json", json.dumps({
        "run_id": run_id,
        "root": str(sandbox_root),
        "outside_workspace": not _overlaps(sandbox_root, ROOT),
        "cleanup": "manual",
        "caveat": ("清单只记录**采集那一刻**观察到的文件集合：两次扫描之间创建又删除的文件抓不到，"
                   "它不等于「完整临时文件历史」；>%d 字节的文件只留清单不归档（archived=false + why_not）。"
                   % SANDBOX_ARCHIVE_MAX_BYTES),
        "attempts": sandbox_manifest,
    }, ensure_ascii=False, indent=2))
    write_out("RUN.json", json.dumps(run, ensure_ascii=False, indent=2))

    say("")
    say("[runner] result  %s" % run["result"].upper())
    if replan_needed:
        say("[runner] replan  %s" % ", ".join(replan_needed))
    say("[runner] 证据包  " + str(out_dir))
    return 0 if run["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
