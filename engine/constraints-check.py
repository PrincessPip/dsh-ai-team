#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constraints-check.py — 出处闸门：条款必须带出处，或者带一条**已批准的豁免条目**。

不是禁止写规矩，是禁止「无出处地拿规矩当武器」（`decisions/CONSTRAINTS.json` 的 `P-001`）。

## 第 5 轮（2026-11-18，伙伴批准 C 方案）：判据里不再有语义词表

前 4 轮都在猜：「这句话是证据条款还是产品限制？」—— 启发式猜（1–3 轮）、白名单猜（4 轮），
全都被验收官用新说法穿过去。第 4 轮 9 个反例（`产品副本` / `门锁` / `产品：…` / `成功提示按钮` /
`绿色主题` / `且` 连接）证明：**只要靠句子表面判断意思，词表多长都会漏**。

第 5 轮把「语义判断」从机器挪走：

    条款行自己声明它属于哪条豁免 → 闸门只核「有标签吗 → 表里在吗 → 批准了吗」

判定只有三步，**一句自然语言都不读**：

    1. 这一行有 [C:E-00x] / [C:I-00x] / [C:P-00x] 吗？没有 → 命中 baseline 就放行，否则 P（FAIL）
    2. 有 → 去 `exemptions`（E/I）或 `entries`（P）查这个 id
    3. 查得到且 status=approved、quote 非空 → 放行；否则 UNKNOWN-ID / NOT-APPROVED

句子写多长、用什么词、有没有「门锁 / 产品副本 / 且」，**全都无关** —— 这就是它不可能被塞词穿透的原因。

**豁免也是要登记的**：前 4 轮的病根是「闸门自己给自己发豁免」，现在是「豁免清单由伙伴批准」
（`decisions/CONSTRAINTS.json` 的 `exemptions`，10 条，2026-11-18 批准；
草案与理由见 `tasks/CONSTRAINTS-2026-11-17/round5-plan/EXEMPTIONS-DRAFT.md`）。
新增豁免条目才需要再找伙伴；已有条目长期有效。

`SUGGEST` 那张表**只产出报错里的「建议 id」，不参与判定** —— 变异台 `M32` 把它接进判定后，
第 4 轮那批穿透反例里立刻有 5 个被放行。它是给人省事用的，不是判据。

## 保留的东西（前四轮的资产，一个字没动）

- 抽取面：禁限词前置边界、副词前缀、`PRESCRIPTIVE_RE`（规定式句子）、`code_mask`（围栏 / 缩进块）、
  `~~` 划线行、`>` 引用块、24 万字符长行、扫描面与排除目录 —— 全部原样。
- baseline 的语义：**存量登记，不是免检通道**。新文件 / 新条款 / 改写过措辞的条款不在表内，必红。
- `--defs`：核 `classes[*].definition` 在 `AGENTS.md` 与 `TRIGGER-POLICY.md` 里逐字一致（三处定义漂移）。
- 报错三行化（为什么 / 怎么改这句 / 原则）+ 末尾总则；理念句从 `P-001.guidance` 读，不硬编码。
- 数据台账：`--inventory`（带原文）+ 累积日志 + `--stats`。

## 用法

    python scripts/constraints-check.py --changed <本单元改动清单.txt>
    python scripts/constraints-check.py --changed HANDOVER.md tasks/X/TASK.md
    python scripts/constraints-check.py --write-baseline        # 重建存量登记
    python scripts/constraints-check.py --all --out out.txt     # 全仓体检
    python scripts/constraints-check.py --all --inventory       # 顺手刷全仓条款台账
    python scripts/constraints-check.py --stats                 # 从累积日志出频次报告
    python scripts/constraints-check.py --defs                  # 核三处 E/I/P 定义逐字一致
    python scripts/constraints-check.py --list-exemptions       # 印出豁免条目表

退出码：0 OK/SKIP · 1 FAIL · 2 用法错（含空清单）· 3 常量表坏了
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "decisions" / "CONSTRAINTS.json"
DEFAULT_BASELINE = ROOT / "scripts" / "constraints-baseline.txt"
DEFAULT_INVENTORY = ROOT / "decisions" / "constraints-inventory.jsonl"
DEFAULT_LOG = ROOT / "decisions" / "constraints-log.jsonl"

GUIDANCE_ID = "P-001"

# 生成物目录：扫它们只会把同一批条文重复算一遍
EXCLUDE_DIR_PARTS = {
    "node_modules", ".git", "runs", "live-products", "isolated", "backup",
    "tmp", "artifacts", "NOTES-archive",
}
EXCLUDE_DIR_SUFFIX = ("-before", "-before-round1", "-before-round2", "-raw", "-backup")

