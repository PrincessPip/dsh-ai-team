#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""constraints-mutate.py — 出处闸门的变异台（第 5 轮：豁免条目制）。

房子规矩（抄 `mutate-landing.py` / `mutate-doc-sync.py`）：
    逐块打坏 → 断言「红的**恰好是**那一块」；`void>0` 直接判失败（没变异 ≠ 抓不到）；
    **干净镜像必须先全绿**，否则红不作数。

第 5 轮的核心格（编号按现行台子；第 8 轮把这段过时编号校正过一次）：
    M10 不认标签 · M12 未知标签当通过 · M13/M14 不查 status/quote · M15 查错表 —— 判定面四根柱子
    M27 inventory 自己重算 registered（第 4 轮 B2 的原形）· M19 / M34 豁免条目 id 校验
    **M32 把建议器接进判定** —— 这是最该钉的一格：建议器一旦参与判定，
        第 4 轮那批穿透反例（`T20`–`T28`）就会重新被放行。它是「提案」不是「判据」。
    M33 恢复跨行继承（第 5–7 轮 B1 的四种形态）· M35 漂移 key
    M26–M31 台账六条 · M23/M24/M25 报错三行化与理念句

一格可以改**多处**（`old`/`new` 给成等长列表），用于「同一个判据块由两条正则实现」的场合。

用法：
    python scripts/constraints-mutate.py
    python scripts/constraints-mutate.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "constraints-check.py"
SELFTEST = ROOT / "scripts" / "constraints-selftest.py"

