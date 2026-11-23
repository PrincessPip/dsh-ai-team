#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-sync.py · 文档↔代码对账闸门

    python scripts/doc-sync.py [登记表] [--root DIR] [--json] [--out FILE] [--list]

## 为什么要有它

`tasks/LEDGER-JOB-2026-11-11/` 一个单元里，「文档与代码之间那一步没人核」犯了**六次**：
策略里写了一版更合理的规格、然后当代码已经那样做了；头注释没同步；说明声称改了两处而实际只改一处；
账本写「已改」而文件没动；附二写「已记」而没记。变异台只测代码不测文档 ——
这类问题没有任何机器检查盯着，全靠下一个人读的时候发现。

它的第一个真实被试件是 `DOC-SYNC-2026-11-11` 自己修的那个契约 bug：
`INC-TEMPLATE.md` §五 的四件名单 与 `scripts/fire-chief.py` 的 `ARTIFACT_FILES` 是同一个事实的
两份副本，而它们曾经不一致到让 `exit 6` 分不清「工人没交作业」和「契约写错」。

## 它不做的事（先说清楚，免得期待错位）

**不做全文档 token 扫描，不做自然语言理解。** 只核**登记过**的副本：
一条登记 = 一句人话（`why`）+ 几处「同一事实的副本」+ 一致性规则。
没登记的副本它不管 —— 「扫出来一堆没人认领的」不是闸门，是噪音源，
而且会逼着人给闸门加一堆白名单，最后白名单比正文还长。

## 判据

| 出口码 | 意思 |
|---|---|
| `0` | 所有登记项一致 |
| `1` | 有登记项不一致（`MISMATCH` / `EMPTY_EXTRACTION` / `MISSING_FILE` / `LOCATE_MISSED`） |
| `2` | 登记表读不了或不合法（**不许当成「0 项检查，全过」**） |

三条硬规则：

1. **空集不通**（`set_equal` / `subset`）：抽不到 token 说明「这条登记已经和文档脱钩了」，
   它此刻既不能证明一致、也不能证明不一致 —— 判红并说「抓不到」。放过空集的话，
   闸门会在文档改写之后静默变成一句「OK」，比没有更糟。
2. **输出必须自证查了哪几个文件**：每条 copy 打印 `file:行号`。只打 `RESULT=OK` 的闸门
   等于要求人相信它 —— 而「证据要自证范围」是本工程反复付学费学到的（见账本 2026-11-11 更正行）。
3. **登记表不合法一律 `exit 2`**：`checks` 为空、`rule` 不认识、正则编不过，都不许降级成「全过」。

## 登记表格式

```json
{
  "version": 1,
  "checks": [
    {
      "id": "fire-artifacts",
      "why": "为什么这两处必须是同一个事实（人话，写给以后接手的人）",
      "rule": "set_equal | equal | subset",
      "copies": [
        {"file": "INC-TEMPLATE.md", "locate": "(?ms)^## 五.*?^## 六", "tokens": "filename", "note": "模板 §五"}
      ]
    }
  ]
}
```

* `locate` 是**内联标志自带的**正则（`(?m)` / `(?s)` 自己写），用来圈出「副本所在的那一段」；
  不写 `locate` 等于整文件。
* `tokens` 决定怎么从那段里抽「事实」：`filename` / `artifact` / `quoted` / `backtick` / `count` / `line` / `table_first_col`。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_REGISTRY = HERE / "doc-sync.json"

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_REGISTRY = 2

RULES = ("equal", "set_equal", "subset")

def _table_first_col(body):
    """只抽表格**第一格**里反引号包裹的 token。

    为什么非要这一个抽取器（第 1 轮验收官 B2 实测出来的漏报）：
    `INC-TEMPLATE.md` §五 那张表的「内容」列里会**再次提到**同表其它文件名 ——
    `VERIFY.md` 那行写着「证据位置（`TRANSCRIPT.log` 第几段 + file:line）」。
    按整段抽 token 的话，**删掉 `TRANSCRIPT.log` 那一行**时，`VERIFY.md` 行里的引用
    会把它补回集合，闸门照样 `RESULT=OK`。而「删了一行」正是「名字漂移」最基本的一种。
    所以表格式副本必须只认第一格。
    """
    out = set()
    for line in body.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = s.strip("|").split("|")
        if not cells:
            continue
        m = re.fullmatch(r"\s*`([^`\n]+)`\s*", cells[0])
        if m:
            out.add(m.group(1).strip())
    return sorted(out)


