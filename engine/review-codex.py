#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""送审（codex 版）：把本单元交给独立验收官（GPT，走 ChatGPT 订阅额度）。

2026-11-12（`CLAUDE-TO-GPT-2026-11-12`，伙伴拍板 D1=A）：
Claude 订阅被 portal KYC 挡住且决定不做 KYC → Claude 通道整体不可用、不会恢复。
验收官从 `claude -p --allowedTools Read,Grep,Glob,Bash` 换到 `codex exec`。

════════════════════════════════════════════════════════════════════════
⚠️ 「验收官只读」的语义**已降级**，用之前必须知道：
   Windows 上 codex 的 `-s read-only` 实测是「禁止执行任何命令」，连读文件都被
   blocked（`PROBE-READONLY.txt` 三组对照，2026-11-10）。验收官要读文件、跑只读命令，
   就只能给 `--approve-for-me`（workspace-write + auto_review）。
   所以：**只读现在是「行为约定 + 事后快照核」，不是进程级硬锁。**
   裁决报告里必须如实写 `readonly_enforcement: snapshot_only`。
   依据：`tasks/CLAUDE-TO-GPT-2026-11-12/TASK.md` §三·D1。
════════════════════════════════════════════════════════════════════════

用法：

    python scripts/review-codex.py --brief tasks/<单元>/REVIEW-BRIEF.md \
        [--round 1] [--model gpt-6-astra] [--out-dir tasks/<单元>] [--dry-run]

输出：
    <out-dir>/REVIEW<round后缀>-raw.txt     验收官原话（UTF-8，由脚本直接落盘）
    <out-dir>/REVIEW<round后缀>-stdout.txt  rc / 耗时 / stderr / 事件统计

为什么写脚本而不是手敲一行：
  * 中文报告必须**由命令直接落盘**（PowerShell 的 `>` 会按 CP936 转码，报告会坏）；
  * egress 自检必须先 READY，否则白跑一轮额度（`TRIGGER-POLICY.md` §三 送审前置）；
  * prompt 走 stdin（含空格/中文的 prompt 用 argv 传会被拆）。

抽 JSON 裁决那一步仍然用 `scripts/review-backfill.ps1`。
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import tempfile
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Avoid import bytecode writes before the snapshot.
_spec = importlib.util.spec_from_file_location("review_snapshot", HERE / "review-snapshot.py")
snapshot_tools = importlib.util.module_from_spec(_spec)
exec(compile((HERE / "review-snapshot.py").read_bytes(), str(HERE / "review-snapshot.py"), "exec"), snapshot_tools.__dict__)

# codex 侧前置自检（它消费 claude-egress-check.ps1 的 -Json 取端口，端口真值只有一处）
EGRESS = Path.home() / ".dsh" / "scripts" / "cdp" / "codex-egress-check.ps1"
DEFAULT_MODEL = "gpt-6.2-astra"
DEFAULT_EFFORT = "high"


def find_node() -> str:
    exe = shutil.which("node.exe") or shutil.which("node")
    if not exe:
        raise SystemExit("找不到 node（codex.js 需要它）")
    return exe


def find_codex() -> Path:
    env = os.environ.get("CODEX_JS")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise SystemExit("CODEX_JS 指向的文件不存在：" + env)
    p = (Path.home() / ".dsh" / "profiles" / "web" / "node_modules"
         / "@openai" / "codex" / "bin" / "codex.js")
    if p.is_file():
        return p
    raise SystemExit("找不到 codex.js：%s（或用 CODEX_JS 指定）" % p)


