# DELIVERY · 重试断言（**可核版** —— 每条都有 Read/Grep 能拿到的依据）

## A1 重试确实发生了

证据一（代码控制流）：`scripts/runner.py` 里 attempts 循环的判据是
`tries = (1 + retry_n) if ti == 0 else 1`，`retry_n` 取自 `node.on_fail.retry`。

证据二（落盘产物）：`runs/_harness/retrythenok/RUN.json` 里 `nodes.w_try.attempts` 的值是 `2`，
`nodes.w_try.status` 是 `ok`。用 Read 打开这个文件即可核对。

## A2 第 1 次的痕迹没被洗掉

证据：`runs/_harness/retrythenok/NODES/w_try.json` 的 `attempts[0].checks.overclaim` 是
`["tasks/DAG-2026-11-06/fixtures/sandbox/notes.md"]`，`attempts[0].ok` 是 `false`；
而 `attempts[1].ok` 是 `true`。用 Read 打开即可核对这两项。

## A3 越界不可洗

证据一（代码）：`scripts/runner.py` 里有一行 `harm |= set(checks["out_of_scope"]) | set(checks["readonly_violation"])`，
并且停止条件里有 `elif harm: stop = "harm"` —— 一旦 harm 非空就不再重试。

证据二（落盘产物）：`runs/_harness/retry-oos/NODES/w_bad.json` 的 `attempt_count` 是 `1`
（不是 2），`harm` 是 `["tasks/DAG-2026-11-06/fixtures/sandbox/OFF-LIMITS.md"]`，`status` 是 `fail`。
