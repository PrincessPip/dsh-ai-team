#!/usr/bin/env python3
"""AGENTS 注入预算守卫 —— 调**真实渲染函数**，不拿正文相加冒充预算。

为什么必须调真实函数（2026-11-12 实测）：
  `dsh-agent-instructions` 的预算计的是**完整渲染后的 system-reminder**
  （包装 + 引导语 + 路径 + 正文），且**先把宽泛的整份文件丢掉，最后才截断最具体的那份**。
  正文相加会在两处撒谎：① 少算 326–377 B 的包装；② 完全看不见「全局那份被整份丢弃」。

判据（两条**分开**报，不许用一个数字盖住另一个）：
  A. 完整渲染：`omitted == []` 且 `truncated == []`（硬，超了必红）
  B. 正文合计 ≤ `--target`（默认 55,000；这是工作目标，不是引擎硬限制）

条件触发（2026-11-12 · DIET-FOLLOWUP K1 · 伙伴拍板「条件进」送审前置）：
  它核的是「注入预算」，只在**改动命中注入面**时才可能红。无条件每次送审都跑 = 一道必然绿的门，
  所以送审前置写成条件式：给 `--changed <本单元改动清单>`，由脚本自己回答「这次该不该核」。
  三个面：`AGENTS.md` / `CLAUDE.md`（正文变了渲染字节就变）· `cordis.patch.yml`（**预算本身**在这里，
  不用动正文也能把预算挤爆）。没命中就 `RESULT=SKIP` 退出 0，**不启动 node**（省 ~0.3 s）。
  ⚠️ 给了 `--changed` 但清单为空 → **exit 2**： 「我改了东西但没告诉它」和「我没碰注入面」是两件事，
  否则漏给一份清单就能绕过这道门。

退出码：0 全过（含 SKIP）/ 1 不通过 / 2 环境或接口异常（**接口漂移必须报失败，不许降级成正文相加**）。

用法：
  python scripts/agents-budget.py
  python scripts/agents-budget.py --changed AGENTS.md scripts/runner.py     # 条件触发
  python scripts/agents-budget.py --changed-from <本单元改动清单.txt>
  python scripts/agents-budget.py --json --out tasks/<单元>/AGENTS-BUDGET.txt
  python scripts/agents-budget.py --global-file a.md --project-file b.md --target 55000
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# 输出统一 UTF-8（Windows 管道默认 cp936：会把中文写坏，也会让 --out 与 stdout 不一致）
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    sys.stderr.reconfigure(encoding="utf-8", newline="")
except Exception:  # noqa: BLE001
    pass

DEFAULT_TARGET = 55000
BOM = b"\xef\xbb\xbf"
BOM_NL = b"\r\n"
PROFILE = os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "web", "cordis.patch.yml")

LIB_CANDIDATES = [
    os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "@deepseek-ai", "dsh",
                 "node_modules", "@deepseek-ai", "dsh-agent-instructions", "lib", "index.js"),
    os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "web", "node_modules",
                 "@deepseek-ai", "dsh-agent-instructions", "lib", "index.js"),
]

RENDER_JS = r"""
import fs from 'node:fs';
const cfg = JSON.parse(process.env.AGENTS_BUDGET_INPUT);
const mod = await import(cfg.libUrl + '?v=' + Date.now());
const fn = mod.renderAgentInstructions;
if (typeof fn !== 'function') {
  console.error('INTERFACE_MISSING: renderAgentInstructions');
  process.exit(2);
}
const files = cfg.paths.map((p, i) => ({
  absolutePath: p,
  displayPath: cfg.displays[i],
  content: fs.readFileSync(p, 'utf8'),
}));
const r = fn(files, { maxBytes: cfg.maxBytes, replacePreviousBaseline: !!cfg.replacement });
console.log(JSON.stringify({
  renderedBytes: Buffer.byteLength(r.text),
  omitted: (r.omitted || []).map((x) => x.displayPath),
  truncated: (r.truncated || []).map((x) => ({
    displayPath: x.displayPath, originalBytes: x.originalBytes, includedBytes: x.includedBytes,
  })),
}));
"""


INJECTION_BASENAMES = ("agents.md", "claude.md")


def is_injection_surface(path: str) -> bool:
    """改动这个文件，**可能**改变注入预算吗？

    三个正面，理由各不相同：
      - `AGENTS.md` / `CLAUDE.md`：**正文**变了，渲染字节数直接变（引擎只认 AGENTS.md，CLAUDE.md 是候选名，
        本脚本的候选链还没扩到它 —— 所以更不能在判定时假装它不存在）；
      - `cordis.patch.yml`：**预算本身**（`maxBytes`）在这里。改它不用动正文也能把预算挤爆，
        而且这种事故在渲染结果里表现为「某个文件被整份 omitted」，人肉很难第一时间联想到是 patch 改的。

    归一化：反斜杠换正斜杠、小写 —— 同一份文件在 Windows 上可能带 `\\`、可能带盘符、可能只有相对路径。

    **fail-closed（2026-11-12 第 1 轮验收官 N2）**：判不出来的形态一律算**命中**。
    理由是代价不对称：多跑一次 node ≈ 0.3 s，漏核一次 = 预算挤爆没人知道。
    这条是 N2 的实质修复 —— `--changed .`（目录）、`--changed tasks/`（带尾斜杠）、
    以及任何**没有扩展名**的写法（如 `Makefile`）都不再静默 SKIP。
    """
    flat = path.replace("\\", "/").lower()
    base = os.path.basename(flat)
    if base in INJECTION_BASENAMES:
        return True
    if "cordis.patch.yml" in flat:
        return True
    # 判不出来 → 判命中。拆成三行是**故意**的：三种形态各要能被单独打坏（变异台 M12/M13/M14）。
    # ① 目录写法：`.` / `..` / 空 / 尾斜杠
    if base in ("", ".", "..") or flat.endswith("/"):
        return True
    # ② 无扩展名（`Makefile` 之类）
    if "." not in base:
        return True
    # ③ **真实存在的目录**（第 2 轮验收官 N-b）：`<USERPROFILE>\.dsh` 这种「目录名里带点」的写法
    #    会从 ①② 漏过去，而全局 `AGENTS.md` 恰好就在 `~/.dsh` 下 —— 那正是这道门最该守的地方。
    if os.path.isdir(os.path.expanduser(path)):
        return True
    return False


def find_lib() -> str | None:
    env = os.environ.get("DSH_AGENT_INSTRUCTIONS_LIB")
    if env and os.path.isfile(env):
        return env
    for cand in LIB_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def read_profile_budget(path: str = PROFILE) -> tuple[int | None, str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return None, f"{path} ({exc.__class__.__name__})"
    # 先按 agent-instructions 插件块定位（第 2 轮验收官 N6：全文正则会撞上别的插件同名键）
    m = re.search(r"agent-instructions[\s\S]{0,2000}?maxBytes:\s*(\d+)", text)
    if m:
        return int(m.group(1)), f"{path} (agent-instructions block)"
    hits = re.findall(r"maxBytes:\s*(\d+)", text)
    if not hits:
        return None, f"{path} (no maxBytes)"
    vals = sorted(set(hits))
    if len(vals) > 1:
        # 不再「碰巧取最后一个」：多处不同值时判失败，逼人显式给 --max-bytes
        return None, f"{path} (multiple different maxBytes: {vals})"
    return int(hits[0]), path


def render(lib, paths, displays, max_bytes, replacement=False):
    env = dict(os.environ)
    env["AGENTS_BUDGET_INPUT"] = json.dumps({
        "libUrl": "file:///" + lib.replace("\\", "/"),
        "paths": paths,
        "displays": displays,
        "maxBytes": max_bytes,
        "replacement": replacement,
    }, ensure_ascii=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found in PATH")
    proc = subprocess.run(
        [node, "--input-type=module", "-e", RENDER_JS],
        env=env, capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError("render failed rc=%d: %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()[:400]))
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--global-file", default=os.path.join(os.path.expanduser("~"), ".dsh", "AGENTS.md"))
    ap.add_argument("--project-file", default=None, help="默认 <root>/AGENTS.md")
    ap.add_argument("--max-bytes", type=int, default=None, help="默认从 profile 读")
    ap.add_argument("--profile", default=PROFILE, help="从哪个 profile 读 maxBytes")
    ap.add_argument("--lib", default=None, help="dsh-agent-instructions/lib/index.js 路径（默认自动探测）")
    ap.add_argument("--target", type=int, default=DEFAULT_TARGET)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="把整段输出原样写进文件（由本脚本写，别用 PowerShell >）")
    ap.add_argument("--changed", nargs="*", default=None,
                    help="本单元改动的文件清单（相对工程根或绝对路径都行，**不要求存在**）；"
                         "给了它就只在命中注入面时才真核，否则 RESULT=SKIP 退出 0")
    ap.add_argument("--changed-from", default=None,
                    help="同 --changed，从文件读：每行一个路径，空行与 # 开头的行忽略")
    args = ap.parse_args()

    project = args.project_file or os.path.join(args.root, "AGENTS.md")
    budget, budget_src = (args.max_bytes, "--max-bytes") if args.max_bytes else read_profile_budget(args.profile)
    lines: list[str] = []

    def emit(s=""):
        lines.append(s)

    def finish(code: int, text_override: str | None = None) -> int:
        text = text_override if text_override is not None else ("\n".join(lines) + "\n")
        sys.stdout.write(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        return code

    # --- 条件触发：先回答「这次该不该核」，再决定要不要启动 node ---
    changed: list[str] | None = None
    if args.changed is not None or args.changed_from is not None:
        changed = list(args.changed or [])
        if args.changed_from:
            try:
                # utf-8-sig：PowerShell 5.1 的 `-Encoding UTF8` 会带 BOM，留着它会让首行 `#` 注释判定失效
                with open(args.changed_from, "r", encoding="utf-8-sig") as f:
                    raw = f.read().splitlines()
            except OSError as exc:
                emit(f"[FAIL] 读不到 --changed-from：{args.changed_from} ({exc.__class__.__name__})")
                return finish(2)
            changed += [ln.strip() for ln in raw if ln.strip() and not ln.strip().startswith("#")]
        if not changed:
            emit("[gate] mode=conditional changed=0")
            emit("[FAIL] 改动清单为空 ——「我改了东西但没告诉它」不等于「我没碰注入面」，不判 SKIP")
            return finish(2)
        hits = [p for p in changed if is_injection_surface(p)]
        emit(f"[gate] mode=conditional changed={len(changed)} hit={hits}")
        if not hits:
            emit("[gate] SKIP —— 改动清单没落在注入面（AGENTS.md / CLAUDE.md / cordis.patch.yml）")
            emit(f"RESULT=SKIP changed={len(changed)}")
            return finish(0)
        emit("[gate] REQUIRED —— 命中注入面，真核预算")
    else:
        emit("[gate] mode=always（未给 --changed，无条件核）")

    lib = args.lib or find_lib()
    missing = [p for p in (args.global_file, project) if not os.path.isfile(p)]

    emit(f"[budget] maxBytes={budget} source={budget_src}")
    emit(f"[lib] {lib or 'NOT FOUND'}")
    emit(f"[target] 正文合计 <= {args.target}")
    for p in (args.global_file, project):
        if os.path.isfile(p):
            b = open(p, "rb").read()
            emit(f"[file] {p} bytes={len(b)} crlf={b.count(BOM_NL)} bom={b.startswith(BOM)}")
        else:
            emit(f"[file] {p} MISSING")
    if budget is None:
        emit("[FAIL] 读不到 maxBytes，且没给 --max-bytes（不猜、不降级）")
        return finish(2)
    if lib is None or not os.path.isfile(lib):
        emit(f"[FAIL] 找不到渲染函数入口：{lib!r}（--lib 或 DSH_AGENT_INSTRUCTIONS_LIB）")
        return finish(2)
    if missing:
        emit(f"[FAIL] 必需文件缺失：{missing}（缺文件不许当 0 B 全过）")
        return finish(2)

    try:
        r = render(lib, [os.path.abspath(args.global_file), os.path.abspath(project)],
                   ["user-global/AGENTS.md", "AGENTS.md"], budget)
    except Exception as exc:  # noqa: BLE001
        emit(f"[FAIL] 渲染失败：{exc}")
        return finish(2)

    body = sum(os.path.getsize(p) for p in (args.global_file, project))

    emit(f"[render] renderedBytes={r['renderedBytes']} omitted={r['omitted']} truncated={r['truncated']}")
    emit(f"[body] total={body} overhead={r['renderedBytes'] - body:+d} (omitted 会改写这个数，只作参考)")

    ok_render = not r["omitted"] and not r["truncated"]
    ok_target = body <= args.target
    emit(f"[check-A] 完整渲染无省略/截断: {'PASS' if ok_render else 'FAIL'}")
    emit(f"[check-B] 正文合计 {body} <= {args.target}: {'PASS' if ok_target else 'FAIL'}")
    if not ok_render:
        for name in r["omitted"]:
            emit(f"  [omitted] {name} —— 被整份丢弃（宽泛文件先丢）")
        for t in r["truncated"]:
            emit(f"  [truncated] {t['displayPath']} {t['originalBytes']} -> {t['includedBytes']}")
    emit(f"RESULT={'OK' if (ok_render and ok_target) else 'FAIL'} body={body} rendered={r['renderedBytes']} budget={budget} target={args.target}")

    return finish(0 if (ok_render and ok_target) else 1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] unexpected: {exc.__class__.__name__}: {exc}")
        sys.exit(2)