def check_egress() -> None:
    """不是 READY 就不送审。**硬拦，没有跳过开关**（判法同 manager.py）。"""
    if not EGRESS.is_file():
        raise SystemExit("codex-egress-check.ps1 不存在：%s" % EGRESS)
    r = subprocess.run(["powershell", "-NoProfile", "-File", str(EGRESS)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out.rstrip())
    if r.returncode != 0:
        raise SystemExit("egress 没过（rc=%s）—— 不送审。" % r.returncode)
    if not any("[result]" in ln and "READY" in ln for ln in out.splitlines()):
        raise SystemExit("egress 退出码 0 但没有 [result] READY 行 —— 不算就绪，不送审。")


def build_prompt(brief_rel: str, contract_rel: str) -> str:
    return (
        "你是本次交付的独立验收官。请读 %s 并按它执行；判据用 %s 全文。\n\n"
        "硬约束（本机/本工程事实，不写进 prompt 你就要到处撞）：\n"
        "1. **不要修改本工程的任何文件**。需要跑台子或造镜像，一律在 %%TEMP%% 下复制一份来做。\n"
        "2. 你可以读文件、跑只读命令。**注意**：本机上「只读」不是进程级沙箱强制的"
        "（Windows 上 codex 的 read-only 会连读文件都禁），所以你的只读**靠你自己守**，"
        "出了写入就是事故。\n"
        "3. 裁决格式：最后输出一段 JSON，含 `verdict`（pass / rework / escalate）、"
        "`confidence`（0–1）、`blocking`（数组，每条含 id / claim / evidence）、"
        "`nonblocking`（数组）、`readonly_enforcement`（必须写 \"snapshot_only\"）。\n"
        "4. 没有证据不算通过：任何「已修复 / 已验证」都要给命令输出或 file:line。\n"
    ) % (brief_rel, contract_rel)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True, help="REVIEW-BRIEF.md 的路径")
    ap.add_argument("--contract", default=str(ROOT / "REVIEW-CONTRACT.md"))
    ap.add_argument("--round", default="1")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default=DEFAULT_EFFORT,
                    choices=("low", "medium", "high", "xhigh", "max", "ultra"),
                    help="请求的推理强度（默认 high，非服务端确认值）")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--out-dir", default=None, help="默认取 brief 所在目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印 argv 与 prompt，不送审")
    args = ap.parse_args()

    if not re.fullmatch(r"[1-9][0-9]{0,8}", args.round):
        ap.error("round must be a positive decimal integer (max 9 digits)")
    if args.timeout <= 0:
        ap.error("timeout must be positive")
    # ④b · B1（2026-11-20 round 1 blocking）：`resolve()` 会把链接抹平成真目录，
    # 于是「指向工程内真目录的 junction」会被当成正常 out-dir 接受并写出报告（实测 exit=0）。
    # 所以入口必须在 resolve **之前**对原始绝对路径及祖先查链接；resolve 之后的检查保留作复查。
    try:
        for raw in (args.brief, args.out_dir):
            if raw:
                snapshot_tools.no_links(Path(os.path.abspath(raw)))
    except OSError as e:
        ap.error(str(e))

    brief = Path(args.brief).resolve()
    if not brief.is_file():
        raise SystemExit("brief 不存在：%s" % brief)
    contract = Path(args.contract).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else brief.parent

    try:
        snapshot_tools.no_links(out_dir)
        out_dir.relative_to(ROOT)
        if not out_dir.is_dir():
            raise ValueError("out-dir must be an existing project directory")
        suffix = "" if args.round == "1" else "-round%s" % args.round
        for ending in ("raw.txt", "stdout.txt", "events.jsonl", "snapshot.json"):
            target = out_dir / ("REVIEW%s-%s" % (suffix, ending))
            snapshot_tools.no_links(target)
            if target.exists() and not target.is_file():
                raise ValueError("report target is not a regular file")
    except (OSError, ValueError) as e:
        ap.error(str(e))

    try:
        brief_rel = brief.relative_to(ROOT).as_posix()
    except ValueError:
        brief_rel = brief.as_posix()
    try:
        contract_rel = contract.relative_to(ROOT).as_posix()
    except ValueError:
        contract_rel = contract.as_posix()

    prompt = build_prompt(brief_rel, contract_rel)
    codex_js = find_codex()
    argv = [
        find_node(), str(codex_js), "exec",
        "--json",
        "--skip-git-repo-check",
        "-m", args.model,
        "-c", "model_reasoning_effort=%s" % args.effort,
        "--approve-for-me",   # D1=A：只读降级为 workspace-write + 事后核
        "-C", str(ROOT),
        "-",
    ]

    if args.dry_run:
        print("[review] dry-run：不送审")
        print("[review] argv  = " + " ".join(argv))
        print("[review] brief = " + brief_rel)
        print("--- prompt ---")
        print(prompt)
        return 0

    # Lock/evidence staging are external; no project writes until after scan.
    lock = snapshot_tools.RootLock(ROOT)
    try:
        lock.acquire()
    except OSError as e:
        print("[review] lock rejected (investigate stale locks manually):", e)
        return 6
    try:
        check_egress()
        started = time.monotonic()
        result = snapshot_tools.verify(ROOT, argv, prompt, args.timeout)
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        result['requested_model'] = args.model
        result['requested_reasoning_effort'] = args.effort
        model = result['model']
        texts = []
        for line in model['stdout'].splitlines():
            try:
                ev = json.loads(line)
                item = ev.get('item') or {}
                if ev.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                    texts.append(item.get('text') or '')
            except (ValueError, AttributeError):
                pass
        # Preserve authoritative evidence outside root first, even if model damages out-dir.
        evidence = Path(tempfile.mkdtemp(prefix="review-codex-evidence-"))
        if evidence.is_relative_to(ROOT):
            raise OSError("evidence must be outside root")
        result['external_evidence'] = str(evidence)
        files = {
            "raw.txt": "\n\n".join(texts) + "\n",
            "events.jsonl": model['stdout'],
            "stdout.txt": "rc=%s elapsed=%ss model=%s readonly_enforcement=snapshot_only\nrequested_reasoning_effort=%s\n" % (
                model['returncode'], result['elapsed_seconds'], args.model, args.effort) + json.dumps(
                    {k:v for k,v in model.items() if k != 'stdout'}, ensure_ascii=False, indent=2),
            "snapshot.json": json.dumps(result, ensure_ascii=False, indent=2),
        }
        for ending, content in files.items():
            (evidence / ("REVIEW%s-%s" % (suffix, ending))).write_text(content, encoding='utf-8')
        try:
            snapshot_tools.no_links(out_dir)
            for ending, content in files.items():
                target = out_dir / ("REVIEW%s-%s" % (suffix, ending))
                snapshot_tools.no_links(target)
                # Never overwrite model-written artifacts or existing reports.
                with target.open('x', encoding='utf-8') as f:
                    f.write(content)
        except (OSError, ValueError) as e:
            result['publication_error'] = repr(e)
            result['exit_code'] = result['exit_code'] or 7
            (evidence / ("REVIEW%s-snapshot.json" % suffix)).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print("[review] local publication failed; external evidence=%s: %s" % (evidence, e))
            return result['exit_code']
        print("[review] exit=%s evidence=%s" % (result['exit_code'], evidence))
        return result['exit_code']
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