# `line` 是给「一句话的原文副本」用的：整段压空白后当一条 token。
# `count` 给「同一个数字写在两处」用的：抽 `N 次`。改上限只改一处时它会立刻红。
# `artifact` 是 `filename` 的窄版：只要 `.log / .md / .txt`。
# 为什么窄版是默认需求：四件名单那种表格旁边经常紧挨着 `scripts/xxx.py` 这种引用，
# 宽抽法会把脚本体也卷进集合，闸门就变成「一改引用就红」的噪音源。
# `table_first_col` 见上面那段：表格副本必须只认第一格，否则删行会被别处的引用补回来。
TOKEN_EXTRACTORS = {
    "filename": re.compile(r"[A-Za-z0-9_.\-]+\.(?:log|md|txt|json|py|ps1|mjs|yml|yaml)"),
    "artifact": re.compile(r"[A-Za-z0-9_.\-]+\.(?:log|md|txt)"),
    "quoted": re.compile(r"[\"']([^\"'\n]+)[\"']"),
    "backtick": re.compile(r"`([^`\n]+)`"),
    "count": re.compile(r"(\d+)\s*次"),
    "table_first_col": _table_first_col,
    "line": None,   # 特判：整段压空白
}


def die(code, msg):
    print("[doc-sync] " + msg, file=sys.stderr, flush=True)
    sys.exit(code)


# ------------------------------------------------------------------ 登记表

def load_registry(path: Path):
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        die(EXIT_REGISTRY, "登记表读不了：%s（%s）" % (path, e))
    try:
        reg = json.loads(raw)
    except Exception as e:
        # 格式串两个占位符，两个参数 —— 一个 `%s` 塞两个值会抛 TypeError，
        # 于是 `exit 2` 变成未捕获 traceback（本行曾是 `% (path, e)` 的单占位版本，
        # 被 `selftest-doc-sync.py` 的 S06 当场抓住）。
        die(EXIT_REGISTRY, "登记表不是合法 JSON：%s（%s）" % (path, e))

    if not isinstance(reg, dict):
        die(EXIT_REGISTRY, "登记表顶层必须是对象。")
    if reg.get("version") != 1:
        die(EXIT_REGISTRY, "登记表 version 必须是 1，实际 %r。" % reg.get("version"))
    checks = reg.get("checks")
    if not isinstance(checks, list) or not checks:
        # 空登记表是**最危险**的形态：跑出来 0 项全过，看起来比任何一次都绿。
        die(EXIT_REGISTRY, "登记表 checks 为空 —— 空登记表等于「不检查」，不许当通过。")

    seen = set()
    for i, c in enumerate(checks, 1):
        where = "第 %d 条登记" % i
        if not isinstance(c, dict):
            die(EXIT_REGISTRY, "%s 不是对象。" % where)
        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            die(EXIT_REGISTRY, "%s 缺 id。" % where)
        if cid in seen:
            die(EXIT_REGISTRY, "id 重复：%s —— 重复的 id 会让「哪条红了」说不清。" % cid)
        seen.add(cid)
        if c.get("rule") not in RULES:
            die(EXIT_REGISTRY, "%s（%s）的 rule=%r 不认识，只支持 %s。"
                % (where, cid, c.get("rule"), " / ".join(RULES)))
        if not isinstance(c.get("why"), str) or not c["why"].strip():
            die(EXIT_REGISTRY, "%s（%s）的 why 必须是非空字符串 —— 登记项要能自己解释"
                               "「为什么这是一个事实」。" % (where, cid))
        copies = c.get("copies")
        if not isinstance(copies, list) or len(copies) < 2:
            die(EXIT_REGISTRY, "%s（%s）至少要 2 处副本，实际 %r —— 一处副本没什么可对账的。"
                % (where, cid, len(copies) if isinstance(copies, list) else copies))
        for j, cp in enumerate(copies, 1):
            cw = "%s（%s）的第 %d 处副本" % (where, cid, j)
            if not isinstance(cp, dict):
                die(EXIT_REGISTRY, "%s 不是对象。" % cw)
            # 类型必须先查：`cp["tokens"] in TOKEN_EXTRACTORS` 对 list 会抛
            # `TypeError: unhashable type`，`root / 123` 会抛 `TypeError` ——
            # 两者都退成**未捕获异常 + 退出码 1**，跟「真不一致」撞码（第 1 轮验收官 N1）。
            for k in ("file", "tokens", "note"):
                if not isinstance(cp.get(k), str) or not cp[k].strip():
                    die(EXIT_REGISTRY, "%s 的 %s 必须是非空字符串，实际 %r。"
                        % (cw, k, cp.get(k)))
            if cp.get("locate") is not None and not isinstance(cp["locate"], str):
                die(EXIT_REGISTRY, "%s 的 locate 必须是字符串（或直接省略 = 整文件），实际 %r。"
                    % (cw, cp.get("locate")))
            if cp["tokens"] not in TOKEN_EXTRACTORS:
                die(EXIT_REGISTRY, "%s 的 tokens=%r 不认识，只支持 %s。"
                    % (cw, cp["tokens"], " / ".join(TOKEN_EXTRACTORS)))
            if cp.get("locate"):
                try:
                    re.compile(cp["locate"])
                except re.error as e:
                    die(EXIT_REGISTRY, "%s 的 locate 正则编不过：%s" % (cw, e))
    return reg


