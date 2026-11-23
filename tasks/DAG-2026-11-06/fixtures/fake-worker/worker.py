#!/usr/bin/env python3
"""worker.py —— 假的 dsh，只给 `runner.py --engine fixture` 用

它不是员工，是**测试替身**：读 runner 从 stdin 塞进来的 prompt，按 `--spec` 的
`nodes.<节点id>` 一节行事，然后输出与 headless `--json` 同形的 NDJSON
（`tool_call` / `tool_result` / `final`）。这样 runner 的
「子进程 → 事件解析 → 快照 → 自报对照」整条链路都是**真的**，只有「谁来扮演 dsh」被替换。

它刻意做三件事，让 harness 能咬住真实行为：
  1. **真读 stdin** 并校验 prompt 含 `expect_prompt_contains` 的字样 —— 否则 exit 9。
     这样「runner 到底有没有把任务传下去」是可证的，不是靠看代码。
  2. **真写文件**（相对工程根），这样 runner 的快照 diff 抓得到。
  3. **尊重 `DSH_PERMISSION_MODE`**：设为 `read-only` 时拒绝写并输出一条沙箱拒绝的 tool_result。
     这是模拟，**不是**真沙箱 —— 真沙箱的硬拦由 `engine=headless` 的真跑证明（TASK C1）。
  4. **能精确制造 F13/F12/F14 那几种行为**（EVIDENCE-GUARD 2026-11-10 加）：
     `touch_files`（只改 mtime、内容不动）· `tamper_files`（改证据内容但把 mtime 复原）·
     `{RUN}` 占位（指到本次证据包 `runs/<run_id>/`，由 `DSH_RUN_ID` 提供）。
     这些**只给测试用**，是为了让判据有正反两面证据，不代表真实员工会这么干。

失效边界：它产出的证据包带 `FAKE` 标记，**不能当交付证据**。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# worker.py 在 tasks/DAG-2026-11-06/fixtures/fake-worker/ 下，往上 4 层是工程根
ROOT = Path(__file__).resolve().parents[4]

# runner 用 UTF-8 写 stdin、dsh 的 Node 端也按 UTF-8 读 —— 这里必须对齐，否则中文 prompt 会乱
for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def expand_run(rel):
    """把 spec 里的 `{RUN}` 换成 `runs/<run_id>`（run_id 由 runner 通过 `DSH_RUN_ID` 传下来）。

    为什么要有它：C3/C4 测的是「员工去碰证据包」，而证据包路径里有 run_id，
    spec 是静态文件、不可能预先写死。没有这个占位就只能靠硬编码场景名 —— 那会让
    变异台换 run-id 前缀跑的时候静默指错地方。
    """
    rid = (os.environ.get("DSH_RUN_ID") or "").strip()
    if "{HOME}" in rel:
        # C6 的逃逸面：`$USERPROFILE` 顶层（真跑里 integ a1 写过 `_dsh_probe.ps1`）。
        # 写成绝对路径，`ROOT / abs` 在 pathlib 里就是 abs 本身。
        rel = rel.replace("{HOME}", os.environ.get("USERPROFILE") or "")
    if "{RUN}" not in rel or not rid:
        return rel
    return rel.replace("{RUN}", "runs/" + rid.replace("\\", "/").strip("/"))


def spec_for(node_spec, spec_path, node_id):
    """按「被调用了几次」选这一轮的设定。返回 `(cfg, n, attempts 总数)`。

    **为什么由 worker 自己数、而不是 runner 告诉它第几次**：如果 runner 传一个
    `attempt` 环境变量下来，「第 2 次才成功」这件事就变成了 runner 的自证 ——
    runner 不重跑、只传 attempt=2 也照样绿。自己数计数文件，才能证明
    **真的有第二次子进程起来**。

    计数文件放哪：优先 `DSH_FAKE_COUNTER_DIR`（runner 传 `runs/<id>/FAKE-COUNTERS`，
    在快照排除区里），否则退回 spec 目录。**不能落在工程根的扫描范围内** ——
    runner 的快照会把计数文件算成节点的写入，只读节点直接判 `readonly_violation`
    （2026-11-07 第一次实现就此红了 9 条）。
    """
    attempts = node_spec.get("attempts")
    base = os.environ.get("DSH_FAKE_COUNTER_DIR") or str(Path(spec_path).parent)
    cdir = Path(base)
    cdir.mkdir(parents=True, exist_ok=True)
    counter = cdir / (".calls-" + node_id)
    n = 0
    if counter.is_file():
        try:
            n = int(counter.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            n = 0
    n += 1
    counter.write_text(str(n), encoding="utf-8")
    if isinstance(attempts, list) and attempts:
        idx = min(n - 1, len(attempts) - 1)
        merged = dict(node_spec)
        merged.pop("attempts", None)
        cfg = attempts[idx]
        merged.update(cfg or {})
        return merged, n, len(attempts)
    return node_spec, n, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--node", required=True)
    # runner 在升档场景会带 `--patch <overlay.yml>`（真 headless 就是这么换模型的）。
    # 这里只接受、不使用：让 **argv 形状与真跑一致**，于是「升档到底有没有把 --patch 带上」
    # 在零成本场景里也咬得住。
    ap.add_argument("--patch", default=None)
    a = ap.parse_args()

    prompt = sys.stdin.read()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    raw_node_spec = (spec.get("nodes") or {}).get(a.node)
    if raw_node_spec is None:
        emit({"type": "final", "text": "FAKE_WORKER_NO_SPEC_FOR_NODE: " + a.node})
        return 8
    node_spec, call_n, n_attempts = spec_for(raw_node_spec, a.spec, a.node)

    # N1（2026-11-10 第 2 轮验收官 N1）：TEMP 必须是**已存在**的目录 —— 真 dsh 每次启动都查这一点
    # （`requireDirectory`），替身不能只在「写临时文件」时才查：否则 runner 忘了 mkdir 时，
    # 只读节点和重试节点照样「跑成功」，只有那个恰好写临时文件的节点会红。
    temp_env = os.environ.get("TEMP") or ""
    if temp_env and not Path(temp_env).is_dir():
        emit({"type": "final", "text": "FAKE_WORKER_TEMP_MISSING: " + temp_env})
        return 127

    # 1) prompt 真的到了吗
    missing = [s for s in (node_spec.get("expect_prompt_contains") or []) if s not in prompt]
    if missing:
        emit({"type": "final", "text": "FAKE_WORKER_PROMPT_MISSING: %r" % missing})
        return 9

    # 2) 假工具轨迹
    for tc in (node_spec.get("tool_calls") or []):
        emit({"type": "tool_call", "name": tc.get("name"), "args": tc.get("args") or {}})

    # 3) 按沙箱模式决定写不写得动
    readonly = os.environ.get("DSH_PERMISSION_MODE") == "read-only"
    if node_spec.get("ignore_readonly"):
        # ⚠️ **只给「波级归因」测试用**：模拟沙箱被绕过（真沙箱由 engine=headless 的真跑证明）。
        # 它存在的唯一目的，是让「并行窗口里出现变化 → 组内所有参与者连坐」这条判据有正反证据。
        readonly = False
    for rel, content in (node_spec.get("write_files") or {}).items():
        rel = expand_run(rel)
        if readonly:
            emit({"type": "tool_result", "name": "Write", "ok": False,
                  "text": "[sandbox] denied: DSH_PERMISSION_MODE=read-only（%s）" % rel})
            continue
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        emit({"type": "tool_result", "name": "Write", "ok": True, "text": "wrote " + rel})

    # 3.05) F14 的精确复现：**只改 mtime、内容一字节不动**。
    #   旧判据（`sha 不同 or mtime 不同`）会把它算成 modified；新判据只认 sha，于是落进 touched。
    for rel in (node_spec.get("touch_files") or []):
        rel = expand_run(rel)
        p = ROOT / rel
        if not p.is_file():
            emit({"type": "tool_result", "name": "Write", "ok": False, "text": "touch skipped: " + rel})
            continue
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        emit({"type": "tool_result", "name": "Write", "ok": True, "text": "touched (mtime only) " + rel})

    # 3.06) F13 的精确复现：改**已登记证据**的内容、再把 mtime 复原（size 也保持相同）。
    #   窗口内的轻量指纹看不见它 —— 只有证据清单的 sha256 复核（C4）抓得住。
    for t in (node_spec.get("tamper_files") or []):
        rel = expand_run(t.get("path") or "")
        p = ROOT / rel
        if not p.is_file():
            emit({"type": "tool_result", "name": "Write", "ok": False, "text": "tamper skipped: " + rel})
            continue
        st = p.stat()
        if t.get("content") is not None:
            new = str(t["content"])
        else:
            # 等长翻转：改一个 ASCII 字符，size 与 mtime 都还原得回去 —— 这样窗口内的
            # 轻量指纹（mtime/size）绝对看不见，只有 sha256 清单抓得到（C3 vs C4 的分界线）。
            text = p.read_text(encoding="utf-8")
            i = len(text.rstrip()) - 1
            if i < 0:
                emit({"type": "tool_result", "name": "Write", "ok": False, "text": "tamper skipped: 空文件 " + rel})
                continue
            ch = text[i]
            new = text[:i] + ("X" if ch != "X" else "Y") + text[i + 1:]
        p.write_text(new, encoding="utf-8")
        if p.stat().st_size != st.st_size:
            emit({"type": "tool_result", "name": "Write", "ok": False,
                  "text": "tamper size 变了（会同时触发 evidence_write）：" + rel})
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        emit({"type": "tool_result", "name": "Write", "ok": True, "text": "tampered (mtime restored) " + rel})

    # 3.07) F12 的**收容**验证（C5 / N1）：写 `$env:TEMP` 里的东西落到哪。
    #   runner 把子进程的 TMP/TEMP/TMPDIR 指到 **workspace 外**的受管目录，所以这里应该落在那里，
    #   而不是真实 `%TEMP%`（以前 `%TEMP%\mkpatch-wdocs.py` 就是这么留下的）。
    #   ⚠️ **不自己建目录**：真 dsh 的 Windows ACL runner 要求 `--temp` 是**已存在**的目录
    #   （不存在 → 子进程 exit 127 / `realpathSync` ENOENT）。测试替身原先自己 `mkdir`，
    #   于是 runner 忘了建目录只有真跑才炸 —— 2026-11-10 第 1 轮验收官 B1 就是靠这个「太宽容的替身」
    #   抓出来的。现在替身同款严格。
    for name, content in (node_spec.get("write_temp_files") or {}).items():
        tp = Path(os.environ.get("TEMP") or ".")
        if not tp.is_dir():
            emit({"type": "final", "text": "FAKE_WORKER_TEMP_MISSING: " + str(tp)})
            return 127
        p = tp / name
        p.write_text(content, encoding="utf-8")
        emit({"type": "tool_result", "name": "Write", "ok": True, "text": "wrote to $TEMP: " + str(p)})

    # 3.5) 人为延迟：让「并发区间重叠」这件事在 fixture 里可测（TASK-step2b K2）。
    #     没有它的话，两个节点各 20ms 跑完，重叠不重叠都可能被判错 —— 测的是调度抖动，不是调度。
    delay = float(node_spec.get("sleep_s") or 0)
    if delay > 0:
        time.sleep(delay)

    # 4) 最终回答（<RESULT> 块）
    res = node_spec.get("result")
    if res is None:
        final_text = node_spec.get("final_text") or "（没有 result）"
    else:
        final_text = "fixture 员工完成。\n<RESULT>\n" + json.dumps(res, ensure_ascii=False) + "\n</RESULT>"
    emit({"type": "final", "text": final_text})

    return int(node_spec.get("exit_code") or 0)


if __name__ == "__main__":
    sys.exit(main())
