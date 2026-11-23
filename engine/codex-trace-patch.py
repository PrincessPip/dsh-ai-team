#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TRACE-PATCH-v1 · 给 @deepseek-ai/dsh-subagent-codex 打「工具轨迹回传」补丁。

为什么是就地补丁 + 守卫：插件是 npm 发布产物（`lib/index.js`，无 src），
`pnpm add` / dsh 升级会把它覆盖掉。所以补丁必须**可重放**（--apply 幂等），
并且**能证明还在**（--check 退出码 0/1，挂在跑 codex 之前）。

用法（工程根执行）：
    python scripts/codex-trace-patch.py --check      # 默认动作；READY / NOT-READY
    python scripts/codex-trace-patch.py --apply      # 打补丁（可重复执行）
    python scripts/codex-trace-patch.py --revert     # 回到上游原版
    python scripts/codex-trace-patch.py --apply --force   # 上游换版本后强制重建基线

退出码：0 = OK / 1 = 缺失或漂移 / 2 = 结构性错误（锚点不唯一、上游版本变化）

设计约束：
1. **行级锚点**（按整行 strip 后比较），不用大段文本匹配 —— 上游改缩进不会误伤。
2. 每个锚点必须**恰好出现 1 次**，任何一步不满足就整体放弃、**不写盘**。
3. 首次 --apply 把上游原版存成 `index.js.tr4ce-orig`；后续 --apply 都从它重建，
   所以重复执行不会叠加第二份补丁。
4. 基线漂移（index.js 既不含补丁标记、又不等于基线）→ 拒绝动手，要求 --force，
   否则会把上游的新版本内容覆盖成旧基线。
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path.home()
PLUGIN_LIB = HOME / ".dsh" / "profiles" / "web" / "node_modules" / "@deepseek-ai" / "dsh-subagent-codex" / "lib"
INDEX = PLUGIN_LIB / "index.js"
TRACE_DST = PLUGIN_LIB / "trace.mjs"
BASELINE = PLUGIN_LIB / "index.js.tr4ce-orig"
BASELINE_SHA = PLUGIN_LIB / "index.js.tr4ce-orig.sha256"

ROOT = Path(__file__).resolve().parents[1]
TRACE_SRC = ROOT / "tasks" / "CODEX-TRACE-2026-11-10" / "patch" / "trace.mjs"

MARK = "TRACE-PATCH-v1"