# ------------------------------------------------------------------ 提取

def extract_copy(root: Path, copy: dict):
    """抽一处副本。返回 {tokens, raw, lines, error?}。

    `error` 只有三种：`MISSING_FILE`（文件没了）/ `LOCATE_MISSED`（正则圈不到东西）/
    抽到 0 个 token。三种都判红 —— 它们共同的意思是「这条登记已经和文档脱钩」。
    """
    path = root / copy["file"]
    if not path.is_file():
        return {"error": "MISSING_FILE", "detail": str(copy["file"])}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        return {"error": "MISSING_FILE", "detail": "%s（%s）" % (copy["file"], e)}

    spans = []
    if copy.get("locate"):
        for m in re.compile(copy["locate"]).finditer(text):
            spans.append((m.start(), m.end(), m.group(0)))
        if not spans:
            return {"error": "LOCATE_MISSED", "detail": copy["locate"]}
    else:
        spans.append((0, len(text), text))

    body = "\n".join(s[2] for s in spans)
    kind = TOKEN_EXTRACTORS[copy["tokens"]]
    if kind is None:
        norm = " ".join(body.split())
        tokens = [norm] if norm else []
    elif callable(kind):
        tokens = kind(body)
    else:
        tokens = sorted({t.strip() for t in kind.findall(body) if t.strip()})

    lines = sorted({text.count("\n", 0, s[0]) + 1 for s in spans})
    return {"tokens": tokens, "raw": " ".join(body.split()), "lines": lines}


def judge_check(root: Path, check: dict):
    copies = [dict(meta=cp, **extract_copy(root, cp)) for cp in check["copies"]]
    verdict = "OK"
    detail = []

    for i, c in enumerate(copies, 1):
        if "error" in c:
            verdict = c["error"]
            detail.append("copy #%d（%s）：%s" % (i, c["meta"]["file"], c["error"]))
    if verdict == "OK":
        if any(not c["tokens"] for c in copies):
            verdict = "EMPTY_EXTRACTION"
            for i, c in enumerate(copies, 1):
                if not c["tokens"]:
                    detail.append("copy #%d（%s）抽到 0 项 —— 登记已与文档脱钩，不能算一致"
                                  % (i, c["meta"]["file"]))

    if verdict == "OK":
        rule = check["rule"]
        if rule == "equal":
            head = copies[0]["raw"]
            for i, c in enumerate(copies[1:], 2):
                if c["raw"] != head:
                    verdict = "MISMATCH"
                    detail.append("copy #%d 与 copy #1 原文不等" % i)
        elif rule == "set_equal":
            head = set(copies[0]["tokens"])
            for i, c in enumerate(copies[1:], 2):
                other = set(c["tokens"])
                if other != head:
                    verdict = "MISMATCH"
                    only_head = sorted(head - other)
                    only_other = sorted(other - head)
                    detail.append("copy #%d 与 copy #1 集合不等：只在 #1 = %s；只在 #%d = %s"
                                  % (i, only_head or "（无）", i, only_other or "（无）"))
        else:  # subset：copy #1 ⊆ 其余每一处
            head = set(copies[0]["tokens"])
            for i, c in enumerate(copies[1:], 2):
                missing = sorted(head - set(c["tokens"]))
                if missing:
                    verdict = "MISMATCH"
                    detail.append("copy #1 有 %d 项 copy #%d 没有：%s" % (len(missing), i, missing))

    return {"id": check["id"], "why": check["why"], "rule": check["rule"],
            "copies": copies, "verdict": verdict, "detail": detail}


