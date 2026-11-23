# -*- coding: utf-8 -*-
"""cite-check 断言台 —— 正反两面都跑，不只看「该绿的绿」。

用临时目录造夹具（不碰工程文件），每个 case 独立。
跑法：python scripts/cite-check-harness.py
输出：checks=N failed=M（失败会逐条打印原因）
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "cite-check.py"
PY = sys.executable

RESULTS = {"checks": 0, "failed": 0}


def check(name, cond, detail=""):
    RESULTS["checks"] += 1
    if not cond:
        RESULTS["failed"] += 1
        print(f"[FAIL] {name}  {detail}")
    return cond


def run(root, files, extra=()):
    cmd = [PY, str(TOOL), "--root", str(root), "--files", *files, *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


def fixture(tmp, target_text, doc_text):
    (tmp / "target.md").write_text(target_text, encoding="utf-8")
    (tmp / "doc.md").write_text(doc_text, encoding="utf-8")


TARGET = """# 目标文件

第一行正文

## 二·五 救火档（v2.0 · 2026-11-07 伙伴拍板）

这一节讲救火队的触发条件。
"""

ANCHOR = "## 二·五 救火档（v2.0 · 2026-11-07 伙伴拍板）"


def case(name, target_text, doc_text, files=("doc.md",), extra=(), want_verdict=None,
         want_exit=None, want_stdout=(), forbid_stdout=()):
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    try:
        fixture(tmp, target_text, doc_text)
        code, out, err = run(tmp, list(files), extra)
        ok = True
        if want_verdict:
            ok &= check(f"{name}: 判定={want_verdict}", f"[{want_verdict}]" in out,
                        f"stdout={out[:300]!r} err={err[:200]!r}")
        if want_exit is not None:
            ok &= check(f"{name}: exit={want_exit}", code == want_exit, f"got {code}")
        for s in want_stdout:
            ok &= check(f"{name}: 含 {s!r}", s in out, f"stdout={out[:300]!r}")
        for s in forbid_stdout:
            ok &= check(f"{name}: 不含 {s!r}", s not in out, f"stdout={out[:300]!r}")
        return ok, tmp, out
    finally:
        pass


def cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    global TOOL
    ap = argparse.ArgumentParser(description="cite-check 断言台")
    ap.add_argument("--tool", default=str(TOOL), help="被测脚本路径（变异台用它指向被打坏的副本）")
    args = ap.parse_args()
    TOOL = Path(args.tool).resolve()
    sys.stdout.reconfigure(encoding="utf-8")

    # 1 OK：锚逐字一致，行号对
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("OK: 判定 OK", "[OK]" in out, out[:300])
    check("OK: exit 0", code == 0, f"got {code} {err[:200]}")
    check("OK: 覆盖率 100%", "anchored=1 (100.0%" in out, out[:300])
    cleanup(tmp)

    # 2 DRIFT：插入两行，行号漂了但锚还在
    target2 = "# 目标文件\n\n新增甲\n新增乙\n\n第一行正文\n\n" + ANCHOR + "\n\n这一节讲救火队的触发条件。\n"
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, target2, f"引用 `target.md:3`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("DRIFT: 判定 DRIFT", "[DRIFT]" in out, out[:300])
    check("DRIFT: exit 2（可自动修）", code == 2, f"got {code}")
    check("DRIFT: 报出实际行号 8", "now_at=[8]" in out, out[:300])
    cleanup(tmp)

    # 3 GONE：锚被改写（改的是**锚里的字**，不是正文别处）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET.replace(ANCHOR, ANCHOR.replace("救火档", "灭火档")),
            f"引用 `target.md:5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("GONE: 判定 GONE", "[GONE]" in out, out[:300])
    check("GONE: exit 1（必挂）", code == 1, f"got {code}")
    cleanup(tmp)

    # 4 AMBIG：锚出现两次
    target4 = TARGET + "\n" + ANCHOR + "\n"
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, target4, f"引用 `target.md:5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("AMBIG: 判定 AMBIG", "[AMBIG]" in out, out[:300])
    check("AMBIG: exit 1", code == 1, f"got {code}")
    cleanup(tmp)

    # 5 WEAK：锚太短
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, "引用 `target.md:5`「正文」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("WEAK: 判定 WEAK", "[WEAK]" in out, out[:300])
    check("WEAK: exit 1", code == 1, f"got {code}")
    cleanup(tmp)

    # 6 NOANCHOR：旧语法
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, "引用 `target.md:5` 那一节\n")
    code, out, err = run(tmp, ["doc.md"])
    check("NOANCHOR: 判定 NOANCHOR", "[NOANCHOR]" in out, out[:300])
    check("NOANCHOR: 默认不挂（历史文档）", code == 0, f"got {code}")
    code2, out2, _ = run(tmp, ["doc.md"], extra=("--strict",))
    check("NOANCHOR: --strict 下挂", code2 == 1, f"got {code2}")
    cleanup(tmp)

    # 7 LONE：孤立引用不判定
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, "引用 `target.md:5`「" + ANCHOR + "」，另见 `:9` 与 `:12`\n")
    code, out, err = run(tmp, ["doc.md"])
    check("LONE: 统计到 2 个孤立引用", "lone=2" in out, out[:300])
    cleanup(tmp)

    # 8 UNRESOLVED：目标文件不存在
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `nope.md:5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("UNRESOLVED: 判定 UNRESOLVED", "[UNRESOLVED]" in out, out[:300])
    check("UNRESOLVED: exit 1", code == 1, f"got {code}")
    cleanup(tmp)

    # 9 规范化：锚含 markdown 标记差异（真实踩过的坑：**它不算第四档** vs **它不算第四档。**）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:5`「{ANCHOR.replace('（', ' (')}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("规范化: 全角/半角括号差异仍判 OK", "[OK]" in out, out[:300])
    cleanup(tmp)

    # 10 REF_LIKE：相邻的另一个引用不能被当成锚
    # 注意：这条夹具里有**两个**引用形态（:5 和引号里的 :9），只查「含 NOANCHOR」会被第二个掩护，
    # 所以同时要求 GONE 不存在、且 NOANCHOR 计数为 2。
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, "引用 `target.md:5`「target.md:9」与别处\n")
    code, out, err = run(tmp, ["doc.md"])
    check("REF_LIKE: 相邻引用不算锚", "[NOANCHOR]" in out and "[GONE]" not in out, out[:300])
    check("REF_LIKE: 两条都是 NOANCHOR（不是一条 NOANCHOR + 一条其他）",
          "[NOANCHOR]   2" in out, out[:300])
    cleanup(tmp)

    # 11 逗号列表：命中其中一个即 OK
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:1,5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("逗号列表: 命中 5 判 OK", "[OK]" in out, out[:300])
    cleanup(tmp)

    # 12 --fix：行号被改对，且**除了那几位数字，整行逐字节不变**（M6 变异：连锚串一起写）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    doc = f"引用 `target.md:3`「{ANCHOR}」\n"
    fixture(tmp, target2, doc)
    code, out, err = run(tmp, ["doc.md"], extra=("--fix",))
    fixed = (tmp / "doc.md").read_text(encoding="utf-8")
    check("--fix: 报出改了几处", "rewrote 1 line-number" in out, out[:300])
    check("--fix: 行号 3 -> 8", "target.md:8" in fixed, fixed[:200])
    check("--fix: 锚串一字未改", f"「{ANCHOR}」" in fixed, fixed[:200])
    check("--fix: 整行只差那一位数字", fixed == doc.replace("target.md:3", "target.md:8"),
          f"fixed={fixed!r} want={doc.replace('target.md:3', 'target.md:8')!r}")
    code3, out3, _ = run(tmp, ["doc.md"])
    check("--fix 后复跑: 全 OK", "[OK]" in out3 and code3 == 0, out3[:300])
    cleanup(tmp)

    # 13 --fix 不碰 OK 项
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    doc = f"引用 `target.md:5`「{ANCHOR}」\n"
    fixture(tmp, TARGET, doc)
    run(tmp, ["doc.md"], extra=("--fix",))
    check("--fix: 对 OK 不写盘", (tmp / "doc.md").read_text(encoding="utf-8") == doc, "文件被改了")
    cleanup(tmp)

    # 14 找不到文件 -> exit 3
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, "x\n")
    code, out, err = run(tmp, ["missing.md"])
    check("缺文件: exit 3", code == 3, f"got {code}")
    cleanup(tmp)

    # 15 多文件作用域
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:5`「{ANCHOR}」\n")
    (tmp / "doc2.md").write_text("引用 `target.md:5`「" + ANCHOR + "」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md", "doc2.md"])
    check("多文件: 两条都算 OK", "[OK] " in out and "anchored=2" in out, out[:300])
    cleanup(tmp)

    # 16 SELF：锚只在引用行自己身上出现（同文件自引）—— 不能判 OK，也不能判 GONE
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "doc.md").write_text(
        "# 文档甲\n正文\n引用 `doc.md:9`「只在一处出现的怪串只在」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("SELF: 判定 SELF", "[SELF]" in out, out[:300])
    check("SELF: exit 1（要人看）", code == 1, f"got {code}")
    cleanup(tmp)

    # 17 代码文件：运算符不能被规范化洗掉（>= vs > 是两个论断）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "target.py").write_text("def f():\n    if started > maxAgents:\n        pass\n", encoding="utf-8")
    (tmp / "doc.md").write_text("引用 `target.py:2`「if started >= maxAgents」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("代码: >= 与 > 不能算同一个锚", "[GONE]" in out, out[:300])
    check("代码: exit 1", code == 1, f"got {code}")
    cleanup(tmp)

    # 18 时间/端口/IP 不能被当成孤立引用
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "doc.md").write_text(
        "采样 06:31–07:01 经 127.0.0.1:1081 与 ::1 出口；另见 `:42`\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("时间/端口: 只有 1 条真孤立引用", "lone=1" in out, out[:300])
    cleanup(tmp)

    # 19 范围引用：命中落在区间内即 OK（-M 写法不能被丢掉）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:3-7`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("范围: 命中 5 落在 3-7 内判 OK", "[OK]" in out, out[:300])
    check("范围: 结尾行号不被吃掉", "refs=1" in out and "lone=0" in out, out[:300])
    cleanup(tmp)

    # 20 HIST: 历史引用不参与判定（且计数单列，供验收官查自声明豁免）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"第 1 轮当时是 `HIST:target.md:3`，现在 `target.md:5`「{ANCHOR}」\n")
    code, out, err = run(tmp, ["doc.md"])
    check("HIST: 不计失败，exit 0", code == 0, f"got {code} {out[:300]}")
    check("HIST: 单列计数", "[HIST]" in out, out[:400])
    check("HIST: 打提醒行", "[note] HIST=1" in out, out[:400])
    check("HIST: 同行的真引用照常判 OK", "[OK]" in out, out[:300])
    # 反证：不带 HIST: 前缀的同一条引用必须被判（GONE，因为锚在 but 行号 3 不对 -> DRIFT/NOANCHOR）
    (tmp / "doc.md").write_text("第 1 轮当时是 `target.md:3`\n", encoding="utf-8")
    code2, out2, _ = run(tmp, ["doc.md"])
    check("HIST 反证: 去掉前缀就照判（NOANCHOR 计失败）", "[NOANCHOR]" in out2, out2[:300])
    cleanup(tmp)

    # 21 --fix 不能改文件名里的数字（第 1 轮验收官 B1：a11.md:11 -> a20.md:11）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "a11.md").write_text("填充\n" * 19 + ANCHOR + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `a11.md:11`「{ANCHOR}」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"], extra=("--fix",))
    fixed = (tmp / "doc.md").read_text(encoding="utf-8")
    check("--fix: 文件名里的 11 不被改", "`a11.md:" in fixed, fixed[:160])
    check("--fix: 行号 11 -> 20", "`a11.md:20`" in fixed, fixed[:160])
    check("--fix 后该条变 OK", "[after-fix] OK=1" in out, out[:300])
    cleanup(tmp)

    # 22 --fix 保留范围（a-b 整体平移，宽度不变）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "target.md").write_text("填充\n" * 19 + ANCHOR + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `target.md:3-5`「{ANCHOR}」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"], extra=("--fix",))
    fixed = (tmp / "doc.md").read_text(encoding="utf-8")
    check("--fix 范围: 平移成 20-22（宽度保持）", "`target.md:20-22`" in fixed, fixed[:160])
    cleanup(tmp)

    # 23 --fix 保留逗号列表里的其余段
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "target.md").write_text("填充\n" * 19 + ANCHOR + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `target.md:2,105-107`「{ANCHOR}」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"], extra=("--fix",))
    fixed = (tmp / "doc.md").read_text(encoding="utf-8")
    check("--fix 列表: 首段改 20、其余保留", "`target.md:20,105-107`" in fixed, fixed[:160])
    cleanup(tmp)

    # 24 外部根同名文件：带目录的引用必须解析到正确那一个（第 1 轮验收官 B2）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    ext = Path(tempfile.mkdtemp(prefix="citeext-"))
    (ext / "tiny").mkdir()
    (ext / "profiles" / "web").mkdir(parents=True)
    (ext / "tiny" / "cordis.yml").write_text("a: 1\nb: 2\nc: 3\n", encoding="utf-8")
    (ext / "profiles" / "web" / "cordis.yml").write_text("x: 1\ny: 2\n" + ANCHOR + "\n", encoding="utf-8")
    (tmp / "scripts").mkdir()
    (tmp / "scripts" / "ledger-external.txt").write_text(str(ext) + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `profiles/web/cordis.yml:3`「{ANCHOR}」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("外部根: 两个同名文件时按路径后缀选对", "[OK]" in out, out[:400])
    check("外部根: 不落到 tiny/cordis.yml", "[GONE]" not in out, out[:400])
    cleanup(tmp)
    shutil.rmtree(ext, ignore_errors=True)

    # 25 工程内恰好一个同名文件 + 外部根里目录匹配的那一个：必须选外部那个（第 2 轮 B2 的真因）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    ext = Path(tempfile.mkdtemp(prefix="citeext-"))
    (tmp / "docs").mkdir()
    (tmp / "docs" / "x.md").write_text("无关内容一\n无关内容二\n", encoding="utf-8")
    (ext / "sub").mkdir()
    (ext / "sub" / "x.md").write_text("无关\n" + ANCHOR + "\n", encoding="utf-8")
    (tmp / "scripts").mkdir()
    (tmp / "scripts" / "ledger-external.txt").write_text(str(ext) + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `sub/x.md:2`「{ANCHOR}」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("同名+目录: 工程内唯一同名文件不能挤掉外部的正确目标", "[OK]" in out, out[:400])
    cleanup(tmp)
    shutil.rmtree(ext, ignore_errors=True)

    # 26 --fix 不能改文件换行风格（第 2 轮 N1）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "target.md").write_text("填充\n" * 19 + ANCHOR + "\n", encoding="utf-8")
    (tmp / "doc.md").write_bytes(f"引用 `target.md:3`「{ANCHOR}」\n".encode("utf-8"))
    before = (tmp / "doc.md").read_bytes()
    run(tmp, ["doc.md"], extra=("--fix",))
    after = (tmp / "doc.md").read_bytes()
    check("--fix: 不把 LF 改成 CRLF", b"\r\n" not in after, repr(after[:120]))
    check("--fix: 字节数只差行号那几位", len(after) - len(before) == len("20") - len("3"),
          f"before={len(before)} after={len(after)}")
    cleanup(tmp)

    # 27 家目录简写 `~/...` 必须被识别成引用（不能截成 `/.dsh/...` 判 UNRESOLVED）——第 3 轮 B2'
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    home_target = Path.home() / ".dsh" / "AGENTS.md"
    if home_target.is_file():
        (tmp / "doc.md").write_text("引用 `~/.dsh/AGENTS.md:1`「# ai-team」\n", encoding="utf-8")
        code, out, err = run(tmp, ["doc.md"])
        check("家目录: `~/` 引用不被判 UNRESOLVED", "[UNRESOLVED]" not in out, out[:300])
    else:
        check("家目录: 夹具前置条件（~/.dsh/AGENTS.md 存在）", False, "文件不存在，跳过即视为未覆盖")
    cleanup(tmp)

    # ---- 以下 28–32 为救火队长 2026-11-07 补（INC-20261107-01 §三.3）----

    # 28 家目录**隔离**版（第 4 轮 N1）：假 USERPROFILE + 工程内同路径同名文件，必须解析到外部那个。
    #    工程内的 .dsh/AGENTS.md 也含同一锚串但在别的行 —— 解析错了会是 DRIFT 而不是 OK。
    import json as _json
    import os as _os
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    home = Path(tempfile.mkdtemp(prefix="citehome-"))
    (home / ".dsh").mkdir()
    (home / ".dsh" / "AGENTS.md").write_text("# 外部\n" + ANCHOR + "\n", encoding="utf-8")
    (tmp / ".dsh").mkdir()
    (tmp / ".dsh" / "AGENTS.md").write_text("# 工程内\n填充\n填充\n" + ANCHOR + "\n", encoding="utf-8")
    (tmp / "AGENTS.md").write_text("# 根\n" + ANCHOR + "\n", encoding="utf-8")
    (tmp / "doc.md").write_text(f"引用 `~/.dsh/AGENTS.md:2`「{ANCHOR}」\n", encoding="utf-8")
    env = dict(_os.environ, USERPROFILE=str(home), HOME=str(home))
    p = subprocess.run([PY, str(TOOL), "--root", str(tmp), "--files", "doc.md", "--json"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    out = p.stdout
    check("家目录隔离: 判 OK", "[OK]" in out, out[:300])
    try:
        items = _json.loads(out[out.index("\n[\n") + 1:])
        tp = items[0].get("target_path", "")
    except (ValueError, IndexError, KeyError):
        tp = ""
    check("家目录隔离: 目标是假家目录里的文件，不是工程内同名", tp.startswith(str(home)), f"target_path={tp!r}")
    cleanup(tmp)
    shutil.rmtree(home, ignore_errors=True)

    # 29 CRLF 文档 --fix（Windows 换行）：CRLF 必须原样保留。原 26 只测 LF 文档，读端去掉 newline=""
    #    （CRLF 被悄悄改成 LF）时 53 条全绿 —— 救火实测假绿。
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "target.md").write_bytes(("填充\r\n" * 19 + ANCHOR + "\r\n").encode("utf-8"))
    doc = f"第一行\r\n引用 `target.md:3`「{ANCHOR}」\r\n第三行\r\n"
    (tmp / "doc.md").write_bytes(doc.encode("utf-8"))
    run(tmp, ["doc.md"], extra=("--fix",))
    after = (tmp / "doc.md").read_bytes()
    check("--fix CRLF: 三个 CRLF 原样保留", after.count(b"\r\n") == 3, repr(after[:160]))
    check("--fix CRLF: 只差行号那几位", after == doc.replace("target.md:3", "target.md:20").encode("utf-8"),
          repr(after[:160]))
    cleanup(tmp)

    # 30 锚串内含 ASCII 引号 / 反引号：不能被静默截短（救火 P1/P2：截短后目标改了照样 OK）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "t.py").write_text('x = 1\nif name.startswith("XX_CHANGED"):\n    pass\n', encoding="utf-8")
    (tmp / "doc.md").write_text('引用 `t.py:2`「if name.startswith("~/"):」\n', encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("内含引号: 引号内的内容改了必须 GONE（不是截短后的假 OK）", "[GONE]" in out and code == 1, out[:300])
    (tmp / "t.py").write_text('x = 1\nNAMED = re.compile(r"`?([A-Z]*)")\n', encoding="utf-8")
    (tmp / "doc.md").write_text('引用 `t.py:2`「NAMED = re.compile(r"`?([A-Z]*)")」\n', encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"], extra=("--json",))
    check("内含引号: 完整锚串原样取到", '"anchor": "NAMED = re.compile(r\\"`?([A-Z]*)\\")"' in out, out[-400:])
    cleanup(tmp)

    # 31 定界符不配对（开「闭`）：不能当成锚
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    fixture(tmp, TARGET, f"引用 `target.md:5`「{ANCHOR}` 后面还有字\n")
    code, out, err = run(tmp, ["doc.md"], extra=("--strict",))
    check("内含引号: 开「闭` 不配对判 NOANCHOR", "[NOANCHOR]" in out and code == 1, out[:300])
    cleanup(tmp)

    # 32 目标含 \x0c（form feed）：行号必须与编辑器/grep 一致（救火 P6：splitlines 多切一行）
    tmp = Path(tempfile.mkdtemp(prefix="citecase-"))
    (tmp / "t.py").write_text("a = 1\x0c\nb = 2\nUNIQUE_MARKER_ABCDEFG = 3\n", encoding="utf-8")
    (tmp / "doc.md").write_text("引用 `t.py:3`「UNIQUE_MARKER_ABCDEFG = 3」\n", encoding="utf-8")
    code, out, err = run(tmp, ["doc.md"])
    check("换行口径: form feed 不算换行（锚在第 3 行判 OK）", "[OK]" in out and code == 0, out[:300])
    cleanup(tmp)

    print(f"checks={RESULTS['checks']} failed={RESULTS['failed']}")
    return 0 if RESULTS["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