# (kind, anchor, payload)
#   replace       : 把整行替换成 payload（沿用该行原缩进）
#   insert_after  : 在匹配行之后插入 payload 各行（payload 自带绝对缩进）
#   insert_before : 在匹配行之前插入 payload 各行
OPS = [
    (
        "replace",
        "import { readFileSync, writeFileSync } from \"node:fs\";",
        "import { mkdirSync, readFileSync, writeFileSync } from \"node:fs\"; // TRACE-PATCH-v1",
    ),
    (
        "replace",
        "import { dirname, resolve } from \"node:path\";",
        "import { dirname, join, resolve } from \"node:path\"; // TRACE-PATCH-v1",
    ),
    (
        "insert_after",
        "import { dirname, join, resolve } from \"node:path\"; // TRACE-PATCH-v1",
        [
            'import { homedir } from "node:os"; // TRACE-PATCH-v1',
            'import { appendTrace, normalizeTraceOptions, renderTrace, writeTraceFile } from "./trace.mjs"; // TRACE-PATCH-v1',
        ],
    ),
    (
        "replace",
        "constructor(input, output, permissionMode, model) {",
        "constructor(input, output, permissionMode, model, trace) { // TRACE-PATCH-v1",
    ),
    (
        "insert_after",
        "this.model = model;",
        [
            "\t\tthis.trace = []; // TRACE-PATCH-v1",
            "\t\tthis.traceOptions = normalizeTraceOptions(trace); // TRACE-PATCH-v1",
        ],
    ),
    (
        "insert_before",
        'if (this.recordDeclinedItem(item, order)) return;',
        [
            "\t\t\tappendTrace(this.trace, item, this.traceOptions); // TRACE-PATCH-v1 先收：declined 的也算证据",
        ],
    ),
    (
        "replace",
        "return selected !== void 0 && selected.trim().length > 0 ? [{",
        "const blocks = selected !== void 0 && selected.trim().length > 0 ? [{",
    ),
    (
        "replace",
        "}] : [];",
        "}] : [];\n"
        "\t\t// TRACE-PATCH-v1:begin\n"
        "\t\t// 落盘与「进块」解耦：没有答案的运行一样要留证据（第 1 轮 N1）。\n"
        "\t\tif (this.traceFlushed !== true) {\n"
        "\t\t\tthis.traceFlushed = true;\n"
        "\t\t\tconst written = writeTraceFile(this.trace, this.traceOptions, { provider: \"codex\" }, { fs: { mkdirSync, writeFileSync }, path: { join }, os: { homedir } });\n"
        "\t\t\tif (written && written.error) this.traceWriteError = written.error;\n"
        "\t\t\telse if (written && written.path) this.tracePath = written.path;\n"
        "\t\t}\n"
        "\t\t// 只在**已有答案**时附轨迹块：runTurn 用 output.length === 0 判「没答案」，\n"
        "\t\t// 让轨迹块把空答案顶成非空会掩盖真实故障（第 1 轮 B2）。\n"
        "\t\tif (blocks.length > 0) {\n"
        "\t\t\tconst traceBlock = renderTrace(this.trace, this.traceOptions);\n"
        "\t\t\tif (traceBlock !== void 0) {\n"
        "\t\t\t\tlet text = traceBlock;\n"
        "\t\t\t\tif (this.traceWriteError) text += \"\\n\\n> trace 落盘失败：\" + this.traceWriteError;\n"
        "\t\t\t\telse if (this.tracePath) text += \"\\n\\n落盘：`\" + this.tracePath + \"`\";\n"
        "\t\t\t\tblocks.push({ type: \"text\", text });\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t\t// TRACE-PATCH-v1:end\n"
        "\t\treturn blocks;",
    ),
    (
        # 原来这一行是对象最后一项、**没有尾逗号**，所以必须整行替换而不是后面插入
        "replace",
        "disposeGraceMs: z.number().default(DEFAULT_DISPOSE_GRACE_MS)",
        "disposeGraceMs: z.number().default(DEFAULT_DISPOSE_GRACE_MS),\n"
        "\t// TRACE-PATCH-v1: 轨迹默认 summary（进上下文但截断），全文另落盘\n"
        '\ttrace: z.string().default("summary"),\n'
        "\ttraceMaxItems: z.number().default(40),\n"
        "\ttraceMaxChars: z.number().default(4000),\n"
        '\ttraceDir: z.string().default(""),',
    ),
    (
        # 同上：resolved 对象里也没有尾逗号
        "replace",
        "disposeGraceMs: config.disposeGraceMs",
        "disposeGraceMs: config.disposeGraceMs,\n"
        '\t\ttrace: config.trace ?? "summary", // TRACE-PATCH-v1\n'
        "\t\ttraceMaxItems: config.traceMaxItems ?? 40,\n"
        "\t\ttraceMaxChars: config.traceMaxChars ?? 4000,\n"
        '\t\ttraceDir: config.traceDir ?? "",',
    ),
    (
        "insert_after",
        "disposeGraceMs: this.config.disposeGraceMs,",
        [
            "\t\t\ttrace: { mode: this.config.trace, maxItems: this.config.traceMaxItems, maxChars: this.config.traceMaxChars, dir: this.config.traceDir }, // TRACE-PATCH-v1",
        ],
    ),
    (
        "replace",
        "const wire = new CodexAppServerWire(child.stdout, child.stdin, spec.permissionMode, spec.model);",
        'const wire = new CodexAppServerWire(child.stdout, child.stdin, spec.permissionMode, spec.model, spec.trace); // TRACE-PATCH-v1',
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def syntax_error(path: Path):
    """把 .js 复制成 .mjs 让 node --check 按 ESM 语法解析；返回错误文本或 None。"""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8", newline="\n") as fh:
            fh.write(read_text(path))
            tmp = fh.name
        proc = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return None
        return (proc.stderr or proc.stdout or "node --check failed")[:600]
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def find_unique(lines, needle):
    hits = [i for i, line in enumerate(lines) if line.strip() == needle.strip()]
    if len(hits) != 1:
        # 用 2 表示「结构性错误」，与 docstring 的退出码表一致（第 2 轮验收官 N5：
        # SystemExit(字符串) 实际退 1）。
        print("[FATAL] 锚点不唯一（%d 处）：%r —— 上游结构变了，拒绝动手" % (len(hits), needle.strip()[:80]))
        raise SystemExit(2)
    return hits[0]


def build_patched(original: str) -> str:
    lines = original.split("\n")
    for kind, anchor, payload in OPS:
        idx = find_unique(lines, anchor)
        if kind == "replace":
            lines[idx] = indent_of(lines[idx]) + payload
        elif kind == "insert_after":
            lines[idx + 1 : idx + 1] = payload
        elif kind == "insert_before":
            lines[idx:idx] = payload
        else:  # pragma: no cover
            raise SystemExit("[FATAL] 未知操作 " + kind)
    text = "\n".join(lines)
    # ROLE-EFFORT-2026-11-13: compose known effort extension AFTER TRACE.
    # Keep original baseline immutable and keep cmd_check's full-text equality.
    effort_ops = [
        ('constructor(input, output, permissionMode, model, trace) { // TRACE-PATCH-v1',
         'constructor(input, output, permissionMode, model, trace, reasoningEffort) { // TRACE-PATCH-v1; EFFORT-PATCH-v1'),
        ('\t\tthis.model = model;',
         '\t\tthis.model = model;\n\t\tthis.reasoningEffort = reasoningEffort; // EFFORT-PATCH-v1'),
        ('\t\t\t\tthreadId,\n\t\t\t\tinput: texts.map',
         '\t\t\t\tthreadId,\n\t\t\t\t...this.reasoningEffort === void 0 ? {} : { effort: this.reasoningEffort }, // EFFORT-PATCH-v1\n\t\t\t\tinput: texts.map'),
        ('spec.permissionMode, spec.model, spec.trace); // TRACE-PATCH-v1',
         'spec.permissionMode, spec.model, spec.trace, spec.reasoningEffort); // TRACE-PATCH-v1; EFFORT-PATCH-v1'),
        ('\tmodel: z.string().min(1),',
         '\tmodel: z.string().min(1),\n\treasoningEffort: z.union(["low", "medium", "high", "xhigh", "max", "ultra"]), // EFFORT-PATCH-v1: optional; omitted preserves CLI default'),
        ('\t\t\t...this.config.model === void 0 ? {} : { model: this.config.model },',
         '\t\t\t...this.config.model === void 0 ? {} : { model: this.config.model },\n\t\t\t...this.config.reasoningEffort === void 0 ? {} : { reasoningEffort: this.config.reasoningEffort }, // EFFORT-PATCH-v1'),
        ('\t\t...config.model === void 0 ? {} : { model: config.model },',
         '\t\t...config.model === void 0 ? {} : { model: config.model },\n\t\t...config.reasoningEffort === void 0 ? {} : { reasoningEffort: config.reasoningEffort }, // EFFORT-PATCH-v1'),
    ]
    for old, new in effort_ops:
        if text.count(old) != 1:
            print('[FATAL] effort anchor missing/ambiguous; refuse write')
            raise SystemExit(2)
        text = text.replace(old, new, 1)
    return text


def effective_text() -> str:
    """补丁生效后 index.js 应该长什么样（用于 --check 逐条比对）。"""
    if not BASELINE.exists():
        print("[FATAL] 基线缺失：%s（先跑 --apply）" % BASELINE)
        raise SystemExit(2)
    return build_patched(read_text(BASELINE))


def cmd_check(verbose: bool = True) -> int:
    problems = []
    if not INDEX.exists():
        problems.append("插件 index.js 不存在：%s" % INDEX)
    if not TRACE_DST.exists():
        problems.append("lib/trace.mjs 不存在（补丁未落）")
    if not BASELINE.exists():
        problems.append("基线 index.js.tr4ce-orig 不存在")
    if problems:
        for p in problems:
            print("[FAIL] " + p)
        print("RESULT=NOT-READY")
        return 1

    if sha256(TRACE_DST) != sha256(TRACE_SRC):
        problems.append(
            "lib/trace.mjs 与源不一致（dst=%s src=%s）" % (sha256(TRACE_DST)[:12], sha256(TRACE_SRC)[:12])
        )

    # 基线本身也要钉住：否则「把基线和 index.js 一起改成缺了 agentMessage 过滤的版本」
    # 仍会判 READY（第 2 轮验收官 N3 的 X9）。
    if BASELINE_SHA.exists():
        recorded = BASELINE_SHA.read_text(encoding="utf-8").strip().upper()
        if recorded != sha256(BASELINE):
            problems.append(
                "基线 index.js.tr4ce-orig 被改过（记录 %s，实际 %s）——「上游原件」不再是原件"
                % (recorded[:12], sha256(BASELINE)[:12])
            )
    else:
        problems.append("缺少基线 sha256 记录 %s（重跑 --apply 生成）" % BASELINE_SHA.name)

    actual = read_text(INDEX)
    expected = effective_text()

    # 唯一权威判据：**逐字全文相等**（第 1 轮验收官 B1：
    # 只比对「含标记的行集合」挡不住 H1 删 blocks.push / H2 挪 appendTrace /
    # H3 return blocks→return [] —— 三者都会让补丁失效却仍报 READY）。
    if actual != expected:
        a_lines = actual.split("\n")
        e_lines = expected.split("\n")
        first = None
        for i in range(max(len(a_lines), len(e_lines))):
            av = a_lines[i] if i < len(a_lines) else "<缺行>"
            ev = e_lines[i] if i < len(e_lines) else "<多行>"
            if av != ev:
                first = (i + 1, av, ev)
                break
        if first is None:
            problems.append("index.js 与「基线+补丁」不等（差异只在行数 %d vs %d）" % (len(a_lines), len(e_lines)))
        else:
            problems.append(
                "index.js 与「基线+补丁」不逐字相等：第一处差异在行 %d\n    实际: %s\n    期望: %s"
                % (first[0], first[1][:140], first[2][:140])
            )

    syn = syntax_error(INDEX)
    if syn is not None:
        problems.append("index.js 语法检查失败：" + syn.replace("\n", " ")[:300])

    if verbose:
        for p in problems:
            print("[FAIL] " + p)
        if not problems:
            print("[OK] trace.mjs sha256 = %s" % sha256(TRACE_DST)[:12])
            print("[OK] index.js 与「基线+补丁」逐字相等（%d 行）" % len(actual.splitlines()))
            print("[OK] 语法检查通过")
        print("RESULT=" + ("READY" if not problems else "NOT-READY"))
    return 0 if not problems else 1


def cmd_apply(force: bool) -> int:
    if not INDEX.exists():
        raise SystemExit("[FATAL] 插件不存在：%s" % INDEX)
    if not TRACE_SRC.exists():
        raise SystemExit("[FATAL] 补丁源不存在：%s" % TRACE_SRC)

    current = read_text(INDEX)
    patched_mark = MARK in current

    if BASELINE.exists():
        baseline = read_text(BASELINE)
        if not patched_mark and current != baseline:
            if not force:
                raise SystemExit(
                    "[STALE-BASELINE] index.js 既不含补丁标记、又与基线不同 —— 上游换过版本。\n"
                    "             确认真要用当前文件做新基线再加 --force。"
                )
            shutil.copyfile(INDEX, BASELINE)
            BASELINE_SHA.write_text(sha256(BASELINE) + "\n", encoding="utf-8")
            baseline = read_text(BASELINE)
            print("[baseline] 已用当前文件重建基线")
        if not BASELINE_SHA.exists():
            BASELINE_SHA.write_text(sha256(BASELINE) + "\n", encoding="utf-8")
            print("[baseline] 补写基线 sha256")
    else:
        shutil.copyfile(INDEX, BASELINE)
        BASELINE_SHA.write_text(sha256(BASELINE) + "\n", encoding="utf-8")
        baseline = read_text(BASELINE)
        print("[baseline] 首次建立基线：%s" % BASELINE.name)

    if patched_mark:
        print("[apply] 检测到已有补丁 → 从基线重建（幂等）")

    patched = build_patched(baseline)
    write_text(INDEX, patched)
    print("[apply] index.js 已写入（%d 行）" % len(patched.splitlines()))

    shutil.copyfile(TRACE_SRC, TRACE_DST)
    print("[apply] trace.mjs 已复制（sha256=%s）" % sha256(TRACE_DST)[:12])

    return cmd_check()


def cmd_revert() -> int:
    if not BASELINE.exists():
        raise SystemExit("[FATAL] 没有基线可恢复：%s" % BASELINE)
    shutil.copyfile(BASELINE, INDEX)
    print("[revert] index.js 已恢复上游原版")
    if TRACE_DST.exists():
        TRACE_DST.unlink()
        print("[revert] lib/trace.mjs 已删除")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.revert:
        return cmd_revert()
    if args.apply:
        return cmd_apply(args.force)
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