MAX_LINE = 240
LOG_MAX_ROWS = 3000

# --- 抽取面（第 1–4 轮资产，原样保留）--------------------------------------
# 只是「出现了禁止词」还不够，还得是**规定式**的句子（列表项 / 条款句），
# 否则叙述文里那些「不匹配 → 不重试」的现象描述会被大量误抓。
PROHIBIT_WORDS = (
    r"禁止|严禁|不得|不许|不允许|不可|切勿|勿|不要|禁用|不隐藏|"
    r"不把|不用于|不作为|不开放|不恢复|不新增|不删除|不复制|不导入|"
    r"不动|不改|不换|不申请|不重启|不另开|不自动|不依赖|不承诺|不冒称|不删|"
    # 「不 + 动作」这一族（不写 / 不跑 / 不执行 …）也要收 ——
    # 否则「测试只在临时目录跑，不写主树」这种条款连抽都抽不到（第 4 轮 T26 抓出来的）。
    r"不写|不跑|不执行|不运行|不读取|不占用|不覆盖|不移动|不替换|不启用|不开启|不加载|"
    r"不新建|不创建|不许把|不得把"
)
# 抽取阶段要求禁限词前是行首或标点（挡掉「余额调不动」这类词内假阳性）；
# 但「产品永久禁用复制」这种句里，禁限词前是副词而不是标点 —— 漏掉它等于放过一句
# 产品面限制。所以额外放行一组**固定长度**的副词前缀（lookbehind 必须定长）。
_PROHIBIT_ALLOW_PREFIX = (
    r"(?<=永久)|(?<=一律)|(?<=全部)|(?<=严格)|(?<=彻底)|(?<=完全)|(?<=绝不)|"
    r"(?<=从不)|(?<=直接)|(?<=默认)|(?<=始终)|(?<=不再)|(?<=统一)|(?<=强制)|"
    r"(?<=全程)|(?<=任何)|(?<=所有)|(?<=禁止)"
)
PROHIBIT_RE = re.compile(r"(?:^|[\s，。；：、（）()「」·—!?\"“”]|" + _PROHIBIT_ALLOW_PREFIX +
                         r")(?:" + PROHIBIT_WORDS + ")")
PRESCRIPTIVE_RE = re.compile(
    r"^\s*(?:[-*+]\s|\d+[.)、]\s|\||#|>\s*$)|禁止|严禁|不得|不许|不允许|不可|切勿"
)

# 标签：P-001（出处）/ E-001 · I-001（豁免条目）—— 一个正则吃三种
TAG_RE = re.compile(r"\[C:([EIP]-\d{3})\]")
# 以列表标记 / 表格列 / 引用块开头 —— 这类行是**独立条款**，不向下传递标签（第 5 轮 B1）。
_LIST_OR_QUOTE_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)、]\s|\||>)")

# --- 建议器（**不参与判定**，只让报错里有个省事的建议）-------------------
# 台子 T30/变异 M32 钉住它不参与判定，断言全部判定结果不变 —— 它错了只影响「建议」那句话。
SUGGEST: list[tuple[str, re.Pattern]] = [
    ("E-001", re.compile(r"冒称|猜\s*pass|算\s*pass|unverified|算成功|当成功|当通过")),
    ("E-002", re.compile(r"伪造|补签|终裁|裁决|独立审阅")),
    ("E-003", re.compile(r"凑绿|假绿|删断言|改判据")),
    ("I-001", re.compile(r"只读")),
    ("I-002", re.compile(r"scripts/|TRIGGER-POLICY|REVIEW-CONTRACT|INC-TEMPLATE|graph-rules")),
    ("I-003", re.compile(r"本工程|主树|送审树|TEMP|临时目录|副本")),
    ("I-004", re.compile(r"artifacts|profile|海量")),
    ("I-005", re.compile(r"锁|交付目录")),
    ("I-006", re.compile(r"live|生产|网关推理")),
    ("I-007", re.compile(r"账本|runs/|更正行")),
]

# --- 代码块识别（B2）：围栏（``` 与 ~~~，按长度配对）+ 四空格/Tab 缩进块 ----
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
INDENT_CODE_RE = re.compile(r"^(?: {4}|\t)")


def suggest_exemption(text: str, registry: dict) -> str | None:
    """只给建议：这句话「像」哪条豁免。**不参与判定**（判定只看标签与表）。

    还要**只在表里真的存在时才建议** —— 否则报错会给出一个查不到的 id
    （第 5 轮自测 `T21` 就崩在这上面：沙箱表里没有 `I-005`，建议器却指出了它）。
    """
    for eid, pat in SUGGEST:
        if eid in registry["exemptions"] and pat.search(text):
            return eid
    return None