# (格号, 说明, 原文片段（str 或 list）, 替换成（str 或 list）, 预期变红的用例前缀)
MUTATIONS = [
    # --- 抽取面（第 1–4 轮资产，格号沿用含义）--------------------------------
    ("M01", "去掉禁限词的前置边界 → 词内假阳性复活",
     'PROHIBIT_RE = re.compile(r"(?:^|[\\s，。；：、（）()「」·—!?\\"“”]|" + _PROHIBIT_ALLOW_PREFIX +\n'
     '                         r")(?:" + PROHIBIT_WORDS + ")")',
     'PROHIBIT_RE = re.compile(PROHIBIT_WORDS)', ["T12"]),
    ("M02", "去掉行长上限 → 长篇叙述被当条款",
     '        if not line or len(line) > MAX_LINE:', '        if not line:', ["T14"]),
    ("M03", "去掉「规定式句子」判据 → 叙述句被当条款",
     '        if not PRESCRIPTIVE_RE.search(line):\n            continue\n', '', ["T13"]),
    ("M04", "不跳已划掉条文 → 废止条款仍被当有效",
     'if "~~" in line:', 'if False:', ["T09"]),
    ("M05", "不跳引用块 → 别人的引用被当本方条款",
     '        if line.lstrip().startswith(">"):   # 引用/证据块，不是本方订立的条款\n            continue\n',
     '', ["T11"]),
    ("M06", "围栏开启分支失效 → 示例代码里的禁令被当条款",
     '        m = FENCE_RE.match(raw)\n        if m:\n            fence_char, fence_len = m.group(1)[0], len(m.group(1))',
     '        m = None\n        if m:\n            fence_char, fence_len = m.group(1)[0], len(m.group(1))',
     ["T10", "T56", "T59"]),
    ("M07", "不认四空格/Tab 缩进块 → 缩进示例被当条款",
     '        if raw.strip() and INDENT_CODE_RE.match(raw) and (i == 0 or not lines[i - 1].strip()):',
     '        if False:', ["T57"]),
    ("M08", "缩进块不再要求「上一行为空」→ 列表续行被误当代码块吞掉",
     '        if raw.strip() and INDENT_CODE_RE.match(raw) and (i == 0 or not lines[i - 1].strip()):',
     '        if raw.strip() and INDENT_CODE_RE.match(raw):', ["T58"]),
    ("M09", "围栏不按长度配对 → 短的能关长的，围栏提前结束",
     '                    and len(m.group(1)) >= fence_len and not m.group(2).strip()):',
     '                    and len(m.group(1)) >= 3 and not m.group(2).strip()):', ["T59"]),

    # --- 判定面：标签 --------------------------------------------------------
    ("M10", "完全不认标签 → 所有带标签的条款都被当无出处",
     '        tag = TAG_RE.search(text)',
     '        tag = None',
     ["T05", "T06", "T07", "T08", "T15", "T16", "T17", "T18", "T19", "T29",
      "T42", "T43", "T44", "T45", "T65"]),
    ("M12", "未知标签当成已批准 → UNKNOWN-ID 被吞",
     '            entry = lookup(registry, cid)',
     '            entry = lookup(registry, cid) or {"status": "approved", "quote": "x"}',
     ["T06", "T17", "T42", "T44"]),
    ("M13", "不再检查 status=approved → proposed 的条目被当已批准",
     '            elif entry.get("status") != "approved" or not entry.get("quote"):',
     '            elif not entry.get("quote"):', ["T07", "T18", "T45"]),
    ("M14", "不再检查 quote 非空 → 空出处也算批准",
     ' or not entry.get("quote"):', ':', ["T08", "T19"]),
    ("M15", "查错表（E-/I- 也去 entries 里找）→ 豁免条目全部 UNKNOWN-ID",
     '    if tag_id.startswith("P-"):\n        return registry["entries"].get(tag_id)\n'
     '    return registry["exemptions"].get(tag_id)',
     '    return registry["entries"].get(tag_id)',
     ["T15", "T16", "T18", "T19", "T29", "T43", "T65"]),

    # --- 判定面：baseline 与常量表 ------------------------------------------
    ("M16", "baseline 只按文本比、丢掉路径键 → 同文本换文件就免检",
     '        if f"{path_s}|{digest(text)}" in baseline:',
     '        if any(k.endswith("|" + digest(text)) for k in baseline):', ["T33"]),
    ("M17", "空清单放行 → 漏给清单变成豁免",
     '            print("[gate] EMPTY-CHANGED 漏给清单不是豁免，是漏检 → exit 2")\n            return 2',
     '            print("[gate] EMPTY-CHANGED 漏给清单不是豁免，是漏检 → exit 2")\n            return 0',
     ["T01", "T02"]),
    ("M18", "不校验常量表 schema → 坏常量表被静默接受",
     '    if data.get("schema") != "constraints-v1":', '    if False:', ["T36"]),
    ("M19", "不校验豁免条目 id 形态 → 表里混进 Evil / E001 也照跑",
     '        if not re.fullmatch(r"[EI]-\\d{3}", e["id"]):',
     '        if e["id"][0] not in ("E", "I"):', ["T64", "T64b"]),
    ("M20", "理念句缺失也不报错 → 闸门自己的话可以没有出处",
     '    if not isinstance(guidance, str) or len(guidance.strip()) < 4:', '    if False:', ["T37"]),
    ("M21", "--write-baseline 也登记带标签的行 → 依据变成可选装饰",
     '        untagged = [(r[0], digest(r[2])) for r in rows\n'
     '                    if not (TAG_RE.search(r[2]) or TAG_RE.search(r[3] or ""))]',
     '        untagged = [(r[0], digest(r[2])) for r in rows]', ["T35"]),
    ("M22", "--defs 不做逐字比较 → 定义漂移不再被发现",
     '            if idx < 0:', '            if False:', ["T61"]),

    # --- 报错三行化 ---------------------------------------------------------
    ("M23", "报错不给「为什么」→ agent 只能猜，最省事的修法变成改判据",
     '    out.append("      为什么：" + why_text(v["kind"], v.get("id"), v.get("suggestion"), registry))',
     '    out.append("")', ["T17", "T30", "T39", "T42"]),
    ("M24", "末尾总则缺失 → 没人告诉 agent「改的是句子不是判据」",
     '    if violations:\n        lines_out += render_footer(registry["guidance"], rel(log_path), registry)',
     '    if False:\n        lines_out += render_footer(registry["guidance"], rel(log_path), registry)',
     ["T31", "T40"]),
    ("M25", "理念句硬编码在检查器里、不读常量表 → 闸门自己违反自己的规则",
     '        lines_out += render_violation(v, registry["guidance"], registry)',
     '        lines_out += render_violation(v, "自限条款不应当封锁潜在技术路径。", registry)', ["T41"]),

    # --- 数据台账 -----------------------------------------------------------
    ("M26", "inventory 不写 by 字段 → 答不了「这句靠哪条放行的」",
     '            "by": by_keys.get((f, ln)),', '            "by": None,', ["T43"]),
    ("M27", "inventory 按「有没有标签」自己算 registered → 被拒绝的行也记成已登记（第 4 轮 B2）",
     '            "registered": (f, ln) in by_keys,',
     '            "registered": bool(TAG_RE.search(text)) or (f, ln) in by_keys,',
     ["T44", "T45"]),
    ("M28", "累积日志改成覆盖写 → 只能看见最后一次运行",
     '    with path.open("a", encoding="utf-8") as fh:',
     '    with path.open("w", encoding="utf-8") as fh:', ["T46", "T65"]),
    ("M29", "--no-log 仍然写日志 → 台子/探针会污染真实台账",
     '    if not args.no_log:', '    if True:', ["T47"]),
    ("M30", "--stats 不统计 why → 频次报告只剩空段",
     '        why_count[v.get("why") or "?"] = why_count.get(v.get("why") or "?", 0) + 1',
     '        pass', ["T49"]),
    ("M31", "--stats 不检测依据漂移 → 换依据也看不出来",
     '    drift = {k: v for k, v in drift.items() if len(v["bys"]) > 1}',
     '    drift = {}', ["T50", "T65"]),

    # --- 本轮最重要的一格：建议器不许参与判定 ------------------------------
    ("M32", "**建议器接进判定** → 无标签的句子只要「像」某条豁免就被放行（第 4 轮穿透反例复活）",
     '        violations.append({"kind": "NO-PROVENANCE", "id": None, "file": path_s,\n'
     '                           "line": ln, "text": text, "why": "no-exempt-tag",\n'
     '                           "suggestion": suggest_exemption(text, registry)})',
     '        _sug = suggest_exemption(text, registry)\n'
     '        if _sug:\n'
     '            registered.append({"id": _sug + "(suggested)", "file": path_s, "line": ln})\n'
     '            by_keys[(path_s, ln)] = _sug\n'
     '            reg_count[_sug] = reg_count.get(_sug, 0) + 1\n'
     '            continue\n'
     '        violations.append({"kind": "NO-PROVENANCE", "id": None, "file": path_s,\n'
     '                           "line": ln, "text": text, "why": "no-exempt-tag",\n'
     '                           "suggestion": _sug})',
     # 实测：9 个穿透反例里只有 5 个会被建议器「救活」—— 其余 4 个（门锁 / 按钮 / 绿色主题 /
     # 产品复制）它连建议都给不出。这恰好说明「建议器 = 词表匹配 = 第 4 轮的老毛病」，不能当判据。
     ["T20", "T22", "T25", "T28", "T30", "T54c", "T62", "T63"]),

    # --- 第 5 轮验收官 B1/B2/B3 打出来的三个洞 ----------------------------
    ("M33", "恢复「继承上一行标签」→ 平级列表项 / 引用块 / 标题行都能借标签（第 5–7 轮 B1）",
     '        tag = TAG_RE.search(text)',
     '        tag = TAG_RE.search(text) or TAG_RE.search(prev or "")',
     ["T54", "T54b", "T54c", "T62", "T63"]),
    ("M34", "豁免 id 只查首字母 → Evil / E001 混进表里（第 5 轮 B2）",
     '        if not re.fullmatch(r"[EI]-\\d{3}", e["id"]):',
     '        if e["id"][0] not in ("E", "I"):',
     ["T64", "T64b"]),
    ("M35", "漂移 key 用含标签原文 → 改标签就算新句式，漂移永远看不见（第 5 轮 B3）",
     '            "rows": [[r[0], digest(TAG_RE.sub("", r[2])), by_keys.get((r[0], r[1]))]\n'
     '                     for r in rows][:LOG_MAX_ROWS],',
     '            "rows": [[r[0], digest(r[2]), by_keys.get((r[0], r[1]))] for r in rows][:LOG_MAX_ROWS],',
     ["T65"]),
]


