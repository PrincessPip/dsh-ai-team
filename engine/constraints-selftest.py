#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constraints-selftest.py — 出处闸门的断言台（第 5 轮：豁免条目制）。

房子规矩：判据必须有台子（`selftest-*.py` + `mutate-*.py`），且变异台要求
「红的**恰好是**那一块」，`void>0` 直接判失败 —— 没变异 ≠ 抓不到。

**第 5 轮把断言的对象又换了一层**（2026-11-18 伙伴批准 C 方案）：
  第 1–4 轮问的是「判得对不对」—— 结果是每轮都被一个新说法穿透（`0.97 → 0.97 → 0.98 → escalate 0.99`）；
  第 5 轮问的是「**有标签 → 表里在 → 批准了没**」，并额外钉住两件事：
    · **第 4 轮验收官那 9 个穿透反例，现在必须全部判 `P`** —— 判据里一个词表都没有了，
      它们靠「没标签」自然落 P（`T20`–`T28`）；
    · **建议器不参与判定**（`T30` 断言建议只是提示；变异 `M32` 把它接进放行路径后，那批反例立刻变红）。

每个用例在**全新隔离沙箱**里跑（TEMP/constraints-selftest-<n>），沙箱内自带
`scripts/constraints-check.py` + `decisions/CONSTRAINTS.json`，不碰真工程。
一个用例可以有**多个步骤**（argv 列表），用于「先跑一次产生日志，再用 --stats 读它」。

用法：
    python scripts/constraints-selftest.py            # 跑全部
    python scripts/constraints-selftest.py --json     # 给变异台吃
    python scripts/constraints-selftest.py --script <被变异的副本>
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
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRIPT = ROOT / "scripts" / "constraints-check.py"

# 三处定义一致（T47/T48）用的权威措辞：与真工程 decisions/CONSTRAINTS.json 的
# classes[*].definition 一字不差。这里复制一份是为了沙箱能独立跑，不代表第二处真值 ——
# `--defs` 在真工程里读的是 CONSTRAINTS.json，那才是唯一真值。
DEFS = {
    "E": "证据完整性：不冒称通过 / 不猜 / 不伪造裁决 / 不补签凑绿",
    "I": ("隔离实验边界：评审树只读 / 不复制 / 不删锁 / 不导入旧 live / 不占用生产端口"
          " —— 只约束实验进程，不得外溢成产品能力边界"),
    "P": "限制潜在技术路径：禁这禁那、预先取消可选动作 —— 无伙伴原话出处者仅作建议，只阻塞送审",
}

GUIDANCE = "自限条款不应当封锁潜在技术路径。"
GUIDANCE_ALT = "改过的理念句：自限条款应当带出处。"

REGISTRY_OK = {
    "schema": "constraints-v1",
    "classes": {
        "E": {"name": "evidence-integrity", "auto_pass": True, "definition": DEFS["E"]},
        "I": {"name": "experiment-isolation", "auto_pass": True, "definition": DEFS["I"]},
        "P": {"name": "path-limiting", "auto_pass": False, "definition": DEFS["P"]},
    },
    "exemptions": [
        {"id": "E-001", "class": "E", "name": "不冒称通过", "status": "approved",
         "definition": "判决只能来自实际跑过的验证。", "scope": "全仓",
         "quote": "就先按这个开工吧。", "approved_by": "伙伴", "approved_at": "2026-11-18"},
        {"id": "E-002", "class": "E", "name": "不伪造裁决", "status": "approved",
         "definition": "裁决只能由有资格的人或程序出。", "scope": "全仓",
         "quote": "就先按这个开工吧。", "approved_by": "伙伴", "approved_at": "2026-11-18"},
        {"id": "I-001", "class": "I", "name": "评审只读", "status": "approved",
         "definition": "验收官只读文件、只跑只读命令。", "scope": "评审进程",
         "quote": "就先按这个开工吧。", "approved_by": "伙伴", "approved_at": "2026-11-18"},
        {"id": "I-002", "class": "I", "name": "判据类文件禁改", "status": "approved",
         "definition": "契约、模板、判据脚本是被审对象。", "scope": "评审进程",
         "quote": "就先按这个开工吧。", "approved_by": "伙伴", "approved_at": "2026-11-18"},
        {"id": "I-003", "class": "I", "name": "实验在副本里做", "status": "proposed",
         "definition": "会落盘的动作挪到独占副本。", "scope": "实验进程",
         "quote": "（伙伴尚未批准）", "approved_by": None, "approved_at": None},
        {"id": "I-004", "class": "I", "name": "空 quote 的豁免", "status": "approved",
         "definition": "用来测 quote 为空。", "scope": "实验进程",
         "quote": "", "approved_by": "伙伴", "approved_at": "2026-11-18"},
    ],
    "entries": [
        {"id": "P-001", "class": "P", "status": "approved",
         "text": "出处制", "quote": "尽可能不增加会限制潜在技术路径的自限条款。",
         "guidance": GUIDANCE, "guidance_at": "2026-11-18", "approved_at": "2026-11-17"},
        {"id": "P-002", "class": "P", "status": "approved",
         "text": "网络改动需先给方案", "quote": "废止清单就按照你的方案改。",
         "approved_at": "2026-11-17"},
        {"id": "P-003", "class": "P", "status": "proposed",
         "text": "只是建议", "quote": "（伙伴尚未批准，仅登记为建议）", "approved_at": None},
    ],
}