def norm(text: str) -> str:
    """规范化：折叠空白 + 去掉列表标记，让重新缩进/换 bullet 不算新行，改词才算。"""
    s = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+|\|\s*|#+\s+)", "", text.strip())
    s = re.sub(r"~~", "", s)
    return re.sub(r"\s+", " ", s).strip()


def digest(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:16]


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def code_mask(lines: list[str]) -> list[bool]:
    """标出属于代码块的行 —— 代码块是示例，不是本方订立的条款。

    围栏（CommonMark）：``` 或 ~~~，至少三个、可缩进 ≤3 空格；**关闭围栏必须同字符且
    长度 ≥ 开启长度**（短的不能关长的），关闭行后面只能有空白。第 1 轮验收官 B2 的
    `~~~` 反例就是旧版只认反引号造成的。
    缩进块（四空格 / Tab）：只在**上一行是空行或文件起始**时开启 —— 缩进代码块既不能
    中断段落，也不能是列表项的续行（那属于列表内容，仍按条款判）。
    """
    mask = [False] * len(lines)
    fence_char, fence_len = "", 0
    in_indent = False
    for i, raw in enumerate(lines):
        if fence_char:
            mask[i] = True
            m = FENCE_RE.match(raw)
            if (m and m.group(1)[0] == fence_char
                    and len(m.group(1)) >= fence_len and not m.group(2).strip()):
                fence_char, fence_len = "", 0
            continue
        m = FENCE_RE.match(raw)
        if m:
            fence_char, fence_len = m.group(1)[0], len(m.group(1))
            mask[i] = True
            in_indent = False
            continue
        if in_indent:
            if not raw.strip() or INDENT_CODE_RE.match(raw):
                mask[i] = True
                continue
            in_indent = False
        if raw.strip() and INDENT_CODE_RE.match(raw) and (i == 0 or not lines[i - 1].strip()):
            in_indent = True
            mask[i] = True
    return mask


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if parts & EXCLUDE_DIR_PARTS:
        return True
    return any(part.lower().endswith(EXCLUDE_DIR_SUFFIX) for part in path.parts)


def iter_docs() -> list[Path]:
    """默认扫描面：根下 .md + tasks/ 深度 ≤3 的 .md，跳过生成物目录。"""
    out: list[Path] = []
    for p in sorted(ROOT.glob("*.md")):
        out.append(p)
    for p in sorted(ROOT.glob("tasks/**/*.md")):
        if len(p.relative_to(ROOT).parts) > 4:  # tasks/<unit>/<sub>/<file>
            continue
        out.append(p)
    return [p for p in out if p.is_file() and not is_excluded(p)]


def extract(path: Path) -> list[tuple[int, str, str]]:
    """返回 [(行号, 原文, 上一行原文)]。

    第 3 项（`prev`）现在只供台账与 baseline 回放使用：第 7 轮起**标签只认本行**，
    跨行继承已整个砍掉（第 5–7 轮验收官用四种写法打穿过它，见 `main()` 循环里的说明）。

    **不再分类**：第 5 轮起没有 E/I/P 判定，只有「这行是不是禁限句」。
    """
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[tuple[int, str, str]] = []
    mask = code_mask(lines)
    for i, raw in enumerate(lines, 1):
        if mask[i - 1]:
            continue
        line = raw.rstrip()
        if not line or len(line) > MAX_LINE:
            continue
        if "~~" in line:          # 已划掉的条文（已废止）不再要求出处
            continue
        if line.lstrip().startswith(">"):   # 引用/证据块，不是本方订立的条款
            continue
        if not PROHIBIT_RE.search(line):
            continue
        if not PRESCRIPTIVE_RE.search(line):
            continue
        prev = lines[i - 2].strip() if i >= 2 else ""
        found.append((i, line.strip(), prev))
    return found


