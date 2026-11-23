# -*- coding: utf-8 -*-
"""cite-check —— 锚定引用检查器（v0.2）。

问题：交付文档用 `文件:行号` 引用别处内容，行号是易变量 —— 被引文件一编辑就漂，
而且漂得**静默**（那一行永远存在，只是内容换人了）。七轮送审里有 2 条 blocking、1 条半
直接栽在这上面，另有 2 条 blocking 是「为了救行号去手写清单」引发的次生问题。

治法：引用必须带**锚串**（从目标行逐字复制的一段文本）。锚串是内容的函数，不随插入漂；
行号降级为**可重算的缓存**，由 --fix 维护。

判定：
  OK        锚唯一命中，且命中行落在声明的点/区间里
  DRIFT     锚唯一命中，行号不在声明范围           -> --fix 重算（只改数字）
  AMBIG     锚命中多处（引用行本身已排除）          -> 加长锚串
  SELF      只命中引用行自己（同文件自引）          -> 人看，工具不猜
  GONE      锚 0 命中                              -> 引用已失效，必须人工处理
  WEAK      锚规范化后 < 8 字符
  NOANCHOR  引用后面没有锚（旧语法）               -> --strict 下算失败
  LONE      孤立 `:N`（文件归属歧义）              -> 不判定，只统计
  HIST      `HIST:` 前缀的历史引用                 -> 不判定，只计数（自声明豁免）
  UNRESOLVED 目标文件找不到（含工程外根）

规范化**按目标文件类型分**（2026-11-07 opus 评审第 5 条）：
  `.md`        去 markdown 标记与中英标点
  代码/数据   **只压缩空白** —— 因为代码里 `>=` `>` `=` 就是论断本身，洗掉运算符等于让
              `started >= maxAgents` 与 `started > maxAgents` 变成同一个锚。

用法：
  python scripts/cite-check.py                                    # 全仓汇总
  python scripts/cite-check.py --files tasks/X/DELIVERY.md        # 闸门：只查本单元
  python scripts/cite-check.py --files X.md --strict              # 新交付：NOANCHOR 也算失败
  python scripts/cite-check.py --files X.md --fix                 # 把 DRIFT 的行号改对
  python scripts/cite-check.py --json

退出码：0 全过 / 1 有 GONE·AMBIG·SELF·WEAK·UNRESOLVED（或 --strict 下 NOANCHOR）/ 2 只有 DRIFT / 3 用法错误
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
EXTS = r"md|ps1|py|mjs|json|yml|yaml|txt"
CODE_EXTS = {".py", ".mjs", ".ps1", ".json", ".yml", ".yaml"}

# 带文件名的引用：[`]name:1[,2][-3][`]。字符类**必须含 `~`**，否则 `~/.dsh/x` 会被截成 `/.dsh/x`
# 而永远判 UNRESOLVED（第 3 轮验收官 B2'）。
NAMED = re.compile(r"`?([A-Za-z0-9_./\\~\-]*\.(?:%s)):(\d+(?:\s*[,，]\s*\d+)*(?:\s*-\s*\d+)?)`?" % EXTS)
# 孤立引用：:N / :N-M。冒号前面紧挨数字或另一个冒号的**不算**（时间 06:31、端口 127.0.0.1:1081、IPv6）
LONE = re.compile(r"(?<![0-9:]):(\d+(?:\s*-\s*\d+)?)(?!\d)")
# 锚 = 引用之后第一个反引号 / 「」/ 引号包裹的内容。**定界符必须成对**：「…」内部允许出现 ` 与 "，
# 否则锚会在第一个内部引号处被**静默截短**（救火 2026-11-07 P1/P2：家目录分支那行（startswith 加 ~/ 前缀的判断）
# 被截成 `if name.startswith(`，目标里引号内的内容改掉后仍判 OK = 假 OK）。开「闭` 不配对 -> 无锚。
ANCHOR = re.compile(r"^[\s、，,;；:：·）)】\]]*(?:「([^」\n]+)」|`([^`\n]+)`|\"([^\"\n]+)\"|“([^”\n]+)”)")
# 「锚」若本身就是引用形态（文件:行号，或纯文件名/路径），它不是锚，只是同行相邻的引用
REF_LIKE = re.compile(r"[A-Za-z0-9_./\\~\-]+\.(?:%s)(?::\d+)?" % EXTS)
MD_DROP = re.compile(r"[\s`*_~\[\]()（）「」『』\"'“”‘’、，。：；！？·…—\-=|/\\<>{}]+")

SKIP_DIRS = {"runs", "__pycache__", ".git", "node_modules"}


def split_lines(text):
    """只按换行切（read_text 已把 \\r\\n / \\r 归一成 \\n）。

    坑（救火 2026-11-07 P6）：str.splitlines() 还会在 \\x0b \\x0c \\x1c-\\x1e \\x85 \\u2028 \\u2029 处切，
    行号就和编辑器 / grep 不一致 —— 目标里一个 form feed 就让工具说锚在第 4 行、编辑器说第 3 行，
    --fix 会把对的行号「修」错。
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