DOC = "# 假想单元\n\n{t}\n"


def registry_with(**overrides) -> dict:
    reg = json.loads(json.dumps(REGISTRY_OK))
    if "drop_guidance" in overrides:
        for e in reg["entries"]:
            if e["id"] == "P-001":
                e.pop("guidance", None)
    if "guidance" in overrides:
        for e in reg["entries"]:
            if e["id"] == "P-001":
                e["guidance"] = overrides["guidance"]
    if "extra_entries" in overrides:
        reg["entries"] += overrides["extra_entries"]
    if "schema" in overrides:
        reg["schema"] = overrides["schema"]
    return reg


def step(argv, expect_exit, must=(), must_not=(), check=None):
    return {"argv": list(argv), "expect_exit": expect_exit,
            "must": list(must), "must_not": list(must_not), "check": check}


def case(name, steps, body=None, registry=None, baseline_lines=None, files=None):
    return {"name": name, "steps": steps, "body": body,
            "registry": registry or REGISTRY_OK,
            "baseline_lines": baseline_lines, "files": files or {}}


CHANGED = ["--changed", "{doc}"]


def _defs_docs(i_definition: str) -> dict:
    """造三处定义文件：AGENTS.md 与 TRIGGER-POLICY.md 逐字引用 definition。"""
    def md(sep: str) -> str:
        return ("# 假想规范\n\n"
                f"- `E` {DEFS['E']}\n"
                f"- `I` {i_definition}\n"
                f"- `P` {DEFS['P']}\n"
                f"\n{sep}\n")
    return {"AGENTS.md": md("引用处一"), "TRIGGER-POLICY.md": md("引用处二")}