def load_registry() -> dict:
    if not REGISTRY.is_file():
        print(f"[gate] REGISTRY-MISSING {rel(REGISTRY)}", file=sys.stderr)
        raise SystemExit(3)
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if data.get("schema") != "constraints-v1":
        print(f"[gate] REGISTRY-SCHEMA {data.get('schema')!r}", file=sys.stderr)
        raise SystemExit(3)
    entries: dict[str, dict] = {}
    for e in data.get("entries", []):
        if not e.get("id"):
            print("[gate] REGISTRY-BAD-ENTRY 缺 id", file=sys.stderr)
            raise SystemExit(3)
        entries[e["id"]] = e
    exemptions: dict[str, dict] = {}
    for e in data.get("exemptions", []):
        if not e.get("id"):
            print("[gate] REGISTRY-BAD-EXEMPTION 缺 id", file=sys.stderr)
            raise SystemExit(3)
        if not re.fullmatch(r"[EI]-\d{3}", e["id"]):
            # 第 5 轮 B2：只查首字母会放过 `Evil` / `E001` / `I_001` —— id 必须是 E-00x / I-00x。
            print(f"[gate] REGISTRY-BAD-EXEMPTION {e['id']}：豁免条目的 id 必须是 E-00x 或 I-00x",
                  file=sys.stderr)
            raise SystemExit(3)
        exemptions[e["id"]] = e
    p001 = entries.get(GUIDANCE_ID) or {}
    guidance = p001.get("guidance")
    if not isinstance(guidance, str) or len(guidance.strip()) < 4:
        # 闸门里的理念句自己也必须有出处，否则它违反闸门自己的规则（第 4 轮 2026-11-18 伙伴批准）。
        print(f"[gate] GUIDANCE-MISSING {GUIDANCE_ID}.guidance 未登记"
              f"（报错文案的理念句要从常量表读，不许硬编码在检查器里）→ exit 3",
              file=sys.stderr)
        raise SystemExit(3)
    return {"entries": entries, "exemptions": exemptions,
            "classes": data.get("classes", {}),
            "guidance": guidance.strip(), "guidance_at": p001.get("guidance_at")}


def lookup(registry: dict, tag_id: str) -> dict | None:
    """标签 → 条目。E-/I- 去 exemptions，P- 去 entries。"""
    if tag_id.startswith("P-"):
        return registry["entries"].get(tag_id)
    return registry["exemptions"].get(tag_id)


def load_baseline(path: Path) -> dict[str, str]:
    """key = path|sha1 → 类（老格式第三列，仅作信息保留）。baseline 是存量登记，不是免检通道。"""
    table: dict[str, str] = {}
    if not path.is_file():
        return table
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        table[f"{parts[0]}|{parts[1]}"] = parts[2]
    return table


def write_baseline(rows: list[tuple[str, str, str]], path: Path) -> None:
    uniq: dict[tuple[str, str, str], None] = {}
    for r in rows:
        uniq[(r[0], r[1], r[2])] = None
    body = ["# constraints-baseline v2 —— 存量登记，不是免检通道。",
            "# 第 5 轮（C 方案）起，闸门不再分类 E/I/P：没带 [C:xxx] 标签的历史条款一律靠本表兜，",
            "# 新写的条款要么带标签（[C:E-00x] 豁免 / [C:P-00x] 出处），要么改写措辞。",
            "# 语义照抄 ledger-check：这里登记的是『历史上已经写下的条款』；",
            "# 新文件/新条款/改写过措辞的条款不在本表，必红。",
            "# 由 scripts/constraints-check.py --write-baseline 生成，勿手改。",
            "# 格式：相对路径|文本sha1|标记（v2 起第三列只记 hist，不再记 E/I/P）"]
    for p, h, c in sorted(uniq):
        body.append(f"{p}|{h}|{c}")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


# --- 报错三行化 -------------------------------------------------------------
def why_text(kind: str, vid: str | None, suggestion: str | None,
             registry: dict) -> str:
    ex_ids = "、".join(sorted(registry["exemptions"])) or "（空）"
    p_ids = "、".join(sorted(registry["entries"])) or "（空）"
    if kind == "UNKNOWN-ID":
        if vid and vid[0] in ("E", "I"):
            return (f"标签 [{vid}] 不在豁免表 exemptions 里 —— 等于这句话没有豁免依据"
                    f"（现有豁免：{ex_ids}；出处条目：{p_ids}）")
        return (f"标签 [{vid}] 不在 decisions/CONSTRAINTS.json 的 entries 里 —— "
                f"等于这句话没有出处（现有出处条目：{p_ids}）")
    if kind == "NOT-APPROVED":
        return (f"标签 [{vid}] 登记了，但 status 不是 approved 或 quote 为空 —— "
                f"拿不到批准记录，依据不成立")
    base = "这句没带任何豁免标签，也不在存量登记（baseline）里 → 按 P 处理"
    if suggestion:
        base += f"（建议：这句很像 {suggestion}「{registry['exemptions'][suggestion]['name']}」）"
    return base


