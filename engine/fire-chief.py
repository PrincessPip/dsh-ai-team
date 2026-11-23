#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""救火队长执行器 · fire-chief.py

    python scripts/fire-chief.py <INC 文件> [--engine codex|claude] [--model M] [--effort xhigh]
                                 [--timeout 3600] [--dry-run] [--egress-check PATH] [--root DIR]

它做的是**一件事**：把一份事故单（`INC-TEMPLATE.md` v1.2）变成一次可审计的救火调用，
并把「越界了没有 / 四件齐不齐 / 喂进去的和写下来的对不对得上」当场核完。

## 为什么要有这个脚本

规则（`TRIGGER-POLICY.md` §二·五 v3.0）早就写好了，但 2026-11-07 到 11-10 的两次实战全靠手敲命令：

  * 跑前跑后快照是手抄的（`INC-TEMPLATE.md` §八 那条 PowerShell），只覆盖 mtime + 字节数；
  * 「白名单外零改动」是肉眼对 diff；
  * 产物目录的 sha256（第 2 轮验收官点的 N2）根本没做；
  * 换引擎（Claude → GPT-6 Astra）等于重抄一遍命令，抄错一个参数就是一次白跑的额度。

手敲的判据不是判据，是自觉。所以固化。

## 判据（每一条都写清为什么）

| 判据 | 出口码 | 为什么 |
|---|---|---|
| 事故单不合格（占位符没填 / 白名单空 / 编号不合法 / 没有产物目录授权） | **2** | 半张事故单派出去，等于让它自由发挥——那是白狼单 |
| egress 自检不是 READY | **4** | 与 `manager.py:126-173`「def check_egress」同款：**退出码 0 且输出含 `[result] … READY` 两条都成立**。只看退出码等于留了个假跳过开关（第 2 轮验收 N1 的原话） |
| 引擎非零退出 / 超时 | **3** | 超时也落盘已捕获的输出——`runner.py` 那条「超时把 stdout 丢了」的教训 |
| 白名单外有改动 | **5** | 救火队长持写权限，白名单是提示词约束**不是硬锁**。事后对账是唯一兜底 |
| 四件取证不齐 | **6** | `TRANSCRIPT/CHANGES/VERIFY/SNAPSHOT`。**前三件由本脚本渲染，`VERIFY.md` 归救火队长**（见 `ARTIFACT_FILES` 上方那段）；缺件时分「机器该写的没写出来 = 本脚本 bug」与「工人没交作业」两种报法，不混为一谈 |

注意 `2/4/6` 都发生在**建目录之前或引擎输出之后**，所以失败时不留半截证据包（反例测试才好断言）。

## 引擎

| engine | 通道 | 模型 | egress 脚本 |
|---|---|---|---|
| `codex`（默认） | ChatGPT 订阅（7 天周窗口） | `gpt-6.2-astra` | `codex-egress-check.ps1` |
| `claude` | Claude 订阅（5h 窗口） | `opus` | `claude-egress-check.ps1` |

**默认换 codex 的理由（2026-11-11 伙伴拍板）**：GPT-6 Astra 比 Opus 5.5 强，
而且两条额度窗口互相独立——5h 窗口装不下「救火 + 两轮验收」这件事已经发生过两次。
验收官仍是 Opus，于是**执行体与裁决方不再同模型**，`FIRE-CHIEF.md` §七 那条「同模型同盲区」自动降级。

⚠️ 2026-11-11 实测：`gpt-6-astra-pro` 在订阅通道**不可用**
（HTTP 400 `model_not_found`: not supported when using Codex with a ChatGPT account），
它只存在于按量计费（OpenRouter）。这条已进 `~/.dsh/AGENTS.md` 硬约束第 2 条。

📌 **2026-11-23 更新**：订阅目录里已有 **`gpt-6.2-astra` / `gpt-6.2-sol`**（codex CLI 0.167.2 的
`models_cache.json` 下发，网关实测 `gpt-6.2-astra` 通过），**默认模型已从 `gpt-6-astra` 升到 `gpt-6.2-astra`**。
「订阅里最强」这句的所指随之变化，但**「先在订阅里挑，不先去按量通道」这条原则没变**。

## 编码（2026-11-11 四个实验钉死的，别再猜）

本机代码页 936，历史上踩过一串坑，实测结论：

* 中文 prompt **经 argv 传进 codex 完好**（用码点探针验过：`6551,706B,961F,957F` 精确回显）；
* codex 的 stdout 是 **UTF-8**，中文完好；它写出的文件也是 UTF-8；
* 唯一的坑是**本脚本自己 print 到控制台**会撞 CP936 —— 所以下面 `_reconfigure_stdout()` 先重配。

所以：**argv 传 prompt、UTF-8 直读直写**，不搞 stdin 传参、不搞 GBK 解码。
（喂入副本仍然带 BOM —— 那是给 PS 5.1 **读文件**兜底的，与「怎么传 prompt」是两件事。
原稿把两者混成一句写「不搞 BOM 副本」，自相矛盾，2026-11-11 第 1 轮验收官 N5 抓出来了。）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

EXIT_OK = 0
EXIT_INC = 2          # 事故单不合格
EXIT_CALL = 3         # 引擎调用失败 / 超时
EXIT_EGRESS = 4       # egress 自检未就绪
EXIT_BREACH = 5       # 白名单外有改动
EXIT_ARTIFACTS = 6    # 四件取证不齐

# 快照排除区。**故意只排这三类**：依赖树、版本库、字节码缓存 —— 它们不是「我们的文件」。
# ⚠️ 2026-11-11 第 1 轮验收官 B2：这里原先还排了 dist / build / venv / .venv，而救火队长
# 完全可能往 `scripts/build/` 或 `dist/` 写东西 —— 排掉等于给它开了一块看不见的地方。
# **宁可快照慢一点，也不留观测盲区。** 排除项变化必须同步到 `INC-TEMPLATE.md` §五（那里如实列）。
EXCLUDE_DIR_NAMES = {"node_modules", ".git", "__pycache__", ".pytest_cache"}
EXCLUDE_REL_PREFIXES = ("runs/_harness/",)

# 禁改面（`TRIGGER-POLICY.md` §二·五·五 硬红线）。写进白名单就等于让考生改考卷，
# 所以这里**硬拦**，不是靠事故单自觉。目录 token 覆盖到它们也算。
FORBIDDEN_PATHS = (
    "scripts/ledger-audit.ps1",
    "scripts/ledger-external.txt",
    "REVIEW-CONTRACT.md",
    "TRIGGER-POLICY.md",
    "decisions/self/ledger.md",
)

INC_ID_RE = re.compile(r"^INC-\d{8}-\d{2}$")
TRIGGER_PREFIXES = ("自动 A", "自动 B", "伙伴点名")
QUOTA_RE = re.compile(r"(?i)(\b429\b|quota|rate[ _-]?limit|usage limit|too many requests|overloaded)")

# egress 的「就绪」判据：**整行精确匹配**，不是子串。理由见 `check_egress()` 里的 N1 注释。
READY_RE = re.compile(r"^\[result\]\s+READY\s*$")

# 四件取证（`INC-TEMPLATE.md` §五）。少一件就只剩自述。
#
# ⚠️ 2026-11-11（`DOC-SYNC-2026-11-11`）：**前三个由本脚本自己渲染，`VERIFY.md` 归救火队长。**
# 这一条改掉了原先的死锁 —— 旧契约把四件全派给工人，而 `TRANSCRIPT.log` 要的是
# 「命令 + 输出 + 退出码，时间顺序」，那是**执行进程**才看得见的观测：模型拿不到自己的完整命令流水。
# 于是工人怎么交都错：不写就 `exit 6`（本次救火作废），自己拿 PowerShell 拼就转码
# （`INC-20261111-03` 判 blocking 的那个动作），而 §5.6 又明令禁止事后补件。
# 派一件结构上做不到的事，是契约 bug，不是工人的失误。
ARTIFACT_FILES = ("TRANSCRIPT.log", "CHANGES.md", "VERIFY.md", "SNAPSHOT.txt")
# 由脚本渲染的前三件；剩下的那件才是工人的作业。
MACHINE_ARTIFACTS = ("TRANSCRIPT.log", "CHANGES.md", "SNAPSHOT.txt")
WORKER_ARTIFACTS = ("VERIFY.md",)


