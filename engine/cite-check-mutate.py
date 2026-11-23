# -*- coding: utf-8 -*-
"""cite-check 变异台 —— 证明断言台真的在守东西。

做法：把被测脚本**打坏一处**（锚点必须恰好出现 1 次，否则 exit 9 作废，
这是 Manager 轮的教训：变异没发生和「测试抓不到」在输出上一模一样），
再拿断言台跑这份坏副本，看它是否**恰好**红在预期的那几条上。

跑法：python scripts/cite-check-mutate.py
输出：mutants=N caught=M。
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "cite-check.py"
HARNESS = HERE / "cite-check-harness.py"
PY = sys.executable

# (名字, 原文锚点, 替换成, 期望在失败行里出现的关键词)
MUTANTS = [
    ("M1 去掉锚的规范化", 'self.flat = [norm_for(path, x) for x in self.raw]',
     'self.flat = list(self.raw)', ["规范化"]),
    ("M2 行号比对失效", 'elif in_span(hits[0], set(it["points"]), it["ranges"]):', 'elif True:',
     ["DRIFT"]),
    ("M3 唯一性检查失效", 'if len(hits) > 1:\n            return "AMBIG", hits[:5]',
     'if False:\n            return "AMBIG", hits[:5]', ["AMBIG"]),
    ("M4 弱锚阈值失效", 'if len(a) < 8:', 'if len(a) < 0:',
     ["WEAK"]),
    ("M5 相邻引用过滤失效", 'if anchor and REF_LIKE.search(anchor):', 'if False:',
     ["REF_LIKE"]),
    ("M6 --fix 把整个 token 而不是 spec 段写回", 'lines[idx] = line[:s2] + new_spec + line[e2:]',
     'lines[idx] = line[:m.start()] + m.group(1) + ":" + new_spec + line[m.end():]',
     ["--fix"]),
    ("M7 自引排除失效", 'if self_ref:', 'if False:',
     ["SELF"]),
    ("M8 代码运算符被洗掉", 'if path.suffix.lower() in CODE_EXTS:', 'if False:',
     ["代码"]),
    ("M9 HIST 豁免失效", 'if it.get("historical"):', 'if False:',
     ["HIST"]),
    ("M10 --fix 改回字符串替换（会改坏含数字的文件名）",
     'lines[idx] = line[:s2] + new_spec + line[e2:]',
     'lines[idx] = line[:m.start()] + it["token"].replace(m.group(2), new_spec, 1) + line[m.end():]',
     ["文件名"]),
    ("M11 外部根不核对路径后缀", '    if "/" not in name and "\\\\" not in name:\n        return True',
     '    if True:\n        return True', ["外部根"]),
    ("M12 后缀检查退回「仅多候选时」",
     '    if "/" in name or "\\\\" in name:\n        hits = [p for p in hits if target_ok(name, p, root)]',
     '    if len(hits) > 1:\n        hits = [p for p in hits if target_ok(name, p, root)]',
     ["同名+目录"]),
    ("M13 --fix 写坏锚串（改引号）",
     'lines[idx] = line[:s2] + new_spec + line[e2:]',
     'lines[idx] = (line[:s2] + new_spec + line[e2:]).replace("「", "『", 1)',
     ["锚串"]),
    ("M14 NAMED 去掉 `~`", 'NAMED = re.compile(r"`?([A-Za-z0-9_./\\\\~\\-]*',
     'NAMED = re.compile(r"`?([A-Za-z0-9_./\\\\\\-]*', ["家目录"]),
    # ---- M15–M18：救火队长 2026-11-07 补（INC-20261107-01）----
    ("M15 锚定界符退回「任意引号都能闭合」（内含引号被静默截短）",
     'ANCHOR = re.compile(r"^[\\s、，,;；:：·）)】\\]]*(?:「([^」\\n]+)」|`([^`\\n]+)`|\\"([^\\"\\n]+)\\"|“([^”\\n]+)”)")',
     'ANCHOR = re.compile(r"^[\\s、，,;；:：·）)】\\]\\]]*[`「\\"“]([^`」\\"”\\n]+)[`」\\"”]")',
     ["内含引号"]),
    ("M16 目标切行退回 str.splitlines()（\\x0c / \\u2028 多切一行）",
     'self.raw = split_lines(path.read_text(encoding="utf-8", errors="replace"))',
     'self.raw = path.read_text(encoding="utf-8", errors="replace").splitlines()',
     ["换行口径"]),
    ("M17 --fix 读端去掉 newline=\"\"（CRLF 文档被悄悄改成 LF）",
     'with open(p, encoding="utf-8", newline="") as f:',
     'with open(p, encoding="utf-8") as f:',
     ["CRLF"]),
    ("M18 `~/` 分支改成在工程根下找同路径",
     'cand = Path(os.path.expanduser(name))',
     'cand = root / name[2:]',
     ["家目录隔离"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", default=str(TOOL))
    ap.add_argument("--harness", default=str(HARNESS))
    args = ap.parse_args()
    tool = Path(args.tool).resolve()
    harness = Path(args.harness).resolve()
    sys.stdout.reconfigure(encoding="utf-8")

    src = tool.read_text(encoding="utf-8")
    defined = len(MUTANTS)
    void = []
    total = caught = 0
    for name, old, new, expect in MUTANTS:
        n = src.count(old)
        if n != 1:
            # 锚点失效 = 变异根本没发生。**不能算作「未捕获」，也不能悄悄从分母里消失**
            # （第 2 轮验收官 B4：M6 变成 [VOID] 后 `mutants=10` 把它吞掉了）
            void.append(f"{name}（锚点出现 {n} 次，需要恰好 1 次）")
            print(f"[VOID] {name}: 锚点出现 {n} 次（需要恰好 1 次），跳过 —— 不能当作未捕获")
            continue
        total += 1
        tmp = Path(tempfile.mkdtemp(prefix="mutate-"))
        try:
            bad = tmp / "cite-check.py"
            bad.write_text(src.replace(old, new), encoding="utf-8")
            p = subprocess.run([PY, str(harness), "--tool", str(bad)],
                               capture_output=True, text=True, encoding="utf-8")
            out = (p.stdout or "") + (p.stderr or "")
            fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
            hit = any(any(k in ln for k in expect) for ln in fails)
            if p.returncode != 0 and hit:
                caught += 1
                print(f"[CAUGHT] {name}: {len(fails)} 条红，命中预期")
            else:
                print(f"[MISSED] {name}: returncode={p.returncode} fails={len(fails)} "
                      f"expect={expect} out={out[:400]!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"defined={defined} void={len(void)} ran={total} caught={caught}")
    if void:
        print("[error] 以下变异未能执行（锚点失效）—— 分母不干净，结论不成立：")
        for v in void:
            print("  - " + v)
        return 1
    return 0 if total > 0 and caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