def fix_options(kind: str, vid: str | None, suggestion: str | None,
                registry: dict) -> list[str]:
    if kind == "UNKNOWN-ID":
        return [f"a) 改成真实存在的 id（豁免：{'、'.join(sorted(registry['exemptions']))}；"
                f"出处：{'、'.join(sorted(registry['entries']))}）",
                "b) 删掉这个标签 —— 然后它就按 NO-PROVENANCE 的三条路处置"]
    if kind == "NOT-APPROVED":
        return [f"a) 找伙伴要一句原话，把 [{vid}] 的 status 改成 approved 并填 quote"
                "（唯一能自己走完的一步，但原话必须是伙伴说的）",
                "b) 换一个已批准的 id",
                "c) 按 NO-PROVENANCE 的三条路处置"]
    opts = []
    if suggestion:
        opts.append(f"a) 若是豁免面条款 → 行尾加 [C:{suggestion}]"
                    f"（{registry['exemptions'][suggestion]['name']}）")
    else:
        opts.append("a) 若是豁免面条款 → 行尾加 [C:E-00x] / [C:I-00x]（现有豁免："
                    + "、".join(sorted(registry["exemptions"])) + "）")
    opts.append("b) 若是限制技术路径的条款 → 给出处：[C:P-xxx]，且 status=approved、quote 非空")
    opts.append("c) 降为建议 —— 把「禁止 X」改成「建议不 X」（去掉禁止式措辞即可，不改意思）")
    return opts


def render_violation(v: dict, guidance: str, registry: dict) -> list[str]:
    short = v["text"] if len(v["text"]) <= 110 else v["text"][:110] + "…"
    out = [f"[-] {v['kind']:<14} {v['file']}:{v['line']}  {short}"]
    out.append("      为什么：" + why_text(v["kind"], v.get("id"), v.get("suggestion"), registry))
    out.append("      怎么改（改的是**这句话**，不是判据）：")
    out += ["        " + o for o in fix_options(v["kind"], v.get("id"),
                                                 v.get("suggestion"), registry)]
    out.append(f"      原则：{guidance}（{GUIDANCE_ID}）")
    return out


def render_footer(guidance: str, log_hint: str, registry: dict) -> list[str]:
    ex = registry["exemptions"]
    return [
        "",
        "—" * 64,
        "总则（改的是句子，不是判据）：",
        "  三条路：① 豁免面条款 → 加 [C:E-00x] / [C:I-00x]"
        " ② 限制技术路径 → 给出处 [C:P-xxx] ③ 降为建议（去掉禁止式措辞）",
        f"  豁免条目（{len(ex)} 条，伙伴 2026-11-18 批准）：",
        *[f"    {k}  {v['name']} —— {v['definition'][:60]}" for k, v in sorted(ex.items())],
        "  闸门**不猜语义**：它只看「有标签 → 表里在 → 批准了吗」。句子写什么都不影响判定。",
        "  注意：不要为了变绿去改判据或加豁免词 —— 那等于拆了闸门自己（第 1 轮 B1 就是这么来的）。",
        "    新增豁免条目只能由伙伴批准，并写进 decisions/CONSTRAINTS.json 的 exemptions。",
        f"  台账：{log_hint}（累积日志，只 append）+ `--stats` 看条目使用频次与分类漂移。",
        f"  原则原文：{guidance}（{GUIDANCE_ID}）",
    ]


# --- `--defs`：三处 E/I/P 定义逐字一致（C7） --------------------------------
DEF_CITED_IN = ("AGENTS.md", "TRIGGER-POLICY.md")


def check_defs(registry: dict) -> int:
    """核 decisions/CONSTRAINTS.json 的 classes[*].definition 与两处引用逐字一致。

    第 5 轮换的是**判定方式**，不是三态定义本身 —— 那三条仍描述「什么算 E/I/P」（写文档的人读），
    只是闸门不再拿它们去猜。所以这条逐字一致检查照旧。
    """
    classes = registry.get("classes") or {}
    bad = 0
    lines_out = [f"[defs] 权威：{rel(REGISTRY)} 的 classes[*].definition"]
    for key in ("E", "I", "P"):
        definition = (classes.get(key) or {}).get("definition")
        if not isinstance(definition, str) or len(definition.strip()) < 10:
            lines_out.append(f"[-] DEF-MISSING {key}：definition 缺失或过短")
            bad += 1
            continue
        for relpath in DEF_CITED_IN:
            path = ROOT / relpath
            if not path.is_file():
                lines_out.append(f"[-] MISSING-FILE {relpath}")
                bad += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            idx = text.find(definition)
            if idx < 0:
                lines_out.append(f"[-] MISMATCH   {relpath} 未逐字包含 {key} 的定义")
                bad += 1
            else:
                line = text.count("\n", 0, idx) + 1
                lines_out.append(f"[+] OK         {relpath}:{line}  {key}")
    lines_out.append(f"[gate] RESULT={'FAIL' if bad else 'OK'}  defs={len(DEF_CITED_IN) * 3} "
                     f"violations={bad}")
    print("\n".join(lines_out))
    return 0 if bad == 0 else 1