# ------------------------------------------------------------------ 基础设施

# 执行器流水：`TRANSCRIPT.log` 段 1 的素材。`say()` / `die()` 每说一句就记一条，
# 所以「我做过什么」不靠事后回忆，是随手攒下来的。
_T0 = time.time()
_TRACE = []


def _reconfigure_stdout():
    """Windows 控制台默认 CP936，直接 print 生僻字符会抛 UnicodeEncodeError。

    注意：这不影响**子进程**的编码（那条走 UTF-8，实测无问题），只管我们自己说话。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def say(msg=""):
    _TRACE.append({"t": time.time(), "stream": "out", "text": str(msg)})
    print(msg, flush=True)


def die(code, msg):
    _TRACE.append({"t": time.time(), "stream": "err", "text": "[fire-chief] " + str(msg)})
    print("[fire-chief] " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_utf8(path: Path, text: str):
    """一律按字节写，不让 Python 做换行转换。

    ⚠️ `Path.write_text` 默认 `newline=None`，在 Windows 上会把 `\\n` 改成 `\\r\\n`。
    事故单副本因此会被写坏（原文里的 `\\r\\n` 变成 `\\r\\r\\n`），sha256 也对不上。
    这行注释是 2026-11-11 自查时发现的，不是猜的。
    """
    path.write_bytes(text.encode("utf-8"))


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


# ------------------------------------------------------------------ 事故单解析

def _cell(text: str, label: str):
    """取 `| label | value |` 的第一个 value 单元格（去加粗 / 反引号）。"""
    m = re.search(r"^\|\s*%s\s*\|\s*(.+?)\s*\|\s*$" % re.escape(label), text, re.M)
    if not m:
        return None
    return m.group(1).strip()


def _strip_marks(s: str) -> str:
    return s.replace("**", "").replace("`", "").strip()


def parse_whitelist(text: str):
    """从 §4.1 表格取写面白名单。

    返回 (files, dirs)：files = 精确文件相对路径集合，dirs = 目录前缀（`x/**` 去掉 `/**`）。

    **解析不出来就 die(2)，不许静默放行**：白名单是「写窄」的唯一载体，
    解析失败时如果退化成「没有白名单 = 不限制」，那这个脚本就成了越界的帮凶。
    """
    m = re.search(r"^#{2,4}\s*4\.1.*?$(.*?)(?=^#{2,4}\s|^\Z)", text, re.M | re.S)
    if not m:
        die(EXIT_INC, "事故单里没找到 §4.1 写面白名单。模板见 INC-TEMPLATE.md §四。")

    body = m.group(1)
    entries = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = line.strip("|").split("|")
        if not cells:
            continue
        first = cells[0].strip()
        mm = re.match(r"^`([^`]+)`$", first)
        if mm:
            entries.append(mm.group(1).strip())

    if not entries:
        die(EXIT_INC, "§4.1 白名单表格里一条路径都没解析出来（第一列必须是反引号包裹的路径）。")

    files, dirs = set(), set()
    for e in entries:
        if "{{" in e or "}}" in e:
            die(EXIT_INC, "§4.1 白名单里有没填的占位符：%s" % e)
        e = e.replace("\\", "/")
        if e.endswith("/**"):
            dirs.add(e[:-3])
        elif e.endswith("/"):
            dirs.add(e.rstrip("/"))
        else:
            files.add(e)
    return files, dirs


# 必填区：双花括号内非空、不跨行的内容都算占位符（含空格、标点、Markdown）。
PLACEHOLDER_RE = re.compile(r"\{\{[^{}\r\n]+\}\}")
# 仅单反引号包住的三个完整语法片段可豁免；不扩大到普通占位符或整行。
PLACEHOLDER_SYNTAX_RE = re.compile(r"(?<!`)`(?:\{\{|\}\}|\{\{…\}\})`(?!`)")
# 只豁免显式“例如”后紧接的反引号变量示例列表，不豁免整行、引用块或代码块。
PLACEHOLDER_EXAMPLE_RE = re.compile(
    r"例如[：:]?\s*(?:`\{\{[A-Za-z0-9_\u3400-\u9fff]+\}\}`[、，,\s]*)+")


def first_placeholder(text: str):
    """返回第一处未填变量的 (1-based 行号, 原串)，允许区只认二级章节标题。

    围栏内的伪章节标题不能切换允许区；允许区的子标题仍属该区。
    保留最窄语法片段与既有“例如”列表豁免；示例列表不判断人类意图。
    """
    allowed = False
    fence = None
    for lineno, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
        elif marker:
            fence = marker[1]
        else:
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading:
                allowed = bool(re.match(
                    r"^(?:七|十|附录|附|变更记录)(?=$|[、，：:\s])", heading[1]))
        scan = PLACEHOLDER_EXAMPLE_RE.sub("", line)
        scan = PLACEHOLDER_SYNTAX_RE.sub("", scan)
        found = PLACEHOLDER_RE.search(scan)
        if found and not allowed:
            return lineno, found[0]
    return None


def normalized_path(path):
    """禁改面比较采用 Windows 的大小写不敏感 + 路径折叠语义。"""
    return os.path.normpath(str(path).replace("\\", "/")).replace("\\", "/").lower()


def parse_inc(path: Path, root: Path):
    """解析事故单。任何一项不合格 → die(2)。"""
    if not path.is_file():
        die(EXIT_INC, "事故单不存在：%s" % path)
    try:
        # read_bytes + 显式解码：`read_text` 会做 universal-newlines 转换（\r\n → \n），
        # 那样下面的 sha256 对账必然报假不一致。
        text = path.read_bytes().decode("utf-8-sig")
    except Exception as e:
        die(EXIT_INC, "事故单不是合法 UTF-8：%s（%s）" % (path, e))

    placeholder = first_placeholder(text)
    if placeholder:
        die(EXIT_INC, "事故单第 %d 行有未填占位符：%s" % placeholder)

    inc_id = _strip_marks(_cell(text, "事故编号") or "")
    if not INC_ID_RE.match(inc_id):
        die(EXIT_INC, "「事故编号」不合法：%r（要形如 INC-20261111-01）" % inc_id)

    trigger_raw = _strip_marks(_cell(text, "触发方式") or "")
    trigger = next((p for p in TRIGGER_PREFIXES if trigger_raw.startswith(p)), None)
    if trigger is None:
        die(EXIT_INC, "「触发方式」认不出：%r（只接受 自动 A / 自动 B / 伙伴点名）" % trigger_raw)

    fact = _cell(text, "触发事实") or ""
    if "{{" in fact:
        die(EXIT_INC, "「触发事实」还是占位符没填——自动 A/B 不需要点头，这一行是事后唯一审计口。")

    wd_raw = _strip_marks(_cell(text, "工作目录") or "")
    if not wd_raw:
        die(EXIT_INC, "「工作目录」没填。")
    work_dir = Path(wd_raw)
    if not work_dir.is_absolute():
        die(EXIT_INC, "「工作目录」要是绝对路径：%r" % wd_raw)
    if not work_dir.is_dir():
        die(EXIT_INC, "「工作目录」不存在：%s" % work_dir)

    if work_dir.resolve() != root.resolve():
        die(EXIT_INC, "「工作目录」与 --root 不一致：%s != %s" % (work_dir, root))

    files, dirs = parse_whitelist(text)

    # 禁改面硬拦（第 1 轮验收官 N4）：`TRIGGER-POLICY.md` §二·五·五 管这叫「硬红线」，
    # 但脚本原先没有对应判据 —— 于是「硬红线」实际只靠事故单自觉。这里补上。
    forbidden = {normalized_path(f) for f in FORBIDDEN_PATHS}
    hit = sorted(f for f in files if normalized_path(f) in forbidden)
    for d in dirs:
        nd = normalized_path(d)
        for f in forbidden:
            if nd == "." or f == nd or f.startswith(nd + "/"):
                hit.append("%s（被目录 token `%s/**` 覆盖）" % (f, d))
    if hit:
        die(EXIT_INC,
            "§4.1 白名单里出现了**禁改面**文件（TRIGGER-POLICY.md §二·五·五 硬红线）：\n  "
            + "\n  ".join(hit)
            + "\n  判据类文件不许交给救火队长改 —— 送审是事后，禁改是事前。")

    # 产物目录：白名单里必须有 `runs/fire/…/**`，否则四件体检没有落点，
    # 而且救火队长等于没有合法的写产物授权。
    prod_candidates = sorted(dirs)  # 所有目录 token 都只能是本单产物目录
    if not prod_candidates:
        die(EXIT_INC, "§4.1 白名单里缺少产物目录授权（要有一条 `runs/fire/%s/**`）。" % inc_id)
    prod_dir = "runs/fire/" + inc_id
    if set(prod_candidates) != {prod_dir}:
        die(EXIT_INC, "产物目录必须且只能是 runs/fire/本单事故编号：%s（收到 %s）"
            % (prod_dir, sorted(prod_candidates)))

    # N2 收尾（2026-11-11）：目录 token 已经收紧到「只能是本单产物目录」，
    # 但**文件级 token 一直没人管** —— 白名单写 `runs/fire/INC-旧/VERIFY.md`
    # 照样 rc=0，于是救火队长的写面能盖到**别单的救火证据**上。
    # 范围只取 `runs/fire/`：那是各单的四件取证区，改别人的等于改历史。
    # ⚠️ 不能一刀切成「`runs/` 全都不许」—— `runs/plan-*` 是 DAG 的输入物，
    # 2026-11-10 的 INC-20261110-01 就是靠「改 `runs/plan-20261107-003443/GRAPH.json`」
    # 干活的；写成 `runs/` 会把它判成不合格（T39 当场抓到过）。
    prod_prefix = normalized_path(prod_dir)
    fire_root = "runs/fire/"
    stray = sorted(
        f for f in files
        if normalized_path(f).startswith(fire_root)
        and not (normalized_path(f) == prod_prefix
                 or normalized_path(f).startswith(prod_prefix + "/")))
    if stray:
        die(EXIT_INC,
            "§4.1 白名单里有 `runs/fire/` 下别的单的文件级 token：\n  "
            + "\n  ".join(stray)
            + "\n  那是别的单元的四件取证区 —— 改别单的证据等于改历史（本单只许写 %s/ 之内）。"
              "本单产物请用目录 token `%s/**`。" % (prod_dir, prod_dir))

    # 同一处的另一半：文件 token 必须是相对 `--root` 的相对路径。
    # 这是**输入卫生**，不是「防绕过」—— `in_whitelist()` 比的是**原始相对字符串**，
    # 绝对 token 本来就授不了写权。它挡的是另一件事：如果有人把上面那条 `runs/fire/` 前缀判据
    # 当成「白名单的语义边界」，那么换一种写法（`D:/x/VERIFY.md`）就会让**读过判据的人**以为它被管住了。
    # ⚠️ 别把它吹成安全边界 —— 第 1 轮验收官 N5 就是按「夸大了它保护的东西」否的。
    abs_like = sorted(f for f in files if re.match(r"^[A-Za-z]:", f) or f.startswith("/"))
    if abs_like:
        die(EXIT_INC, "§4.1 白名单里的路径必须是相对 `--root` 的相对路径：\n  "
            + "\n  ".join(abs_like))

    return {
        "path": path.resolve(),
        "id": inc_id,
        "trigger": trigger,
        "trigger_raw": trigger_raw,
        "trigger_fact": fact,
        "work_dir": work_dir,
        "whitelist_files": files,
        "whitelist_dirs": dirs,
        "prod_dir_rel": prod_dir,
        "prod_dir": root / prod_dir.replace("/", os.sep),
        "text": text,
    }


# ------------------------------------------------------------------ 引擎

def find_codex():
    """找 codex.js。**它不在 PATH 里**（插件按包内路径起），所以裸 `codex` 命令不存在。"""
    env = os.environ.get("CODEX_JS")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        die(EXIT_INC, "CODEX_JS 指向的文件不存在：" + env)
    p = Path.home() / ".dsh" / "profiles" / "web" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    if p.is_file():
        return p
    die(EXIT_INC, "找不到 codex.js：%s（或用 CODEX_JS 指定）" % p)


def find_claude():
    """找 claude.exe。**必须是 .exe** —— .cmd/.ps1 会再套一层 shell，argv 转义又回来了。"""
    env = os.environ.get("CLAUDE_EXE")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        die(EXIT_INC, "CLAUDE_EXE 指向的文件不存在：" + env)
    appdata = os.environ.get("APPDATA") or ""
    cands = [
        Path(appdata) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
    ]
    for c in cands:
        if c.is_file():
            return c
    which = shutil.which("claude.exe")
    if which:
        return Path(which)
    die(EXIT_INC, "找不到 claude.exe。装 Claude Code 或用 CLAUDE_EXE 指定路径。")


def proxy_env():
    """子进程环境：把 DSH 的两条代理变量指到网关。

    为什么必须显式给：codex 子进程**不读 DSH 的代理配置**（2026-11-10 首次 device 登录就栽在这）。
    端口不写死——从当前环境里已有的 127.0.0.1:<port> 推断；推不出就**不覆盖**代理变量（继承现有环境）。
    """
    env = dict(os.environ)
    port = None
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        v = env.get(key) or ""
        m = re.search(r"127\.0\.0\.1:(\d+)", v)
        if m:
            port = m.group(1)
            break
    # 开源适配：推不出本机代理端口时**不覆盖**代理环境变量，让子进程继承现有环境。
    # （内部原版此处写死本机端口；硬编码在开源版里只会拼出非法 URL）
    if not port:
        return env
    url = "http://127.0.0.1:%s" % port
    env["HTTPS_PROXY"] = url
    env["HTTP_PROXY"] = url
    env["https_proxy"] = url
    env["http_proxy"] = url
    return env, port


def build_argv(engine, codex_js, claude_exe, model, effort, root, prompt):
    """返回 (argv, meta)。prompt 用 argv 传，不走 stdin（见文件头「编码」一节）。"""
    if engine == "codex":
        node = shutil.which("node") or "node"
        argv = [
            node, str(codex_js), "exec",
            "--json",                       # JSONL 事件流，可直接当证据
            "--skip-git-repo-check",        # 工程根不是 git repo 时也不至于直接死
            "-m", model,
            "-c", "model_reasoning_effort=%s" % effort,
            "--approve-for-me",             # workspace-write + auto_review；`-s read-only` 在 Windows 上禁一切命令（2026-11-10 实测）
            "-C", str(root),
            prompt,
        ]
        return argv, {"engine": "codex", "model": model, "effort": effort,
                      "requested_reasoning_effort": effort,
                      "readonly_enforcement": "snapshot_only"}
    argv = [
        str(claude_exe),
        "-p", prompt,                       # prompt 必须在 --allowedTools 之前（后者是可变参数，会吃掉位置参数）
        "--model", model,
        # N6（第 1 轮验收官）：原来漏了这一个，`parse_claude_result()` 必然 parse_error，
        # META 里永远拿不到 tokens —— 那等于备用通道的用量是黑的。
        "--output-format", "json",
        "--allowedTools", "Read,Grep,Glob,Bash,Write,Edit",
    ]
    return argv, {"engine": "claude", "model": model, "effort": None,
                  "readonly_enforcement": "dontask_by_contract"}


def build_prompt(inc, inc_as_fed_path):
    """指路式 prompt。正文不塞进来——长中文塞 prompt 会坏，而且正文它自己会读。

    三条固定提示都是本机/本项目的硬事实，不写进 prompt 它就得到处撞：
    """
    return (
        "你是救火队长。事故单在这里，先完整读它，然后按它执行：\n"
        "%s\n"
        "\n"
        "三条固定提示（都是本机实测事实，不要再自己试）：\n"
        "1) 事故单是你唯一的输入。§二·五 上下文交接清单、§四 白名单、§六 失败史 三节必读。\n"
        "2) 本机默认代码页是 936，读中文文件必须显式 UTF-8，否则会读成乱码：\n"
        "   powershell -Command \"Get-Content -LiteralPath '<p>' -Encoding UTF8\"\n"
        "   或 [System.IO.File]::ReadAllLines('<p>', [System.Text.Encoding]::UTF8)\n"
        "3) §4.1 白名单**写死**：白名单外任何文件都不许改，包括「顺手修一下」。\n"
        "   产物一律写到 %s/ ；意料之外的发现只报不改（写进 VERIFY.md 的「越界观察」）。\n"
        % (inc_as_fed_path.as_posix(), inc["prod_dir_rel"])
    )


# ------------------------------------------------------------------ egress

DEFAULT_EGRESS = {
    "codex": Path.home() / ".dsh" / "scripts" / "cdp" / "codex-egress-check.ps1",
    "claude": Path.home() / ".dsh" / "scripts" / "cdp" / "claude-egress-check.ps1",
}


def check_egress(engine, script_path=None):
    """不是 READY 就不许调引擎。**硬拦，没有跳过开关。**

    判据 = 退出码 0 **且** 输出里有 `[result] … READY` 行。
    只看退出码的版本被第 2 轮验收官当场打穿（指向一个 `exit 0` 的空脚本即可绕过），
    所以这里两条都要（与 `manager.py:158`「ready = [ln.strip() for ln in out.splitlines()」同款）。
    """
    script = Path(script_path) if script_path else DEFAULT_EGRESS[engine]
    script = script.expanduser()
    if not script.is_file():
        die(EXIT_EGRESS,
            "egress 自检脚本不存在：%s\n"
            "  这条**不能跳过** —— 它保证引擎走的是伪装出口。脚本丢了先把它修回来。" % script)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "") + (r.stderr or "")
    say("[fire-chief] ---- egress 自检（%s）----" % engine)
    for line in out.splitlines():
        say("  " + line)
    if r.returncode != 0:
        die(EXIT_EGRESS,
            "egress 自检没过（退出码 %d）—— **不调引擎**。\n"
            "  3 = 隧道没起（claude 可用 -Start 现拉）/ 4 = 出口 IP 不对（停手，IP 不一致会留风控痕迹）/ "
            "5 = 配置丢了 / 6、7 = codex 侧代理或轨迹补丁异常。\n"
            "  见 AGENTS.md「验收前置自检」。" % r.returncode)
    ready = [ln.strip() for ln in out.splitlines() if READY_RE.match(ln.strip())]
    # N1（第 1 轮验收官）：原先是子串判断 `"READY" in ln`，于是 `[result] NOT-READY` 也会被当成就绪 ——
    # 那不是「拦住了」，那是把拦的对象写错。改成整行精确匹配：[result] 之后只允许空白与 READY。
    if not ready:
        die(EXIT_EGRESS,
            "egress 自检退出码是 0，但输出里没有 `[result] … READY` 行 —— **不算就绪**。\n"
            "  判据是两条同时成立；只看退出码等于给了个跳过开关。\n"
            "  如果你传的是测试用假脚本，让它真的打印一行 `[result]    READY`。")
    return {"script": script.as_posix(), "exit_code": r.returncode, "result_line": ready[0]}


# ------------------------------------------------------------------ 快照与对账

def snapshot(root: Path, exclude_prefixes=()):
    """全树 `sha256` 快照。

    为什么不用 `INC-TEMPLATE.md` §八 那条 PowerShell：它只比 mtime + 字节数，
    **同一秒内、大小不变的改动它看不见**（模板自己也承认了）。全树 sha256 没有这个盲区。
    成本：712 文件 / 28.9 MB 实测 0.7 s，可接受。
    """
    snap = {}
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in EXCLUDE_DIR_NAMES]
        for name in filenames:
            p = d / name
            try:
                rel = rel_posix(p, root)
            except ValueError:
                continue
            if any(rel.startswith(pref) for pref in EXCLUDE_REL_PREFIXES):
                continue
            if any(rel.startswith(pref) for pref in exclude_prefixes):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            snap[rel] = {"size": st.st_size, "sha256": sha256_file(p)}
    return snap


def judge_changes(before, after, files, dirs):
    """把「哪里变了」对到白名单上。

    新增 / 删除 / 内容变化 都算改动（mtime 单点不动不算——那不是内容）。
    """
    changed = []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b is None:
            kind = "added"
        elif a is None:
            kind = "removed"
        elif a["sha256"] != b["sha256"]:
            kind = "modified"
        else:
            continue
        changed.append({"path": rel, "kind": kind,
                        "size_before": None if b is None else b["size"],
                        "size_after": None if a is None else a["size"]})

    def in_whitelist(rel):
        if rel in files:
            return True
        for d in dirs:
            if rel == d or rel.startswith(d + "/"):
                return True
        return False

    for c in changed:
        c["whitelisted"] = in_whitelist(c["path"])

    breaches = [c for c in changed if not c["whitelisted"]]
    return {
        "changed": changed,
        "whitelisted_count": len(changed) - len(breaches),
        "breach_count": len(breaches),
        "breaches": breaches,
        "whitelist_files": sorted(files),
        "whitelist_dirs": sorted(dirs),
    }


# ------------------------------------------------------------------ 调用

def call_engine(argv, prompt, env, timeout, root):
    """跑一次引擎。返回 (exit_code, stdout_bytes, stderr_bytes, elapsed, timed_out)。

    stdin 给 DEVNULL：codex 会提示 "Reading additional input from stdin..."，
    我们不用 stdin 传 prompt（argv 更稳，实测中文完好）。
    """
    t0 = time.time()
    p = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(root),
    )
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate()      # 摘下已缓冲的部分——超时不该把证据一起丢掉
    return p.returncode, out or b"", err or b"", round(time.time() - t0, 1), timed_out


def parse_codex_events(raw: bytes):
    """从 JSONL 事件流里抽统计数据。**只取 ASCII 字段**，中文文本不进判据。

    `commands` 是 `TRANSCRIPT.log` 段 2 的素材：`item.started` / `item.completed` 里的
    `command_execution` 自带 `command` / `aggregated_output` / `exit_code` / `status`。
    只有 `started` 没有 `completed` 的，说明命令跑到一半就断了（超时/被杀）——
    那条**留着**并标 `status="started"`，因为「跑到哪一步断的」比「什么都没记」有用得多。
    """
    usage = {}
    unparsed = 0
    agent_messages = 0
    commands = []
    seen = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            unparsed += 1
            continue
        etype = ev.get("type") or ""
        item = ev.get("item") or {}
        it_type = item.get("type")
        if etype == "turn.completed":
            usage = ev.get("usage") or {}
        elif etype == "item.completed":
            if it_type == "agent_message":
                agent_messages += 1
            elif it_type == "command_execution":
                cid = item.get("id") or ("anon-%d" % len(commands))
                rec = {
                    "id": cid,
                    "command": item.get("command") or "",
                    "output": item.get("aggregated_output") or "",
                    "exit_code": item.get("exit_code"),
                    "status": item.get("status") or "completed",
                }
                if cid in seen:
                    commands[seen[cid]] = rec
                else:
                    seen[cid] = len(commands)
                    commands.append(rec)
        elif etype == "item.started" and it_type == "command_execution":
            cid = item.get("id") or ("anon-%d" % len(commands))
            if cid not in seen:
                seen[cid] = len(commands)
                commands.append({
                    "id": cid,
                    "command": item.get("command") or "",
                    "output": "",
                    "exit_code": None,
                    "status": "started",
                })
    return {"usage": usage, "event_lines_unparsed": unparsed,
            "agent_messages": agent_messages, "commands": commands}


def parse_claude_result(raw: bytes):
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {"parse_error": True}
    return {"usage": body.get("usage") or {}, "num_turns": body.get("num_turns"),
            "cost_usd": body.get("total_cost_usd"), "is_error": bool(body.get("is_error"))}


def quota_matches(engine, out: bytes, err: bytes):
    """只观察 stderr 和引擎结构化错误；文档/命令输出/agent 正文不作额度信号。

    错误格式未知或非 JSON 的 stdout 不猜；这可能漏报，但不会把正常读文档当撞顶。
    """
    parts = [err.decode("utf-8", "replace")]
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        candidates = []
        if engine == "codex":
            if event.get("type") in ("error", "turn.failed"):
                candidates.append(event)
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "error":
                candidates.append(item)
        elif event.get("is_error") is True:
            # Claude --output-format json 的错误结果；正常 result 正文不扫描。
            candidates.append({k: event[k] for k in ("error", "errors", "result") if k in event})
        for error in candidates:
            for key in ("message", "code", "error", "errors", "status_code", "result"):
                if key in error:
                    parts.append(json.dumps(error[key], ensure_ascii=False))
    return QUOTA_RE.findall("\\n".join(parts))


# ------------------------------------------------------------------ main

# ------------------------------------------------------------------ 取证渲染
#
# 本单元（`DOC-SYNC-2026-11-11`）的核心：把「四件取证」里的三件从**工人的作业**
# 改成**执行器的产出**。判断标准只有一条 —— 谁手上真的有那份数据：
#   * 命令流水只有执行进程看得见（模型拿不到自己的完整命令记录）；
#   * 快照与改动判定本来就在本脚本里算出来（`SNAPSHOT-*.json` / `CHANGES-DETECTED.json`）；
#   * `VERIFY.md` 要回答「你改的每一处对应 §三 的哪条完成条件」，那只有工人自己知道 —— 仍归它。
# 所以 `ARTIFACT_FILES` 四个名字**一个都没改**，改的是「谁交」。

MAX_TRANSCRIPT_STDERR = 512 * 1024   # 段 3 上限；超出截断并指回 STDERR.log


def _stamp(t):
    """相对秒 + 钟点。相对秒回答「花了多久」，钟点回答「什么时候」。"""
    return "+%6.2fs %s" % (t - _T0, time.strftime("%H:%M:%S", time.localtime(t)))


def _tcell(text):
    """表格单元格转义：竖线会把 Markdown 表格切碎，换行会把行拆开。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_transcript(inc, engine, commands, stderr_bytes):
    """`TRANSCRIPT.log`：执行器流水 + 引擎命令事件 + 引擎 stderr。"""
    L = []
    ap = L.append
    ap("# TRANSCRIPT.log · %s" % inc["id"])
    ap("")
    ap("> **本文件由 `scripts/fire-chief.py` 自动生成，不是救火队长写的**（%s）。"
       % time.strftime("%Y-%m-%dT%H:%M:%S"))
    ap(">")
    ap("> 为什么机器写：`INC-TEMPLATE.md` §五 要的是「命令 + 输出 + 退出码，时间顺序」——")
    ap("> 那是**执行进程**视角的观测。模型拿不到自己的完整命令流水，派给它只有两种结局：")
    ap("> 交不出来（`exit 6`，本次救火作废），或拿 PowerShell 自己拼（转码，")
    ap("> `INC-20261111-03` 被判 blocking 的那个动作）。")
    ap(">")
    ap("> 三段：① 执行器流水 ② 引擎命令事件 ③ 引擎 stderr。**不含 agent 的自然语言发言** ——")
    ap("> 那是自述不是证据，原文在 `STDOUT.log`。")
    ap("")
    ap("## 段 1 · 执行器流水")
    ap("")
    ap("```")
    for e in _TRACE:
        block = e["text"].splitlines() or [""]
        ap("%s [%s] %s" % (_stamp(e["t"]), e["stream"], block[0]))
        for extra in block[1:]:
            ap(" " * 25 + extra)
    ap("```")
    ap("")
    ap("## 段 2 · 引擎命令事件（%d 条）" % len(commands))
    ap("")
    if engine != "codex":
        ap("（`--engine %s` 不提供命令级轨迹：它回的是单个 JSON，没有逐条 `command_execution`。）" % engine)
        ap("本段为空是**引擎能力边界，不是没跑命令**。命令级证据在段 1 的执行器流水里。")
        ap("")
    elif not commands:
        ap("（事件流里没有 `command_execution` —— 这一轮引擎没跑命令，或事件格式变了。）")
        ap("⚠️ 后者要当心：**本段为空不等于「它没干活」**，`STDOUT.log` 里能看出它到底发了什么事件。")
        ap("")
    else:
        ap("> 事件流**不带逐条时间戳**，所以本段按事件顺序编号，不编时间。")
        ap("> `status=started` 表示这条命令**跑到一半就断了**（超时/被杀），比「什么都没记」有用。")
        ap("")
        for i, c in enumerate(commands, 1):
            ec = "?" if c.get("exit_code") is None else c.get("exit_code")
            ap("### cmd %d · %s · exit=%s" % (i, c.get("status") or "?", ec))
            ap("")
            ap("```")
            ap("$ " + (c.get("command") or "（命令文本缺失）"))
            ap("```")
            out = (c.get("output") or "").rstrip("\n")
            if out:
                ap("输出：")
                ap("")
                ap("```")
                ap(out)
                ap("```")
            else:
                ap("输出：（空）")
            ap("")
    ap("## 段 3 · 引擎 stderr（%d 字节）" % len(stderr_bytes))
    ap("")
    if not stderr_bytes:
        ap("（空）")
    else:
        body = stderr_bytes
        if len(body) > MAX_TRANSCRIPT_STDERR:
            body = body[:MAX_TRANSCRIPT_STDERR]
            ap("> ⚠️ 只贴前 %d KB，完整原文见同目录 `STDERR.log`。" % (MAX_TRANSCRIPT_STDERR // 1024))
            ap("")
        ap("```")
        ap(body.decode("utf-8", "replace").rstrip("\n"))
        ap("```")
    return "\n".join(L) + "\n"


def render_changes_md(inc, judge):
    """`CHANGES.md`：改动清单 + 白名单判定。**判断类的那半留给 `VERIFY.md`。**"""
    L = []
    ap = L.append
    ap("# CHANGES.md · %s" % inc["id"])
    ap("")
    ap("> 由 `scripts/fire-chief.py` 从 `CHANGES-DETECTED.json` 渲染（%s）。"
       % time.strftime("%Y-%m-%dT%H:%M:%S"))
    ap(">")
    ap("> 这里只有**机器判定的改动**（sha256 变了才算，只动 mtime 不算）。")
    ap("> 「每个改动对应 §三 的哪条完成条件、为什么这么改」是判断，机器不猜 ——")
    ap("> 那部分在 `VERIFY.md` 里逐条回答。")
    ap("")
    ap("| 项 | 值 |")
    ap("|---|---|")
    ap("| 工作树 | `%s` |" % _tcell(inc["work_dir"]))
    ap("| 改动 | %d 个 |" % len(judge["changed"]))
    ap("| 白名单内 | %d |" % judge["whitelisted_count"])
    ap("| 越界 | **%d** |" % judge["breach_count"])
    ap("")
    ap("白名单：%d 个文件 + %d 个目录" % (len(judge["whitelist_files"]), len(judge["whitelist_dirs"])))
    ap("")
    for f in judge["whitelist_files"]:
        ap("- `%s`" % f)
    for d in judge["whitelist_dirs"]:
        ap("- `%s/**`" % d)
    ap("")
    ap("## 一、白名单内改动（%d）" % judge["whitelisted_count"])
    ap("")
    wl = [c for c in judge["changed"] if c["whitelisted"]]
    if not wl:
        ap("（无）")
    else:
        ap("| 路径 | 类型 | 字节（前 → 后） |")
        ap("|---|---|---|")
        for c in wl:
            ap("| `%s` | %s | %s → %s |" % (
                _tcell(c["path"]), c["kind"],
                "-" if c["size_before"] is None else c["size_before"],
                "-" if c["size_after"] is None else c["size_after"]))
    ap("")
    ap("## 二、越界改动（%d）—— 必须为 0" % judge["breach_count"])
    ap("")
    if not judge["breaches"]:
        ap("（无）")
    else:
        ap("⚠️ **白名单外有改动。** 这类改动判 `exit 5` —— 出口码优先级是")
        ap("「越界 > 调用失败 > 四件不齐 > 全过」，因为越界是**不可洗**的：")
        ap("先判引擎失败的话，一次「引擎挂了 + 顺手写了白名单外」会被记成 `exit 3`，")
        ap("而规则规定第 1 次救火失败要再派第 2 次 —— 那条越界就被第 2 次洗掉了。")
        ap("")
        ap("| 路径 | 类型 | 字节（前 → 后） |")
        ap("|---|---|---|")
        for c in judge["breaches"]:
            ap("| `%s` | %s | %s → %s |" % (
                _tcell(c["path"]), c["kind"],
                "-" if c["size_before"] is None else c["size_before"],
                "-" if c["size_after"] is None else c["size_after"]))
    return "\n".join(L) + "\n"


def render_snapshot_txt(inc, before, after, judge):
    """`SNAPSHOT.txt`：快照摘要与差异计数；全量 sha256 留在两个 `.json` 里。"""
    L = []
    ap = L.append
    ap("# SNAPSHOT.txt · %s" % inc["id"])
    ap("")
    ap("> 由 `scripts/fire-chief.py` 从 `SNAPSHOT-BEFORE.json` / `SNAPSHOT-AFTER.json` 渲染（%s）。"
       % time.strftime("%Y-%m-%dT%H:%M:%S"))
    ap(">")
    ap("> 全量 sha256 在两个 `.json` 里；本文件只给摘要与差异计数，")
    ap("> 免得四件取证里出现两份全量树（两份就会漂，漂了就没人知道哪份对）。")
    ap("> 快照是**全树 sha256**，不是 mtime + 字节数 —— 同一秒内、大小不变的改动它也看得见。")
    ap("")
    tb = sum(v["size"] for v in before.values())
    ta = sum(v["size"] for v in after.values())
    ap("| 项 | 跑前 | 跑后 |")
    ap("|---|---|---|")
    ap("| 文件数 | %d | %d |" % (len(before), len(after)))
    ap("| 总字节 | %d | %d |" % (tb, ta))
    ap("")
    ap("| 差异 | 数 |")
    ap("|---|---|")
    for kind, label in (("added", "新增"), ("removed", "删除"), ("modified", "内容变化")):
        ap("| %s | %d |" % (label, sum(1 for c in judge["changed"] if c["kind"] == kind)))
    ap("")
    ap("排除区（不在快照内）：`node_modules/` `.git/` `__pycache__/` `.pytest_cache/`")
    ap("`runs/_harness/` · 本单产物目录 `%s/`。" % inc["prod_dir_rel"])
    ap("⚠️ **排除区里的写入看不见** —— 这是已知盲区，不是「一定没写」。")
    ap("工程树之外的写入（`~/.dsh` / `%TEMP%` / 别的盘）同样看不见。")
    ap("")
    if judge["changed"]:
        ap("## 变更明细")
        ap("")
        ap("| 路径 | 类型 | 白名单 |")
        ap("|---|---|---|")
        for c in judge["changed"]:
            ap("| `%s` | %s | %s |" % (
                _tcell(c["path"]), c["kind"], "是" if c["whitelisted"] else "**否**"))
        ap("")
    else:
        ap("跑前跑后**零差异**：这一轮没有落盘改动。")
        ap("")
    return "\n".join(L) + "\n"


def write_machine_artifacts(inc, engine, commands, stderr_bytes, before, after, judge):
    """写 `TRANSCRIPT.log` / `CHANGES.md` / `SNAPSHOT.txt`，返回写了的文件名。

    **引擎失败与超时也要写。** 半成功最需要知道两件事：改了哪里、跑到哪一步断的。
    """
    prod = inc["prod_dir"]
    write_utf8(prod / "TRANSCRIPT.log", render_transcript(inc, engine, commands, stderr_bytes))
    write_utf8(prod / "CHANGES.md", render_changes_md(inc, judge))
    write_utf8(prod / "SNAPSHOT.txt", render_snapshot_txt(inc, before, after, judge))
    return list(MACHINE_ARTIFACTS)


def main():
    _reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description="救火队长执行器：事故单 → 一次可审计的救火调用（egress 硬拦 / 快照对账 / 取证体检）")
    ap.add_argument("inc", help="事故单路径（INC-template v1.2 填好的实例）")
    ap.add_argument("--engine", choices=("codex", "claude"), default="codex",
                    help="执行体通道。codex = ChatGPT 订阅（默认，gpt-6.2-astra）；claude = Claude 订阅（opus）")
    ap.add_argument("--model", default=None, help="默认 codex→gpt-6.2-astra / claude→opus")
    ap.add_argument("--effort", default="xhigh", help="codex 的 model_reasoning_effort（默认 xhigh；请求值，非服务端确认值）")
    ap.add_argument("--timeout", type=int, default=3600,
                    help="单次调用超时秒数（默认 3600；上次真实救火 24 分 11 秒）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析事故单、打印 prompt / 白名单 / argv，不调引擎、不快照、不建目录")
    ap.add_argument(
        "--skip-render", action="store_true",
        help="【测试钩子】删除同名机器三件并跳过渲染，在 META.artifacts 留 skip_render 痕迹。"
             "正常引擎且无越界时必然 exit 6；引擎失败 / 越界仍优先返回 3 / 5。"
             "正常派遣不许用。",
    )
    ap.add_argument(
        "--egress-check", default=None,
        help="【测试钩子】指定 egress 自检脚本。判据仍是「输出含 [result] READY **且** 退出码 0」，"
             "两条缺一即 exit 4。没有跳过开关。",
    )
    ap.add_argument(
        "--root", default=str(ROOT),
        help="【测试钩子】工程根（默认按本脚本位置推断）。改它只改快照与调用的工作根，不是跳过检查。",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        die(EXIT_INC, "--root 不存在：%s" % root)

    inc = parse_inc(Path(args.inc), root)
    say("[fire-chief] 事故单  %s" % inc["id"])
    say("[fire-chief] 触发    %s（%s）" % (inc["trigger"], inc["trigger_raw"]))
    say("[fire-chief] 工作目录 %s" % inc["work_dir"])
    say("[fire-chief] 白名单  %d 个文件 + %d 个目录（产物目录 %s）"
        % (len(inc["whitelist_files"]), len(inc["whitelist_dirs"]), inc["prod_dir_rel"]))
    for f in sorted(inc["whitelist_files"]):
        say("    [file] " + f)
    for d in sorted(inc["whitelist_dirs"]):
        say("    [dir ] " + d + "/**")

    # 2026-11-12（CLAUDE-TO-GPT，R4）：Claude 通道已不可用（portal 的 Persona KYC 把 `-p` 挡住，
    # 伙伴决定不做 KYC）。**显式拒绝**，不静默降级 —— 静默降级会让人以为跑的是 opus。
    if args.engine == "claude":
        die(EXIT_INC,
            "Claude 通道已不可用：portal.anthropic.com 的 Persona KYC 挡住了 `-p`，"
            "2026-11-12 决定不做 KYC，该通道不会恢复。\n"
            "  救火队长请用默认的 --engine codex（GPT-6 Astra，ChatGPT 订阅，无按量费用）。\n"
            "  依据：tasks/CLAUDE-TO-GPT-2026-11-12/TASK.md §二·R4")
    model = args.model or "gpt-6.2-astra"
    codex_js = find_codex() if args.engine == "codex" else None
    claude_exe = find_claude() if args.engine == "claude" else None

    inc_as_fed = inc["prod_dir"] / "INC-AS-FED.md"
    prompt = build_prompt(inc, inc_as_fed)
    argv, meta = build_argv(args.engine, codex_js, claude_exe, model, args.effort, root, prompt)
    env, proxy_port = proxy_env()
    meta["proxy_port"] = proxy_port
    # N3（第 1 轮验收官）：`--root` 是测试钩子，也能把对账核到另一棵树上。
    # 不改判据，但会改「判据看的是谁」——所以必须让验收官一眼看出核的是哪棵树。
    meta["root"] = {"path": root.as_posix(), "is_default": root == ROOT.resolve()}
    if not meta["root"]["is_default"]:
        say("[fire-chief] [warn] --root 不是默认工程根：对账核的是 %s" % root.as_posix())

    if args.dry_run:
        say("")
        say("[fire-chief] dry-run：不调引擎、不快照、不建目录。")
        say("[fire-chief] engine  = %s / model = %s / effort = %s" % (args.engine, model, args.effort))
        say("[fire-chief] codex   = %s" % (codex_js or "(不用)"))
        say("[fire-chief] claude  = %s" % (claude_exe or "(不用)"))
        say("[fire-chief] 产物目录 = %s" % inc["prod_dir"])
        say("[fire-chief] egress  = %s" % (Path(args.egress_check) if args.egress_check else DEFAULT_EGRESS[args.engine]))
        say("--- argv（prompt 已就位，这里只显示前 %d 项与末项是否为 prompt）---" % (len(argv) - 1))
        for i, a in enumerate(argv[:-1]):
            say("  argv[%d] = %s" % (i, a))
        say("  argv[%d] = <prompt %d 字符>" % (len(argv) - 1, len(prompt)))
        say("--- prompt ---")
        say(prompt)
        return

    # dry-run 只解析，因此可复核已有事故单；真实派遣不能复用非空证据目录。
    if inc["prod_dir"].exists() and (
            not inc["prod_dir"].is_dir() or any(inc["prod_dir"].iterdir())):
        die(EXIT_INC, "产物目录已存在且非空或不是目录，请使用新事故编号：%s" % inc["prod_dir"])

    # --- egress 硬拦：放在建目录之前，失败不留半截证据包 ---
    egress = check_egress(args.engine, args.egress_check)
    meta["egress"] = egress

    # --- 喂入副本：UTF-8 with BOM ---
    # 为什么留副本而不是直接喂原件：PS 5.1 对**无 BOM** 的 UTF-8 会按 936 解码；
    # BOM 是那个场景的兜底。原件一字节不动，副本的 sha256 记进 META，可对账。
    inc["prod_dir"].mkdir(parents=True, exist_ok=True)
    write_utf8(inc_as_fed, "\ufeff" + inc["text"])

    # 校验**读回盘上的副本**再比，而不是比内存里的 bytes（第 1 轮验收官 N2：比内存测不出「副本写坏了」）。
    # 原件也可能自带 BOM（PowerShell 的 Out-File 默认就带），所以两边都先剥 BOM 再比。
    fed_disk = inc_as_fed.read_bytes()
    src_disk = inc["path"].read_bytes()
    BOM = b"\xef\xbb\xbf"
    fed_body = fed_disk[len(BOM):] if fed_disk.startswith(BOM) else fed_disk
    src_body = src_disk[len(BOM):] if src_disk.startswith(BOM) else src_disk
    consistent = sha256_bytes(fed_body) == sha256_bytes(src_body)
    meta["inc"] = {
        "path": inc["path"].as_posix(),
        "sha256": sha256_bytes(src_disk),
        "sha256_body": sha256_bytes(src_body),
        "source_had_bom": src_disk.startswith(BOM),
        "as_fed": inc_as_fed.as_posix(),
        "as_fed_sha256": sha256_bytes(fed_disk),
        # 副本去掉 BOM 后必须与原件（同样去掉 BOM）字节一致。不一致就是副本写坏了 ——
        # 那意味着救火队长读到的不是我们审过的那份事故单。
        "as_fed_strips_to_source": consistent,
    }
    if not consistent:
        # 兑现文件头「失败不留半截证据包」：只删**本次**写的这一个文件，目录要是空了就一并收掉。
        # （不能整目录递归删 —— 重跑时那里面是上一轮的证据。）
        try:
            inc_as_fed.unlink()
            inc["prod_dir"].rmdir()
        except OSError:
            pass
        die(EXIT_INC, "喂入副本去掉 BOM 后与原件 sha256 不一致 —— 副本写坏了，不调引擎。")

    # --- 跑前快照 ---
    excl = [inc["prod_dir_rel"] + "/"]
    say("")
    say("[fire-chief] 跑前快照…")
    before = snapshot(root, excl)
    write_utf8(inc["prod_dir"] / "SNAPSHOT-BEFORE.json",
               json.dumps(before, ensure_ascii=False, indent=1) + "\n")
    say("[fire-chief] 快照 %d 个文件" % len(before))

    # --- 调引擎 ---
    say("")
    say("[fire-chief] ---- 调 %s（%s）----" % (args.engine, model))
    rc, out, err, elapsed, timed_out = call_engine(argv, prompt, env, args.timeout, root)

    (inc["prod_dir"] / ("CODEX-EVENTS.jsonl" if args.engine == "codex" else "CLAUDE-RESULT.json")).write_bytes(out)
    (inc["prod_dir"] / "STDOUT.log").write_bytes(out)
    (inc["prod_dir"] / "STDERR.log").write_bytes(err)

    quota_hits = quota_matches(args.engine, out, err)

    stats = parse_codex_events(out) if args.engine == "codex" else parse_claude_result(out)
    meta.update({
        "exit_code": rc,  # 兼容既有消费者：仍为引擎退出码
        "engine_exit_code": rc,
        "timed_out": timed_out,
        "elapsed_s": elapsed,
        "quota_signal": {"hit": bool(quota_hits), "matches": sorted(set(m.lower() for m in quota_hits))},
        "stats": stats,
    })
    say("[fire-chief] 引擎退出码 %s / 耗时 %ss%s" % (rc, elapsed, "（超时）" if timed_out else ""))
    say("[fire-chief] tokens: %s" % json.dumps(stats.get("usage") or {}, ensure_ascii=False))
    if quota_hits:
        say("[fire-chief] [warn] 撞顶信号：" + ", ".join(sorted(set(m.lower() for m in quota_hits))))

    # --- 跑后快照 + 对账（引擎失败也照做：半成功最需要知道改了哪里）---
    say("")
    say("[fire-chief] 跑后快照…")
    after = snapshot(root, excl)
    write_utf8(inc["prod_dir"] / "SNAPSHOT-AFTER.json",
               json.dumps(after, ensure_ascii=False, indent=1) + "\n")
    judge = judge_changes(before, after, inc["whitelist_files"], inc["whitelist_dirs"])
    write_utf8(inc["prod_dir"] / "CHANGES-DETECTED.json",
               json.dumps(judge, ensure_ascii=False, indent=1) + "\n")
    say("[fire-chief] 改动 %d 个（白名单内 %d / 越界 %d）"
        % (len(judge["changed"]), judge["whitelisted_count"], judge["breach_count"]))
    for c in judge["changed"]:
        say("    [%s] %s %s" % ("ok " if c["whitelisted"] else "XX ", c["kind"], c["path"]))

    # 出口码的**优先级**（第 1 轮验收官 B1 改的）：越界 > 调用失败 > 四件不齐 > 全过。
    # 为什么越界优先：越界是「不可洗」的重罪 —— 与 `runner.py` 的 `harm` 不可洗同一原则。
    # 反过来说，如果先判 `rc != 0`，一次「引擎自己挂了 + 顺手写了白名单外」就会被记成 exit 3，
    # 越界在账面上消失了；而新规则（`TRIGGER-POLICY.md` §二·五·一）恰好规定
    # 「第 1 次救火失败 → 还派第 2 次」，于是这条越界会被第 2 次调用洗掉。
    if judge["breach_count"]:
        code = EXIT_BREACH
    elif timed_out or rc != 0:
        code = EXIT_CALL
    else:
        code = EXIT_OK

    # --- 三件取证由本脚本渲染（生死在这里：渲染放在体检**之前**）---
    # 顺序很重要：先写盘，再体检。反过来的话，体检看到的永远是「上一轮的文件」。
    say("")
    say("[fire-chief] 渲染取证三件（%s）…" % " / ".join(MACHINE_ARTIFACTS))
    if args.skip_render:
        # 先删除工人写的同名件，防止伪造件通过缺件体检并进入哈希清单（NB1）。
        prod = inc["prod_dir"].resolve()
        for name in MACHINE_ARTIFACTS:
            artifact_path = prod / name
            assert artifact_path.parent.resolve() == prod
            artifact_path.unlink(missing_ok=True)
        say("[fire-chief] [warn] --skip-render（测试钩子）：已删除同名机器三件，跳过渲染并在 META 留痕。")
        written = []
    else:
        written = write_machine_artifacts(
            inc, args.engine, stats.get("commands") or [], err, before, after, judge)
    for name in written:
        say("    [written] %s" % name)

    # --- 四件体检 ---
    # 2026-11-11 起四件的「分工」写死在 `ARTIFACT_FILES` 上方：前三件机器写、`VERIFY.md` 工人写。
    # 所以这里缺件只有两种可能 —— 机器三件缺 = 本脚本的 bug；`VERIFY.md` 缺 = 工人没交作业。
    # 两者必须分开报，否则又回到「分不清是契约错配还是工人失误」的老毛病。
    missing = [f for f in ARTIFACT_FILES if not (inc["prod_dir"] / f).is_file()]
    machine_missing = [f for f in missing if f in MACHINE_ARTIFACTS]
    worker_missing = [f for f in missing if f in WORKER_ARTIFACTS]
    meta["artifacts"] = {
        "dir": inc["prod_dir_rel"],
        "required": list(ARTIFACT_FILES),
        "missing": missing,
        "missing_machine": machine_missing,
        "missing_worker": worker_missing,
        "skip_render": args.skip_render,
        "written_by": {} if args.skip_render else dict(
            [(f, "fire-chief.py") for f in MACHINE_ARTIFACTS]
            + [(f, "救火队长") for f in WORKER_ARTIFACTS]
        ),
    }
    if code == EXIT_OK and missing:
        code = EXIT_ARTIFACTS

    # --- META + N2 产物 sha256 ---
    meta["judge"] = {k: judge[k] for k in ("whitelisted_count", "breach_count", "breaches")}
    meta["fire_chief_exit"] = code
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_utf8(inc["prod_dir"] / "META.json",
               json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    prod_sha = {}
    for p in sorted(inc["prod_dir"].rglob("*")):
        if p.is_file() and p.name != "PRODUCT-SHA256.txt":
            prod_sha[rel_posix(p, inc["prod_dir"])] = sha256_file(p)
    write_utf8(inc["prod_dir"] / "PRODUCT-SHA256.txt",
               "\n".join("%s  %s" % (h, rel) for rel, h in prod_sha.items()) + "\n")

    # --- 结论 ---
    say("")
    say("[fire-chief] ---- 结论 ----")
    say("  事故单   %s（%s）" % (inc["id"], inc["trigger"]))
    say("  引擎     %s / %s / exit %s / %ss%s"
        % (args.engine, model, rc, elapsed, "（超时）" if timed_out else ""))
    say("  改动     %d 个，越界 %d 个" % (len(judge["changed"]), judge["breach_count"]))
    say("  四件     %s" % ("齐" if not missing else "缺 " + ", ".join(missing)))
    if missing:
        say("           机器缺 %s / 工人缺 %s" % (machine_missing or "无", worker_missing or "无"))
    say("  产物     %s/（%d 文件，sha256 已存 PRODUCT-SHA256.txt）" % (inc["prod_dir_rel"], len(prod_sha)))
    say("  下一步   体检 %s → 送标准验收官（Opus 5.5，带 shell）出裁决 → 账本记一行"
        % ("过" if code == EXIT_OK else "不过"))

    if code == EXIT_CALL:
        # N3（第 1 轮验收官）：引擎失败与机器缺件同时发生时，`exit 3` 会把「脚本 bug」盖掉 ——
        # `META.artifacts.missing_machine` 里有记录，但没人会去看。出口码不变
        # （「调用失败优先于四件不齐」是既有优先级），只补一条人读提示。
        if machine_missing:
            print("[fire-chief] ⚠️ 另有机器缺件（%s）—— 那是本脚本的 bug，与上面的引擎失败是两件事。"
                  % ", ".join(machine_missing), file=sys.stderr, flush=True)
        die(EXIT_CALL, "引擎调用失败%s。证据包仍在 %s/。" % ("（超时 %ss）" % args.timeout if timed_out else "", inc["prod_dir_rel"]))
    if code == EXIT_BREACH:
        print("[fire-chief] 越界清单：", file=sys.stderr)
        for c in judge["breaches"]:
            print("  XX %s %s" % (c["kind"], c["path"]), file=sys.stderr)
        die(EXIT_BREACH, "白名单外有 %d 处改动 —— 交付包体检**不过**，不许送审。" % judge["breach_count"])
    if code == EXIT_ARTIFACTS:
        # 分开报：机器缺件是本脚本的 bug（去修渲染，别去补文件）；
        # 工人缺件才是救火队长没交作业（§5.6：重跑或如实记缺陷，不许事后造）。
        if machine_missing:
            die(EXIT_ARTIFACTS,
                "四件取证不齐：机器该写的 %s 没写出来 —— 这是本脚本（`scripts/fire-chief.py`）的 bug，"
                "去修渲染，**不要补文件**。缺件：%s" % (", ".join(machine_missing), ", ".join(missing)))
        die(EXIT_ARTIFACTS,
            "四件取证不齐：缺 %s —— 那是救火队长的作业（`VERIFY.md`），不许事后造（`INC-TEMPLATE.md` §5.6）。"
            % ", ".join(missing))
    return


if __name__ == "__main__":
    main()