RAW_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


def norm_for(path, s):
    """按目标文件类型规范化。代码只压空白 —— 运算符是论断的一部分。"""
    if path.suffix.lower() in CODE_EXTS:
        return re.sub(r"\s+", "", s)
    return MD_DROP.sub("", s)


def parse_spec(spec):
    """'27,42,105-107' -> (points={27,42,105}, ranges=[(105,107)])"""
    points, ranges = set(), []
    for chunk in re.split(r"[,，]", spec):
        c = chunk.strip()
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", c)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                ranges.append((a, b))
            else:
                ranges.append((b, a))
        elif c.isdigit():
            points.add(int(c))
    return points, ranges


def in_span(hit, points, ranges):
    return hit in points or any(a <= hit <= b for a, b in ranges)


def skipped(p, root):
    try:
        parts = set(p.relative_to(root).parts)
    except ValueError:
        return True
    return bool(parts & SKIP_DIRS)


def load_external_roots(root):
    f = root / "scripts" / "ledger-external.txt"
    out = []
    if not f.is_file():
        return out
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(Path(os.path.expanduser(line)))
    return out


EXTERNAL = []


def target_ok(name, path, root):
    """引用里写了目录就要求**路径后缀匹配**，不能只对 basename。

    坑（2026-11-07 第 1 轮验收官 B2）：`ledger-external.txt` 明确警告过
    `~/.dsh/cordis.patch.yml`（9 行）与 `~/.dsh/profiles/web/cordis.patch.yml`（111 KB）
    **是两个不同文件**。只按 basename 匹配会把前者当成后者，给出假 GONE/假 OK。
    """
    if "/" not in name and "\\" not in name:
        return True                                  # 纯 basename：无从判目录
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    p = str(path).replace("\\", "/")
    try:
        rel = str(Path(path).resolve().relative_to(root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        rel = None
    if rel is not None and (rel == n or rel.endswith("/" + n)):
        return True
    return p.endswith("/" + n)


def resolve_target(name, root):
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if name.startswith("~/"):
        # 家目录简写：直接当工程外路径解析，别在工程内 rglob 里绕（第 3 轮 B2'）
        cand = Path(os.path.expanduser(name))
        if cand.is_file():
            return cand.resolve()
        return None
    cand = root / name
    if cand.is_file():
        return cand.resolve()
    base = name.split("/")[-1]
    hits = [p for p in root.rglob(base) if not skipped(p, root)]
    # 引用里**写了目录**就必须核对路径后缀 —— 不管候选是 1 个还是多个。
    # （第 2 轮验收官 B2：原来只在 len(hits) > 1 时才过滤，于是「工程内恰好一个同名文件」
    #   会直接把外部那个正确的挤掉，给出假 GONE。）
    if "/" in name or "\\" in name:
        hits = [p for p in hits if target_ok(name, p, root)]
    if len(hits) == 1:
        return hits[0].resolve()
    # 工程外根（复用 ledger-external.txt，别再维护第二份清单）
    ext_hits = []
    for ext_root in EXTERNAL:
        if ext_root.is_file() and ext_root.name == base:
            ext_hits.append(ext_root)
        elif ext_root.is_dir():
            ext_hits.extend(ext_root.rglob(base))
    ext_hits = [p for p in ext_hits if target_ok(name, p, root)]
    if len(ext_hits) == 1:
        return ext_hits[0].resolve()
    return None                                      # 多个同名候选 -> 不猜


class Target:
    def __init__(self, path):
        self.path = path
        self.raw = split_lines(path.read_text(encoding="utf-8", errors="replace"))
        self.flat = [norm_for(path, x) for x in self.raw]
        # 含引用语法的行：自引时要把它们排除，否则锚在「引用行自身」上必然命中
        self.cite_lines = {i for i, line in enumerate(self.raw, 1)
                           if NAMED.search(line) or LONE.search(line)}

    def find(self, anchor, self_ref=False):
        a = norm_for(self.path, anchor)
        if len(a) < 8:
            return "WEAK", []
        hits = [i for i, line in enumerate(self.flat, 1) if a in line]
        if self_ref:
            only_self = [i for i in hits if i in self.cite_lines]
            hits = [i for i in hits if i not in self.cite_lines]
            if not hits and only_self:
                return "SELF", only_self[:3]
        if len(hits) > 1:
            return "AMBIG", hits[:5]
        if not hits:
            return "GONE", []
        return "HIT", hits


def scan(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    items = []
    for i, line in enumerate(split_lines(text), 1):
        for m in NAMED.finditer(line):
            an = ANCHOR.match(line[m.end():])
            anchor = next((g for g in an.groups() if g), None) if an else None
            if anchor and REF_LIKE.search(anchor):
                anchor = None  # 相邻的另一个引用不是锚
            pts, rngs = parse_spec(m.group(2))
            items.append({"src": str(path), "line": i, "token": m.group(0),
                          "target": m.group(1), "spec": m.group(2),
                          "points": sorted(pts), "ranges": rngs, "anchor": anchor,
                          "historical": line[max(0, m.start() - 5):m.start()] == "HIST:",
                          "col": (m.start(), m.end())})
        for m in LONE.finditer(line):
            if re.search(r"\.(?:%s)$" % EXTS, line[:m.start()]):
                continue
            pts, rngs = parse_spec(m.group(1))
            items.append({"src": str(path), "line": i, "token": m.group(0),
                          "target": None, "spec": m.group(1),
                          "points": sorted(pts), "ranges": rngs, "anchor": None,
                          "historical": line[max(0, m.start() - 5):m.start()] == "HIST:",
                          "col": (m.start(), m.end())})
    return items


def classify(items, root):
    cache = {}
    for it in items:
        it["verdict"] = None
        if it["target"] is None:
            it["verdict"] = "LONE"
            continue
        if it.get("historical"):
            # HIST: 前缀 = 历史引用（记录「当时是第几行」），不参与漂移判定，只计数供人看
            it["verdict"] = "HIST"
            continue
        tgt = resolve_target(it["target"], root)
        if tgt is None:
            it["verdict"] = "UNRESOLVED"
            continue
        if not it["anchor"]:
            it["verdict"] = "NOANCHOR"
            continue
        if tgt not in cache:
            cache[tgt] = Target(tgt)
        self_ref = Path(it["src"]).resolve() == tgt
        state, hits = cache[tgt].find(it["anchor"], self_ref=self_ref)
        if state == "WEAK":
            it["verdict"], it["now_at"] = "WEAK", []
        elif state == "AMBIG":
            it["verdict"], it["now_at"] = "AMBIG", hits
        elif state == "SELF":
            it["verdict"], it["now_at"] = "SELF", hits
        elif not hits:
            it["verdict"], it["now_at"] = "GONE", []
        elif in_span(hits[0], set(it["points"]), it["ranges"]):
            it["verdict"], it["now_at"] = "OK", hits[:1]
        else:
            it["verdict"], it["now_at"] = "DRIFT", hits[:1]
        it["target_path"] = str(tgt)
    return items


def rebuild_spec(spec, hit):
    """只重写 spec 的**第一个段**，其余原样保留（含分隔符与空白）。

    坑（2026-11-07 第 1 轮验收官 B1）：绝不能用 token.replace(spec, new) ——
    文件名里若含同样的数字串（`a11.md:11`），第一个命中会落在文件名上，把文件名改坏。
    所以调用方必须按 `m.start(2)`/`m.end(2)` 的绝对位置替换，而不是字符串替换。
    """
    m = re.match(r"^(\s*)(\d+)(\s*-\s*(\d+))?", spec)
    if not m:
        return spec
    lead, first = m.group(1), int(m.group(2))
    rest = spec[m.end():]
    if m.group(4) is not None:                 # 范围 a-b
        a, b = first, int(m.group(4))
        if a <= hit <= b:
            return spec                        # 锚落在区间内：整条不动
        width = b - a
        sep = m.group(3)[: len(m.group(3)) - len(m.group(4))]   # 只留分隔符（'-' / ' - '），不带数字
        return f"{lead}{hit}{sep}{hit + width}{rest}"            # 整体平移，宽度不变
    if first == hit:
        return spec
    return f"{lead}{hit}{rest}"


def apply_fix(items, root):
    """只改行号数字：按列位置替换 spec 那一段，锚串与文件名一个字节都不动。"""
    changed = 0
    by_file = {}
    for it in items:
        if it["verdict"] != "DRIFT" or not it.get("now_at"):
            continue
        by_file.setdefault(it["src"], []).append(it)
    for src, its in by_file.items():
        p = Path(src)
        # newline="" 两端都要：否则 Windows 上会把整份 LF 文件改写成 CRLF（第 2 轮 N1：
        # 夹具 364 B → 371 B，每一行都长了一个字节）。--fix 只该动那几个数字。
        with open(p, encoding="utf-8", newline="") as f:
            lines = RAW_LINE.findall(f.read())     # 与 split_lines 同一套切法（不认 \x0c / \u2028）
        for it in sorted(its, key=lambda x: (-x["line"], -x["col"][0])):
            idx = it["line"] - 1
            line = lines[idx]
            m = NAMED.search(line, max(0, it["col"][0] - 2))
            if not m or m.group(0) != it["token"]:
                continue
            new_spec = rebuild_spec(m.group(2), it["now_at"][0])
            if new_spec == m.group(2):
                continue
            s2, e2 = m.start(2), m.end(2)          # 只动 spec 这一段
            lines[idx] = line[:s2] + new_spec + line[e2:]
            changed += 1
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("".join(lines))
    return changed


def main():
    global EXTERNAL
    ap = argparse.ArgumentParser(description="锚定引用检查器")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--files", nargs="*", help="只查这些文件（相对 root 或绝对路径）")
    ap.add_argument("--strict", action="store_true", help="NOANCHOR 也算失败（新交付用）")
    ap.add_argument("--fix", action="store_true", help="把 DRIFT 的行号改对（只改数字）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--exclude", nargs="*", default=[], help="排除路径里含这些子串的文件")
    ap.add_argument("--out", help="把 stdout 原样再写一份 UTF-8 文件（避免 PowerShell 重定向转码）")
    ap.add_argument("--show", default="DRIFT,GONE,AMBIG,SELF,WEAK,UNRESOLVED")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    # 输出同时进 stdout 和（可选）文件，保证「落盘的就是命令打出来的」
    buf = []

    def emit(s=""):
        print(s)
        buf.append(s)

    root = Path(args.root).resolve()
    EXTERNAL = load_external_roots(root)

    if args.files:
        files, missing = [], []
        for f in args.files:
            p = Path(f)
            if not p.is_absolute():
                p = root / p
            (files if p.is_file() else missing).append(p.resolve() if p.is_file() else str(p))
        if missing:
            print("[error] 找不到文件: " + ", ".join(missing), file=sys.stderr)
            return 3
    else:
        files = [p for p in sorted(root.rglob("*.md")) if not skipped(p, root)]
    if args.exclude:
        files = [p for p in files if not any(x in str(p).replace("\\", "/") for x in args.exclude)]
    if not files:
        print("[error] 没有可检查的文件", file=sys.stderr)
        return 3

    items = []
    for p in files:
        items.extend(scan(p))
    items = classify(items, root)

    counts = {}
    for it in items:
        counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1

    named = sum(1 for it in items if it["target"] is not None)
    anchored = sum(counts.get(k, 0) for k in ("OK", "DRIFT", "AMBIG", "SELF", "GONE", "WEAK"))

    def relpath(s):
        try:
            return str(Path(s).relative_to(root)).replace("\\", "/")
        except ValueError:
            return s

    emit(f"[scope] files={len(files)}  refs={len(items)}  named={named}  lone={counts.get('LONE', 0)}")
    emit(f"[cover] anchored={anchored} ({anchored * 100.0 / max(named, 1):.1f}% of named)")
    for k in ("OK", "DRIFT", "AMBIG", "SELF", "GONE", "WEAK", "NOANCHOR", "UNRESOLVED", "LONE", "HIST"):
        if counts.get(k):
            emit(f"[{k}]{' ' * (11 - len(k))}{counts[k]}")
    if counts.get("HIST"):
        emit(f"[note] HIST={counts['HIST']} 条历史引用（HIST: 前缀）不计入判定；"
             f"新增 HIST 属于自声明豁免，验收官应单列检查")

    if args.fix:
        n = apply_fix(items, root)
        emit(f"[fix] rewrote {n} line-number(s)")
        # 退出码必须反映**修完之后**的状态（2026-11-07 第 1 轮 N5：原来用的是修复前的计数）
        items = []
        for p in files:
            items.extend(scan(p))
        items = classify(items, root)
        counts = {}
        for it in items:
            counts[it["verdict"]] = counts.get(it["verdict"], 0) + 1
        emit("[after-fix] " + "  ".join(f"{k}={counts[k]}" for k in sorted(counts)))

    want = {x.strip() for x in args.show.split(",") if x.strip()}
    shown = 0
    for it in items:
        if it["verdict"] in want and shown < args.limit:
            shown += 1
            tgt = it["target"] or "-"
            emit(f"  [{it['verdict']}] {relpath(it['src'])}:{it['line']}  {it['token']} -> {tgt} {it['spec']}")
            if it.get("anchor"):
                emit(f"        anchor={it['anchor'][:70]!r} now_at={it.get('now_at')}")

    if args.json:
        emit(json.dumps(items, ensure_ascii=False, indent=1))

    if args.out:
        Path(args.out).write_text("\n".join(buf) + "\n", encoding="utf-8")
        print(f"[out] wrote {args.out}")

    hard = sum(counts.get(k, 0) for k in ("GONE", "AMBIG", "SELF", "WEAK", "UNRESOLVED"))
    if args.strict:
        hard += counts.get("NOANCHOR", 0)
    if hard:
        return 1
    if counts.get("DRIFT", 0):
        return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