# ------------------------------------------------------------------ 输出

def render(cid, res):
    lines = []
    ap = lines.append
    ap("[check] %s  rule=%s" % (cid, res["rule"]))
    ap("  why: %s" % res["why"])
    for i, c in enumerate(res["copies"], 1):
        meta = c["meta"]
        if "error" in c:
            ap("  copy #%d  %s  <-- %s" % (i, meta["file"], c["error"]))
            continue
        where = "%s:%s" % (meta["file"], ",".join(str(n) for n in c["lines"]))
        ap("  copy #%d  %-46s %2d 项  「%s」" % (i, where, len(c["tokens"]), meta["note"]))
        for t in c["tokens"]:
            ap("             %s" % t)
    for d in res["detail"]:
        ap("  !! %s" % d)
    ap("  => %s" % res["verdict"])
    return lines


def main():
    ap = argparse.ArgumentParser(description="文档↔代码对账闸门：核登记过的事实副本是否仍一致")
    ap.add_argument("registry", nargs="?", default=str(DEFAULT_REGISTRY),
                    help="登记表路径（默认 scripts/doc-sync.json）")
    ap.add_argument("--root", default=str(ROOT), help="工程根（默认按本脚本位置推断）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--list", action="store_true", help="只列登记项，不检查")
    ap.add_argument("--out", default=None, help="把输出写进文件（UTF-8，由本脚本直接落盘）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        die(EXIT_REGISTRY, "--root 不存在：%s" % root)
    reg = load_registry(Path(args.registry))

    if args.list:
        out = ["登记 %d 项（%s）" % (len(reg["checks"]), Path(args.registry).as_posix())]
        for c in reg["checks"]:
            out.append("  %-28s %-9s %d 处副本  %s"
                       % (c["id"], c["rule"], len(c["copies"]), c["why"]))
        text = "\n".join(out) + "\n"
        sys.stdout.write(text)
        if args.out:
            Path(args.out).write_bytes(text.encode("utf-8"))
        return EXIT_OK

    results = [judge_check(root, c) for c in reg["checks"]]
    failed = [r for r in results if r["verdict"] != "OK"]

    if args.json:
        payload = {
            "root": root.as_posix(),
            "registry": Path(args.registry).as_posix(),
            "checks": [{"id": r["id"], "rule": r["rule"], "verdict": r["verdict"],
                        "detail": r["detail"],
                        "copies": [{"file": c["meta"]["file"], "lines": c.get("lines", []),
                                    "tokens": c.get("tokens", []),
                                    "error": c.get("error")} for c in r["copies"]]}
                       for r in results],
            "result": "OK" if not failed else "FAIL",
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        lines = ["doc-sync · 登记 %d 项 / 副本 %d 处"
                 % (len(results), sum(len(r["copies"]) for r in results)),
                 "root: %s" % root.as_posix(),
                 "registry: %s" % Path(args.registry).as_posix(),
                 ""]
        for r in results:
            lines += render(r["id"], r)
            lines.append("")
        lines.append("RESULT=%s checks=%d failed=%d files=%d"
                     % ("OK" if not failed else "FAIL", len(results), len(failed),
                        len({c["meta"]["file"] for r in results for c in r["copies"]})))
        text = "\n".join(lines) + "\n"

    sys.stdout.write(text)
    if args.out:
        Path(args.out).write_bytes(text.encode("utf-8"))
    return EXIT_OK if not failed else EXIT_MISMATCH


if __name__ == "__main__":
    sys.exit(main())