def list_exemptions(registry: dict) -> int:
    rows = [f"[exemptions] {len(registry['exemptions'])} 条"
            f"（{rel(REGISTRY)}，伙伴 2026-11-18 批准）"]
    for eid, e in sorted(registry["exemptions"].items()):
        rows.append(f"  {eid}  [{e.get('status')}]  {e['name']}")
        rows.append(f"        scope: {e.get('scope')}")
        rows.append(f"        def  : {e.get('definition')}")
    print("\n".join(rows))
    return 0


# --- 数据台账 ---------------------------------------------------------------
def write_jsonl_line(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_inventory(path: Path, rows: list[tuple[str, int, str, str, str]],
                    by_keys: dict[tuple[str, int], str]) -> None:
    """全仓条款台账：一条一行，带原文 —— baseline 只有 sha1，没有原文，答不了「哪些句式反复被拦」。

    `by` 取**判定循环的实际结果**（标签 id / `(baseline)` / `None`），不自己按「有没有标签」重算：
    第 4 轮验收官 B2 实测，旧写法把 UNKNOWN-ID / status=proposed / quote 空 三种被同一道闸门
    拒绝的行记成了「已登记」，台账与裁决自相矛盾。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = now_ts()
    body = []
    for f, ln, text, _prev, _unused in rows:
        body.append(json.dumps({
            "file": f, "line": ln, "text": text,
            "by": by_keys.get((f, ln)),
            "registered": (f, ln) in by_keys,
            "ts": ts,
        }, ensure_ascii=False))
    path.write_text("\n".join(body) + "\n", encoding="utf-8")


def load_log(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def cmd_stats(log_path: Path, as_json: bool) -> int:
    runs = load_log(log_path)
    if not runs:
        print(f"[stats] 还没有日志：{rel(log_path)}（跑一次闸门就会 append）")
        return 0
    viol_all: list[dict] = []
    for r in runs:
        viol_all += r.get("violations") or []
    why_count: dict[str, int] = {}
    for v in viol_all:
        why_count[v.get("why") or "?"] = why_count.get(v.get("why") or "?", 0) + 1
    sent_count: dict[str, dict] = {}
    for v in viol_all:
        key = digest(v.get("text") or "")
        slot = sent_count.setdefault(key, {"text": v.get("text") or "", "n": 0})
        slot["n"] += 1
    reg_count: dict[str, int] = {}
    for r in runs:
        for k, n in (r.get("registered") or {}).items():
            reg_count[k] = reg_count.get(k, 0) + int(n)
    drift: dict[str, dict] = {}
    for r in runs:
        for f, dg, by in r.get("rows") or []:
            slot = drift.setdefault(f"{f}|{dg}", {"bys": {}, "text": ""})
            slot["bys"][by or "(none)"] = slot["bys"].get(by or "(none)", 0) + 1
    drift = {k: v for k, v in drift.items() if len(v["bys"]) > 1}
    used = {k for k in reg_count if k.startswith(("E-", "I-"))}

    span = f"{runs[0].get('ts', '?')} → {runs[-1].get('ts', '?')}"
    viol_runs = sum(1 for r in runs if r.get("violations"))
    payload = {
        "runs": len(runs), "span": span, "violations": len(viol_all),
        "runs_with_violations": viol_runs,
        "why": dict(sorted(why_count.items(), key=lambda kv: -kv[1])),
        "sentences": sorted(({"text": v["text"], "n": v["n"]} for v in sent_count.values()),
                            key=lambda kv: -kv["n"])[:15],
        "registered": dict(sorted(reg_count.items(), key=lambda kv: -kv[1])),
        "exemptions_used": sorted(used),
        "drift": {k: v["bys"] for k, v in list(drift.items())[:15]},
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    out = [f"[stats] log={rel(log_path)} runs={payload['runs']} span={span}",
           f"[stats] 违规 {payload['violations']} 条，出现在 {viol_runs}/{payload['runs']} 次运行里"]
    out.append("-- why 频次（机器可读的原因码）--")
    out += [f"  {n:>5}  {k}" for k, n in payload["why"].items()] or ["  （无）"]
    out.append("-- 句式频次（同一句式跨运行累计，top 15）--")
    out += [f"  {v['n']:>5}  {v['text'][:96]}" for v in payload["sentences"]] or ["  （无）"]
    out.append("-- 登记依据使用次数（豁免条目 / 出处条目 / baseline）--")
    out += [f"  {k:<10} {n}" for k, n in payload["registered"].items()] or ["  （无）"]
    out.append("-- 用过哪些豁免条目 --")
    out += [f"  {k}" for k in payload["exemptions_used"]] or ["  （无）"]
    if payload["drift"]:
        out.append("-- 依据漂移（同一 文件|句式 在不同运行里走了不同依据）--")
        out += [f"  {k}  {v}" for k, v in payload["drift"].items()]
    out.append("[stats] RESULT=OK")
    print("\n".join(out))
    return 0


def main() -> int:
    # 报告里会回显被扫文档的原文，而原文可能含 GBK 编不了的字符（如 ⚠）——
    # 控制台是 cp936，不兜住的话整份报告会以 UnicodeEncodeError 崩掉（2026-11-18 实测）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--changed", nargs="*", default=None,
                    help="本单元改动清单（路径或清单 .txt）；空清单 → exit 2")
    ap.add_argument("--changed-from", default=None, help="改动清单文件")
    ap.add_argument("--all", action="store_true", help="扫全仓默认面（体检用）")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true", help="重建存量登记后退出")
    ap.add_argument("--defs", action="store_true", help="核三处 E/I/P 定义逐字一致后退出")
    ap.add_argument("--list-exemptions", action="store_true", help="印出豁免条目表后退出")
    ap.add_argument("--inventory", nargs="?", const=str(DEFAULT_INVENTORY), default=None,
                    help="把本次扫描面的条款台账写成 jsonl（不给路径则用默认位置）")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="累积日志（append）")
    ap.add_argument("--no-log", action="store_true", help="本次不写累积日志（台子用）")
    ap.add_argument("--stats", action="store_true", help="从累积日志出频次报告后退出")
    ap.add_argument("--out", default=None, help="把报告落盘")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    baseline_path = Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = ROOT / baseline_path
    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = ROOT / log_path
    registry = load_registry()

    if args.list_exemptions:
        return list_exemptions(registry)
    if args.stats:
        # `--out` 也要对 stats 生效：证据文件一律由脚本自己写 UTF-8，
        # 不走 PowerShell 的 `>`（那会写成 UTF-16）。
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_stats(log_path, args.json)
        text = buf.getvalue()
        sys.stdout.write(text)
        if args.out:
            outp = Path(args.out)
            if not outp.is_absolute():
                outp = ROOT / outp
            outp.parent.mkdir(parents=True, exist_ok=True)
            outp.write_text(text, encoding="utf-8")
            print(f"[constraints] report → {rel(outp)}")
        return code
    if args.defs:
        return check_defs(registry)

    # ---- 目标文件 ----
    targets: list[Path] = []
    if args.all:
        targets = iter_docs()
    elif args.changed_from or args.changed is not None:
        raw = list(args.changed or [])
        if args.changed_from:
            cf = Path(args.changed_from)
            if not cf.is_absolute():
                cf = ROOT / cf
            if cf.is_file():
                raw += [ln.strip() for ln in cf.read_text(encoding="utf-8").splitlines()]
        picked: list[Path] = []
        for item in raw:
            if not item or item.startswith("#"):
                continue
            p = Path(item)
            if not p.is_absolute():
                p = ROOT / p
            if p.is_dir():
                picked += [q for q in p.rglob("*.md")]
            elif p.suffix.lower() == ".md":
                picked.append(p)
        targets = [p for p in picked if p.is_file() and not is_excluded(p)]
        if not targets:
            print("[gate] EMPTY-CHANGED 漏给清单不是豁免，是漏检 → exit 2")
            return 2
    else:
        targets = iter_docs()

    # ---- 抽取 ----
    rows: list[tuple[str, int, str, str]] = []   # (rel, line, text, prev)
    for p in targets:
        for ln, text, prev in extract(p):
            rows.append((rel(p), ln, text, prev))

    print(f"[constraints] files={len(targets)} prohibitions={len(rows)}")

    if args.write_baseline:
        # 带 `[C:xxx]` 标签的行**不进 baseline**：它们已经有依据了。
        # 若把它们也登记成存量，标签哪天被删掉，baseline 会替它兜底 —— 依据就变成可选装饰了。
        tagged = [r for r in rows if TAG_RE.search(r[2]) or TAG_RE.search(r[3] or "")]
        untagged = [(r[0], digest(r[2])) for r in rows
                    if not (TAG_RE.search(r[2]) or TAG_RE.search(r[3] or ""))]
        write_baseline([(p, h, "hist") for p, h in untagged], baseline_path)
        print(f"[constraints] baseline written: {rel(baseline_path)} "
              f"(entries={len(untagged)}) skipped_tagged={len(tagged)}")
        return 0

    baseline = load_baseline(baseline_path)
    violations: list[dict] = []
    registered: list[dict] = []
    by_keys: dict[tuple[str, int], str] = {}
    reg_count: dict[str, int] = {}
    for path_s, ln, text, prev in rows:
        # 标签**只认本行**（第 8 轮口径，第 7 轮起生效）。
        # 标签**只认本行**。第 5/6/7 轮验收官用四种写法轮流打穿过「跨行继承」：
        #   ① 平级列表项 ② 引用块 ③ 缩进文本 + 列表项 ④ 引用块 + 标题行
        # 根因是 Markdown 里「上一行与本行是什么关系」本身没有可靠判据（标题 / 引用 / 缩进 / 代码块
        # 各有各的语义），任何启发式都会被找到新组合。所以第 7 轮起收成最简规则：
        # **标签必须和禁限词在同一行**。折行的条款要在被抽取的那一行上也标。
        tag = TAG_RE.search(text)
        if tag:
            cid = tag.group(1)
            entry = lookup(registry, cid)
            if not entry:
                violations.append({"kind": "UNKNOWN-ID", "id": cid, "file": path_s,
                                   "line": ln, "text": text, "why": "unknown-id",
                                   "suggestion": suggest_exemption(text, registry)})
            elif entry.get("status") != "approved" or not entry.get("quote"):
                violations.append({"kind": "NOT-APPROVED", "id": cid, "file": path_s,
                                   "line": ln, "text": text, "why": "not-approved",
                                   "suggestion": None})
            else:
                registered.append({"id": cid, "file": path_s, "line": ln})
                by_keys[(path_s, ln)] = cid
                reg_count[cid] = reg_count.get(cid, 0) + 1
            continue
        if f"{path_s}|{digest(text)}" in baseline:
            registered.append({"id": "(baseline)", "file": path_s, "line": ln})
            by_keys[(path_s, ln)] = "(baseline)"
            reg_count["(baseline)"] = reg_count.get("(baseline)", 0) + 1
            continue
        violations.append({"kind": "NO-PROVENANCE", "id": None, "file": path_s,
                           "line": ln, "text": text, "why": "no-exempt-tag",
                           "suggestion": suggest_exemption(text, registry)})

    lines_out: list[str] = []
    lines_out.append(f"[constraints] files={len(targets)} prohibitions={len(rows)} "
                     f"registered={len(registered)} violations={len(violations)}")
    for v in registered:
        lines_out.append(f"[+] OK        {v['file']}:{v['line']}  {v.get('id')}")
    for v in violations:
        lines_out += render_violation(v, registry["guidance"], registry)
    result = "FAIL" if violations else "OK"
    if violations:
        lines_out += render_footer(registry["guidance"], rel(log_path), registry)
    lines_out.append(f"[gate] RESULT={result}")

    report = "\n".join(lines_out)
    print(report)
    if args.out:
        outp = Path(args.out)
        if not outp.is_absolute():
            outp = ROOT / outp
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(report + "\n", encoding="utf-8")
        print(f"[constraints] report → {rel(outp)}")

    if args.inventory:
        inv = Path(args.inventory)
        if not inv.is_absolute():
            inv = ROOT / inv
        inv_rows = [(f, ln, text, prev, "") for f, ln, text, prev in rows]
        write_inventory(inv, inv_rows, by_keys)
        print(f"[constraints] inventory → {rel(inv)} (rows={len(rows)})")

    if not args.no_log:
        write_jsonl_line(log_path, {
            "ts": now_ts(),
            "mode": "all" if args.all else ("changed" if targets else "default"),
            "files": [rel(p) for p in targets][:200],
            "counts": {"files": len(targets), "prohibitions": len(rows)},
            "registered": reg_count,
            "baseline_size": len(baseline),
            "violations": violations,
            # 漂移的 key 用「去掉标签后的条款本体」—— 否则改一下标签就算新句式，漂移永远看不见
            # （第 5 轮验收官 B3 实测：行尾 `[C:E-001]` 改成 `[C:I-001]`，drift 仍是空的）。
            "rows": [[r[0], digest(TAG_RE.sub("", r[2])), by_keys.get((r[0], r[1]))]
                     for r in rows][:LOG_MAX_ROWS],
            "result": result,
        })

    if args.json:
        print(json.dumps({"result": result, "prohibitions": len(rows),
                          "violations": violations, "registered": len(registered),
                          "guidance": registry["guidance"]}, ensure_ascii=False))
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