def run_selftest(script: Path) -> tuple[dict, str]:
    env = {"PYTHONIOENCODING": "utf-8", "PATH": os.environ.get("PATH", "")}
    # ⚠️ 2026-11-24 修回归：`constraints-selftest.py` 在 2026-11-22 加了「TEMP 在工程树内就拒跑」
    # 的守卫，而这里只传两个环境变量 → 子进程拿不到 TEMP/TMP/TMPDIR，`tempfile.gettempdir()`
    # 一路退到 cwd（= 工程根），守卫当场 print 三行并 exit 2 → 变异台从那天起只会输出
    # 「干净镜像没跑出结果，台子坏了」。不是判据坏了，是**环境没传下去**。
    # 修法：把宿主的这几个变量原样传下去（它们本来就指向工程外）。
    for k in ("TEMP", "TMP", "TMPDIR", "LOCALAPPDATA"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    proc = subprocess.run(
        [sys.executable, str(SELFTEST), "--script", str(script), "--quiet", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=1800)
    out = (proc.stdout or "") + (proc.stderr or "")
    payload = None
    for line in out.splitlines():
        if line.startswith("JSON="):
            payload = json.loads(line[5:])
    if payload is None:
        return {}, out
    return {r["name"]: r["pass"] for r in payload}, out


def reds(results: dict[str, bool]) -> set[str]:
    def key(name: str) -> str:
        return name.split(" ")[0]
    return {key(n) for n, ok in results.items() if not ok}


def apply_mutation(src: str, old, new) -> str:
    olds = [old] if isinstance(old, str) else list(old)
    news = [new] if isinstance(new, str) else list(new)
    out = src
    for o, n in zip(olds, news):
        out = out.replace(o, n, 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default=None, help="只跑某几格（逗号分隔）")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    # --- 干净镜像先全绿 ---
    clean, out = run_selftest(CHECKER)
    if not clean:
        print("[mutate] 干净镜像没跑出结果，台子坏了")
        print(out[-800:])
        return 2
    clean_red = reds(clean)
    if clean_red:
        print(f"[mutate] 干净镜像未全绿，红不作数：{sorted(clean_red)}")
        return 2
    print(f"[mutate] 干净镜像 {len(clean)}/{len(clean)} 绿 ✓")

    picked = {m.strip() for m in args.only.split(",")} if args.only else None
    tmp = Path(tempfile.mkdtemp(prefix="constraints-mutate-"))
    rows = []
    bad = 0
    void = 0
    try:
        src = CHECKER.read_text(encoding="utf-8")
        for mid, desc, old, new, expect in MUTATIONS:
            if picked and mid not in picked:
                continue
            olds = [old] if isinstance(old, str) else list(old)
            if any(o not in src for o in olds):
                rows.append({"id": mid, "desc": desc, "status": "PATTERN-MISS",
                             "expect": expect, "got": []})
                bad += 1
                print(f"[PATTERN-MISS] {mid} {desc}")
                continue
            mutated = apply_mutation(src, old, new)
            target = tmp / f"checker-{mid}.py"
            target.write_text(mutated, encoding="utf-8")
            results, _ = run_selftest(target)
            if not results:
                rows.append({"id": mid, "desc": desc, "status": "NO-OUTPUT",
                             "expect": expect, "got": []})
                bad += 1
                continue
            got = sorted(reds(results))
            if not got:
                rows.append({"id": mid, "desc": desc, "status": "VOID",
                             "expect": expect, "got": got})
                void += 1
                bad += 1
                print(f"[VOID] {mid} {desc}  —— 打坏了却没人红")
                continue
            ok = set(got) == set(expect)
            rows.append({"id": mid, "desc": desc,
                         "status": "OK" if ok else "MISMATCH",
                         "expect": expect, "got": got})
            if not ok:
                bad += 1
            print(f"[{'OK' if ok else 'MISMATCH'}] {mid} {desc}"
                  f"  预期红={expect} 实红={got}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT={len(rows) - bad}/{len(rows)} 格  void={void}  "
          f"{'全绿' if bad == 0 else '有未抓/多抓，判失败'}")
    if args.json:
        print("JSON=" + json.dumps(rows, ensure_ascii=False))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