CASES = [
    # === 参数与清单纪律 =====================================================
    case("T01 空清单 → exit 2", [step(["--changed"], 2, must=["EMPTY-CHANGED"])],
         body="- 不改任何通道。"),
    case("T02 非 md 不计入 → 空清单 exit 2",
         [step(["--changed", "{note}"], 2, must=["EMPTY-CHANGED"])], body="- 不改任何通道。"),
    case("T03 目录展开其 *.md",
         [step(["--changed", "{dir}"], 1, must=["NO-PROVENANCE"])], body="- 不改任何通道。"),

    # === 核心判定：没标签、不在 baseline → P ================================
    case("T04 无标签无 baseline → FAIL",
         [step(CHANGED, 1, must=["NO-PROVENANCE", "RESULT=FAIL"])],
         body="- 不改系统 DNS，也不换任何代理通道。"),
    case("T05 带 [C:P-001]（approved + quote）→ OK",
         [step(CHANGED, 0, must=["RESULT=OK", "P-001"])],
         body="- 不改系统 DNS。[C:P-001]"),
    case("T06 未知 P 标签 → UNKNOWN-ID",
         [step(CHANGED, 1, must=["UNKNOWN-ID"])], body="- 不改系统 DNS。[C:P-999]"),
    case("T07 P status=proposed → NOT-APPROVED",
         [step(CHANGED, 1, must=["NOT-APPROVED"])], body="- 不改系统 DNS。[C:P-003]"),
    case("T08 P quote 为空 → NOT-APPROVED",
         [step(CHANGED, 1, must=["NOT-APPROVED"])], body="- 不改系统 DNS。[C:P-004]",
         registry=registry_with(extra_entries=[
             {"id": "P-004", "class": "P", "status": "approved", "text": "x", "quote": ""}])),

    # === 抽取精度（不该抓的）===============================================
    case("T09 已划掉条文不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="- 不重启服务（~~不重启~~ 已删除此条）。"),
    case("T10 代码块内不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])], body="```\n- 不重启服务\n```"),
    case("T11 引用块不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])], body="> 禁止在此新增自限条款。"),
    case("T12 词内假阳性不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="- 余额调不动，所以置为 inactive。"),
    case("T13 叙述式非规定句不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="现象：上游不匹配 → 不自动重试。"),
    case("T14 超长行不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="- 不改任何通道。" + "说明" * 120),

    # === 豁免条目（第 5 轮核心）============================================
    case("T15 带 [C:E-001] → 放行",
         [step(CHANGED, 0, must=["RESULT=OK", "E-001"])], body="- 不冒称通过。[C:E-001]"),
    case("T16 带 [C:I-001] → 放行",
         [step(CHANGED, 0, must=["RESULT=OK", "I-001"])], body="- 评审树保持只读，不复制会写产物的脚本。[C:I-001]"),
    case("T17 未知 E 标签 → UNKNOWN-ID，且提示在豁免表里找",
         [step(CHANGED, 1, must=["UNKNOWN-ID", "不在豁免表"])],
         body="- 不冒称通过。[C:E-999]"),
    case("T18 豁免条目 status=proposed → NOT-APPROVED",
         [step(CHANGED, 1, must=["NOT-APPROVED"])], body="- 只在副本里跑，不写主树。[C:I-003]"),
    case("T19 豁免条目 quote 为空 → NOT-APPROVED",
         [step(CHANGED, 1, must=["NOT-APPROVED"])], body="- 只在副本里跑，不写主树。[C:I-004]"),

    # === 第 4 轮被打穿的 9 个反例：现在必须全部 P（判据里已无语义词表）=====
    case("T20 反例：禁止复制任何产品副本 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 禁止复制任何产品副本。"),
    case("T21 反例：禁止修改产品门锁密码 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 禁止修改产品门锁密码。"),
    case("T22 反例：只读产品：禁止导出文件 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 只读产品：禁止导出文件。"),
    case("T23 反例：禁止隐藏产品的成功提示按钮 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 禁止隐藏产品的成功提示按钮。"),
    case("T24 反例：禁止默认使用绿色主题 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 禁止默认使用绿色主题。"),
    case("T25 反例：不改系统 DNS 且不冒称通过（「且」不切子句）→ P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 不改系统 DNS 且不冒称通过。"),
    case("T26 反例：RX-01 原句（已废止的 P）→ P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="- 不把浏览器当作绕过工具非公网地址检查、网站访问控制、验证码或服务商限制的替代通道。"),
    case("T27 反例：产品复制限制 → P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="- 不复制任何文件，产品永久禁用复制功能。"),
    case("T28 反例：混合 E+P（系统 DNS + 不冒称，逗号分隔）→ P",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 不改系统 DNS，不冒称通过。"),
    case("T29 同一句话加标签就过 —— 判定与句子内容无关",
         [step(CHANGED, 0, must=["RESULT=OK", "E-001"])],
         body="- 不改系统 DNS 且不冒称通过。[C:E-001]"),

    # === 建议器（只出建议，不参与判定）=====================================
    case("T30 无标签时给出建议 id",
         [step(CHANGED, 1, must=["建议：这句很像 I-001"])],
         body="- 全程只读，不改任何文件。"),
    case("T31 建议的 id 一定在表里（不许给出不存在的建议）",
         [step(CHANGED, 1, must=["评审只读"])], body="- 不许改 track 里的文件。"),

    # === baseline 的语义（存量登记，不是免检通道）===========================
    case("T32 baseline 命中 → OK",
         [step(CHANGED, 0, must=["RESULT=OK", "baseline"])], body="- 不改系统 DNS。",
         baseline_lines=1),
    case("T33 同文本不同文件 → 仍 FAIL",
         [step(["--changed", "{other}"], 1, must=["NO-PROVENANCE"])],
         body="- 不改系统 DNS。", baseline_lines=1),
    case("T34 --write-baseline 后同文件转绿",
         [step(["--changed", "{doc}", "--write-baseline"], 0, must=["baseline written"],
               check=("baseline_lines", 1)),
          step(["--changed", "{doc}"], 0, must=["RESULT=OK"])],
         body="- 不改系统 DNS。"),
    case("T35 --write-baseline 不登记带标签的行（依据不是装饰）",
         [step(["--changed", "{doc}", "--write-baseline"], 0, must=["skipped_tagged=1"],
               check=("baseline_lines", 0))],
         body="- 不改系统 DNS。[C:P-001]"),

    # === 常量表坏了 =========================================================
    case("T36 registry schema 错 → exit 3",
         [step(CHANGED, 3, must=["REGISTRY-SCHEMA"])], body="- 不改系统 DNS。",
         registry=registry_with(schema="bogus")),
    case("T37 P-001.guidance 缺失 → exit 3（闸门自己的理念句也必须有出处）",
         [step(CHANGED, 3, must=["GUIDANCE-MISSING"])], body="- 不改系统 DNS。",
         registry=registry_with(drop_guidance=True)),
    case("T38 豁免条目 id 不以 E-/I- 开头 → exit 3",
         [step(CHANGED, 3, must=["REGISTRY-BAD-EXEMPTION"])], body="- 不改系统 DNS。",
         registry={**json.loads(json.dumps(REGISTRY_OK)),
                   "exemptions": [{"id": "X-001", "class": "E", "name": "错的 id",
                                   "status": "approved", "quote": "x", "definition": "x"}]}),

    # === 报错三行化 =========================================================
    case("T39 违规给三行：为什么 / 怎么改 / 原则",
         [step(CHANGED, 1, must=["为什么：", "怎么改（改的是**这句话**，不是判据）：",
                                 f"原则：{GUIDANCE}（P-001）"])],
         body="- 不改系统 DNS。"),
    case("T40 末尾总则列出豁免条目与「不要改判据」",
         [step(CHANGED, 1, must=["总则（改的是句子，不是判据）",
                                 "豁免条目（6 条，伙伴 2026-11-18 批准）",
                                 "不要为了变绿去改判据或加豁免词",
                                 "闸门**不猜语义**"])],
         body="- 不改系统 DNS。"),
    case("T41 理念句从常量表读、不硬编码（换一条就跟着换）",
         [step(CHANGED, 1, must=[f"原则：{GUIDANCE_ALT}（P-001）"],
               must_not=[f"原则：{GUIDANCE}（P-001）"])],
         body="- 不改系统 DNS。", registry=registry_with(guidance=GUIDANCE_ALT)),
    case("T42 UNKNOWN-ID 的「为什么」说明标签不在哪张表",
         [step(CHANGED, 1, must=["为什么：标签 [P-999] 不在",
                                 "a) 改成真实存在的 id（豁免：E-001、E-002"])],
         body="- 不改系统 DNS。[C:P-999]"),

    # === 数据台账 ===========================================================
    case("T43 --inventory 落 jsonl，一条一行且带原文与依据",
         [step(["--changed", "{doc}", "--no-log", "--inventory", "decisions/inv.jsonl"], 0,
               must=["inventory → decisions/inv.jsonl"],
               check=("file_contains", "decisions/inv.jsonl", '"by": "E-001"'))],
         body="- 不冒称通过。[C:E-001]"),
    case("T44 inventory 的 registered 跟裁决一致（未知标签不算已登记）",
         [step(["--changed", "{doc}", "--no-log", "--inventory", "decisions/inv.jsonl"], 1,
               must=["UNKNOWN-ID"],
               check=("file_contains", "decisions/inv.jsonl", '"registered": false'))],
         body="- 不改系统 DNS。[C:P-999]"),
    case("T45 inventory：status=proposed 也不算已登记",
         [step(["--changed", "{doc}", "--no-log", "--inventory", "decisions/inv.jsonl"], 1,
               must=["NOT-APPROVED"],
               check=("file_contains", "decisions/inv.jsonl", '"registered": false'))],
         body="- 不改系统 DNS。[C:P-003]"),
    case("T46 默认写累积日志（append，第二次跑变两行）",
         [step(["--changed", "{doc}"], 1,
               check=("file_lines", "decisions/constraints-log.jsonl", 1)),
          step(["--changed", "{doc}"], 1,
               check=("file_lines", "decisions/constraints-log.jsonl", 2))],
         body="- 不改系统 DNS。"),
    case("T47 --no-log 不写日志",
         [step(["--changed", "{doc}", "--no-log"], 1),
          step(["--changed", "{doc}", "--no-log"], 1,
               check=("file_absent", "decisions/constraints-log.jsonl"))],
         body="- 不改系统 DNS。"),
    case("T48 --stats 在无日志时说明没有数据",
         [step(["--stats"], 0, must=["还没有日志"])], registry=REGISTRY_OK),
    case("T49 --stats 出 why 频次与条目用量",
         [step(["--stats"], 0, must=["-- why 频次", "-- 登记依据使用次数", "no-exempt-tag",
                                     "不改系统 DNS"])],
         files={"decisions/constraints-log.jsonl": json.dumps({
             "ts": "2026-11-18T02:00:00", "violations": [
                 {"kind": "NO-PROVENANCE", "id": None, "file": "x.md", "line": 3,
                  "text": "- 不改系统 DNS。", "why": "no-exempt-tag"}],
             "registered": {"(baseline)": 2},
             "rows": [["x.md", "abc123", "(baseline)"]]}, ensure_ascii=False) + "\n"}),
    case("T50 --stats 能看出依据漂移（同一句式换依据）",
         [step(["--stats"], 0, must=["依据漂移", "abc123"])],
         files={"decisions/constraints-log.jsonl": "\n".join([
             json.dumps({"ts": "2026-11-18T02:00:00", "violations": [],
                         "rows": [["x.md", "abc123", "(baseline)"]]}, ensure_ascii=False),
             json.dumps({"ts": "2026-11-18T03:00:00", "violations": [],
                         "rows": [["x.md", "abc123", "I-001"]]}, ensure_ascii=False)]) + "\n"}),

    # === 其他自洽 ===========================================================
    case("T51 --json 输出带 result",
         [step(["--changed", "{doc}", "--json"], 1, must=['"result": "FAIL"'])],
         body="- 不改任何通道。"),
    case("T52 --all 模式在空工程里 OK",
         [step(["--all"], 0, must=["RESULT=OK"])]),
    case("T53 --list-exemptions 印出条目表",
         [step(["--list-exemptions"], 0, must=["[exemptions] 6 条", "E-001", "I-001",
                                               "评审只读"])]),
    case("T54 标题行的标签不传给下面的列表项（本行是列表项就得自带标签）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="**规则** [C:P-001]\n- 不改系统 DNS。"),
    case("T54b 缩进续行也得自带标签（跨行继承第 7 轮起已取消）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="4. **规则** [C:P-001]：这一条要讲很长\n   （续行）不得据此拒绝执行。"),
    case("T54c 「缩进文本 + 列表项」不继承（第 6 轮 B1 的第三种写法）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="   示例 [C:E-001]\n- 禁止复制任何产品副本。"),
    case("T55 隔了空行不算承前 → FAIL",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="**规则** [C:P-001]\n\n- 不改系统 DNS。"),

    # === 代码块识别（B2）====================================================
    case("T56 ~~~ 围栏不计入",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])], body="~~~\n- 不重启服务\n~~~"),
    case("T57 四空格缩进块不计入（前一行空）",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="说明如下：\n\n    - 不重启服务\n"),
    case("T58 缩进列表不算代码块（前一行非空）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])], body="- 铁律：\n    - 不重启服务\n"),
    case("T59 围栏按长度配对：短的不能关长的",
         [step(CHANGED, 0, must=["prohibitions=0", "RESULT=OK"])],
         body="````\n- 不重启服务\n```\n- 不换模型\n````\n"),

    # === 三处定义逐字一致 ===================================================
    case("T60 三处 E/I/P 定义逐字一致 → OK",
         [step(["--defs"], 0, must=["RESULT=OK", "defs=6"])], files=_defs_docs(DEFS["I"])),
    case("T61 定义少两个字 → MISMATCH",
         [step(["--defs"], 1, must=["MISMATCH"])],
         files=_defs_docs(DEFS["I"].replace("不得外溢成产品能力边界", "不得外溢成产品边界"))),

    # === 第 5 轮验收官 B1/B2/B3 打出来的三个洞（修完钉住）====================
    case("T62 相邻列表项不继承标签（B1：平级条款各自要有标签）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="- 不冒称通过。[C:E-001]\n- 禁止复制任何产品副本。"),
    case("T63 引用块里的标签不传递给下一行（B1）",
         [step(CHANGED, 1, must=["NO-PROVENANCE"])],
         body="> 某句 [C:E-001]\n- 禁止复制任何产品副本。"),
    case("T64 豁免 id 不是 E-00x 形态 → exit 3（B2）",
         [step(CHANGED, 3, must=["REGISTRY-BAD-EXEMPTION"])], body="- 不改系统 DNS。",
         registry={**json.loads(json.dumps(REGISTRY_OK)),
                   "exemptions": [{"id": "Evil", "class": "E", "name": "x", "status": "approved",
                                   "quote": "q", "definition": "d"}]}),
    case("T64b 豁免 id 是 E001（缺连字符）→ exit 3（B2）",
         [step(CHANGED, 3, must=["REGISTRY-BAD-EXEMPTION"])], body="- 不改系统 DNS。",
         registry={**json.loads(json.dumps(REGISTRY_OK)),
                   "exemptions": [{"id": "E001", "class": "E", "name": "x", "status": "approved",
                                   "quote": "q", "definition": "d"}]}),
    case("T65 --stats 能看出依据漂移（行尾标签换了也要能看见，B3）",
         [step(["--changed", "{doc}", "--log", "decisions/l.jsonl"], 0, must=["RESULT=OK"]),
          {"rewrite": "- 不冒称通过。[C:I-001]"},
          step(["--changed", "{doc}", "--log", "decisions/l.jsonl"], 0, must=["RESULT=OK"]),
          step(["--stats", "--log", "decisions/l.jsonl"], 0, must=["依据漂移"])],
         body="- 不冒称通过。[C:E-001]"),
]


def _baseline_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"^\s*[-*+]\s+", "", text.strip())).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def build_sandbox(idx: int, spec: dict, script: Path) -> Path:
    sb = Path(tempfile.mkdtemp(prefix=f"constraints-selftest-{idx:02d}-"))
    (sb / "scripts").mkdir(parents=True, exist_ok=True)
    (sb / "decisions").mkdir(parents=True, exist_ok=True)
    (sb / "tasks" / "unit").mkdir(parents=True, exist_ok=True)
    shutil.copy2(script, sb / "scripts" / "constraints-check.py")
    (sb / "decisions" / "CONSTRAINTS.json").write_text(
        json.dumps(spec["registry"], ensure_ascii=False, indent=2), encoding="utf-8")

    for relpath, text in (spec.get("files") or {}).items():
        target = sb / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    if spec["body"]:
        for name in ("doc.md", "other.md"):
            (sb / "tasks" / "unit" / name).write_text(
                DOC.format(t=spec["body"]), encoding="utf-8")
        (sb / "tasks" / "unit" / "note.txt").write_text("ignore me\n", encoding="utf-8")
        d = sb / "tasks" / "unit" / "dir"
        d.mkdir()
        (d / "inner.md").write_text(DOC.format(t=spec["body"]), encoding="utf-8")

    if spec["baseline_lines"] is not None and spec["body"]:
        if spec["baseline_lines"] > 0:
            (sb / "scripts" / "constraints-baseline.txt").write_text(
                f"tasks/unit/doc.md|{_baseline_hash(spec['body'])}|hist\n", encoding="utf-8")
        else:
            (sb / "scripts" / "constraints-baseline.txt").write_text(
                "# 空 baseline\n", encoding="utf-8")
    return sb


def _resolve(tokens: list[str], sb: Path) -> list[str]:
    mapping = {
        "{doc}": str(sb / "tasks" / "unit" / "doc.md"),
        "{other}": str(sb / "tasks" / "unit" / "other.md"),
        "{note}": str(sb / "tasks" / "unit" / "note.txt"),
        "{dir}": str(sb / "tasks" / "unit" / "dir"),
    }
    return [mapping.get(t, t) for t in tokens]


def _check(kind: str, args: tuple, sb: Path) -> str:
    """返回空串=通过，否则返回失败说明。"""
    if kind == "baseline_lines":
        bl = sb / "scripts" / "constraints-baseline.txt"
        n = (sum(1 for ln in bl.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")) if bl.is_file() else -1)
        return "" if n == args[0] else f"baseline 数据行 {n} != {args[0]}"
    if kind == "file_lines":
        p = sb / args[0]
        n = len(p.read_text(encoding="utf-8").splitlines()) if p.is_file() else -1
        return "" if n == args[1] else f"{args[0]} 行数 {n} != {args[1]}"
    if kind == "file_contains":
        p = sb / args[0]
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
        return "" if args[1] in text else f"{args[0]} 里没有 {args[1]!r}"
    if kind == "file_absent":
        return "" if not (sb / args[0]).is_file() else f"{args[0]} 不该存在"
    return f"未知 check: {kind}"


def run_case(idx: int, spec: dict, script: Path) -> dict:
    sb = build_sandbox(idx, spec, script)
    try:
        problems: list[str] = []
        outs: list[str] = []
        for si, st in enumerate(spec["steps"], 1):
            if "rewrite" in st:
                # 步间改文件（测「同一句换标签，漂移能不能被看见」这类跨运行性质）
                (sb / "tasks" / "unit" / "doc.md").write_text(
                    DOC.format(t=st["rewrite"]), encoding="utf-8")
                continue
            argv = [sys.executable, str(sb / "scripts" / "constraints-check.py")]
            argv += _resolve(st["argv"], sb)
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.run(argv, cwd=sb, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=120)
            out = (proc.stdout or "") + (proc.stderr or "")
            outs.append(out)
            if proc.returncode != st["expect_exit"]:
                problems.append(f"step{si} exit={proc.returncode} want={st['expect_exit']}")
            missing = [m for m in st["must"] if m not in out]
            if missing:
                problems.append(f"step{si} 缺: {missing}")
            leaked = [m for m in st["must_not"] if m in out]
            if leaked:
                problems.append(f"step{si} 不该出现: {leaked}")
            if st["check"]:
                msg = _check(st["check"][0], st["check"][1:], sb)
                if msg:
                    problems.append(f"step{si} {msg}")
        return {"name": spec["name"], "pass": not problems,
                "detail": " | ".join(problems), "out": "\n".join(outs)[-1500:]}
    finally:
        shutil.rmtree(sb, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=str(DEFAULT_SCRIPT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    script = Path(args.script)
    if not script.is_file():
        print(f"missing script: {script}")
        return 2

    # 起手守卫（2026-11-21 加）：夹具建在 tempfile.gettempdir() 下，而隔离要求常让人把
    # TEMP/TMP 指到「工程树里的某个目录」。那样夹具就落进树 → 下一轮 before/after 快照
    # 判 deleted → review-codex.py **exit 4**，一整轮送审（约 100 min）作废。
    # round 1 就这么废过一次。这里不猜、不降级：TEMP 在树内直接拒跑。
    tmp_root = Path(tempfile.gettempdir()).resolve()
    proj_root = ROOT.resolve()
    if tmp_root == proj_root or proj_root in tmp_root.parents:
        print(f"[guard] TEMP 解析到工程树内：{tmp_root}")
        print("        夹具会写进树 → 下一轮 after 快照判 deleted → exit 4。")
        print("        把 TEMP/TMP 指到工程外（或直接不设）再跑。")
        return 2

    results = []
    for i, spec in enumerate(CASES, 1):
        r = run_case(i, spec, script)
        results.append(r)
        if not args.quiet:
            print(f"[{'PASS' if r['pass'] else 'FAIL'}] {r['name']}")
            if not r["pass"]:
                print(f"        {r['detail']}")
                print("        --- 输出尾部 ---")
                for ln in (r["out"] or "").splitlines()[-8:]:
                    print("        " + ln)

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    print(f"\nRESULT={passed}/{total}" + ("" if passed == total else "  ← 未全绿，先修"))
    if args.json:
        print("JSON=" + json.dumps([{"name": r["name"], "pass": r["pass"]} for r in results],
                                   ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
