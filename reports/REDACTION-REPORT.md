# 开源脱敏报告

- 生成时间：2026-11-24 03:33:20
- 源目录：`<AI-TEAM-ROOT>`（本机私有工作区）
- 目标目录：`<REPO-ROOT>`（本开放仓库根）
- 迁移文件数：**151**
- 跳过文件数：**5**

## 规则命中汇总

| 规则 | 命中次数 |
|---|---|
| `user-abs-path` | 48 |
| `proxy-port` | 38 |
| `private-script-path` | 13 |
| `vps-ip` | 10 |
| `private-tool` | 6 |
| `lan-ip` | 6 |
| `local-account` | 4 |
| `private-identity` | 3 |
| `tun-subnet` | 3 |
| `local-wan-ip` | 2 |
| `private-project-path` | 1 |

## 逐文件明细

| 文件 | 字节 | 命中 | 规则分布 |
|---|---|---|---|
| `engine/agents-budget.py` | 11444 | 0 | — |
| `engine/cite-check-harness.py` | 18386 | 0 | — |
| `engine/cite-check-mutate.py` | 5245 | 0 | — |
| `engine/cite-check.py` | 16312 | 0 | — |
| `engine/codex-trace-patch.py` | 14943 | 0 | — |
| `engine/constraints-baseline.txt` | 54073 | 2 | private-tool×2 |
| `engine/constraints-check.py` | 30210 | 0 | — |
| `engine/constraints-mutate.py` | 13336 | 0 | — |
| `engine/constraints-selftest.py` | 25158 | 0 | — |
| `engine/dag-gate.mjs` | 4059 | 0 | — |
| `engine/dag.workflow.mjs` | 60693 | 0 | — |
| `engine/doc-sync.json` | 1529 | 0 | — |
| `engine/doc-sync.py` | 13585 | 0 | — |
| `engine/fire-chief.py` | 46812 | 2 | proxy-port×2 |
| `engine/graph-rules.md` | 8134 | 0 | — |
| `engine/ledger-audit.ps1` | 11692 | 0 | — |
| `engine/ledger-check-baseline.txt` | 65 | 0 | — |
| `engine/ledger-check-test.ps1` | 17090 | 0 | — |
| `engine/ledger-check.ps1` | 17213 | 0 | — |
| `engine/ledger-external.txt` | 1246 | 0 | — |
| `engine/manager.ps1` | 2682 | 0 | — |
| `engine/manager.py` | 20223 | 2 | private-script-path×2 |
| `engine/manager.schema.json` | 8281 | 0 | — |
| `engine/review-backfill.ps1` | 3085 | 0 | — |
| `engine/review-codex.py` | 10157 | 0 | — |
| `engine/review-index.ps1` | 6739 | 0 | — |
| `engine/review-snapshot.py` | 10706 | 0 | — |
| `engine/runner.py` | 128055 | 4 | proxy-port×3, private-script-path×1 |
| `engine/selfcheck.workflow.mjs` | 4787 | 0 | — |
| `docs/FIRE-CHIEF.md` | 7418 | 0 | — |
| `docs/INC-TEMPLATE.md` | 11383 | 1 | user-abs-path×1 |
| `docs/PLAN.md` | 8005 | 0 | — |
| `docs/REVIEW-CONTRACT.md` | 13101 | 0 | — |
| `docs/TRIGGER-POLICY.md` | 35720 | 2 | private-script-path×2 |
| `docs/decisions/ACCEPT-HARDEN-2026-11-13-round1.json` | 2805 | 1 | user-abs-path×1 |
| `docs/decisions/AGENTS-DIET-2026-11-12-round1.json` | 2311 | 1 | private-project-path×1 |
| `docs/decisions/AGENTS-DIET-2026-11-12-round2.json` | 1953 | 0 | — |
| `docs/decisions/AGENTS-PATCH-2026-11-06-round2.json` | 8817 | 3 | vps-ip×1, private-tool×2 |
| `docs/decisions/AGENTS-PATCH-2026-11-06.json` | 8248 | 4 | user-abs-path×3, vps-ip×1 |
| `docs/decisions/ASTRA-NATIVE-2B1-2026-11-14-round2.json` | 4242 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-2B1-2026-11-14-round3.json` | 3857 | 0 | — |
| `docs/decisions/ASTRA-NATIVE-2B1-2026-11-14-round4.json` | 4038 | 2 | user-abs-path×2 |
| `docs/decisions/ASTRA-NATIVE-2B2-2026-11-14-round1.json` | 4343 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-CORE-IMPL-2026-11-15-round1.json` | 4582 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-CORE-IMPL-2026-11-15-round2.json` | 4701 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-LIVE-2026-11-15-round1.json` | 5303 | 3 | user-abs-path×2, proxy-port×1 |
| `docs/decisions/ASTRA-NATIVE-N1-2026-11-15-round1.json` | 3668 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-N1-2026-11-15-round2.json` | 2794 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-RELEASE-2026-11-15-round1.json` | 4393 | 2 | user-abs-path×2 |
| `docs/decisions/ASTRA-NATIVE-RELEASE-2026-11-15-round2.json` | 4560 | 2 | user-abs-path×2 |
| `docs/decisions/ASTRA-NATIVE-RELEASE-2026-11-15-round3.json` | 4394 | 2 | user-abs-path×2 |
| `docs/decisions/ASTRA-NATIVE-SAFETY-2026-11-15-round1.json` | 5105 | 2 | user-abs-path×2 |
| `docs/decisions/ASTRA-NATIVE-SAFETY-2026-11-15-round2.json` | 5120 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-WEB-ASSEMBLY-2026-11-15-round1.json` | 3795 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-WEB-ASSEMBLY-2026-11-15-round2.json` | 4394 | 1 | user-abs-path×1 |
| `docs/decisions/ASTRA-NATIVE-WEB-ASSEMBLY-2026-11-15-round4.json` | 5296 | 3 | user-abs-path×1, vps-ip×1, proxy-port×1 |
| `docs/decisions/B4-2026-11-10-round1.json` | 5630 | 0 | — |
| `docs/decisions/B4-2026-11-10-round2.json` | 4291 | 0 | — |
| `docs/decisions/CHECKPOINT1-2026-11-13-round1.json` | 2481 | 1 | user-abs-path×1 |
| `docs/decisions/CODEX-ENGINE-2026-11-11-round1.json` | 10535 | 1 | proxy-port×1 |
| `docs/decisions/CODEX-ENGINE-2026-11-11-round2.json` | 3915 | 11 | proxy-port×11 |
| `docs/decisions/CODEX-ENGINE-2026-11-11-round3.json` | 2758 | 2 | proxy-port×2 |
| `docs/decisions/CODEX-TRACE-2026-11-10-round1.json` | 5835 | 0 | — |
| `docs/decisions/CODEX-TRACE-2026-11-10-round2.json` | 5061 | 0 | — |
| `docs/decisions/CONSTRAINTS-2026-11-17-round1.json` | 5751 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round2.json` | 5543 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round3.json` | 5923 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round4.json` | 7408 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round5.json` | 7354 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round6.json` | 6540 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round7.json` | 6725 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS-2026-11-17-round8.json` | 5013 | 1 | user-abs-path×1 |
| `docs/decisions/CONSTRAINTS.json` | 9689 | 0 | — |
| `docs/decisions/DAG-2026-11-06-round1.json` | 8076 | 0 | — |
| `docs/decisions/DAG-2026-11-06-round2.json` | 5333 | 0 | — |
| `docs/decisions/DAG-2026-11-07-rules-round1.json` | 12615 | 0 | — |
| `docs/decisions/DAG-2026-11-07-rules-round2.json` | 8472 | 0 | — |
| `docs/decisions/DAG-2026-11-07-step2a1-round1.json` | 7939 | 1 | vps-ip×1 |
| `docs/decisions/DAG-2026-11-07-step2a1-round2.json` | 4596 | 1 | vps-ip×1 |
| `docs/decisions/DAG-2026-11-07-step2a2-round1.json` | 4943 | 1 | vps-ip×1 |
| `docs/decisions/DAG-2026-11-07-step2a2-round2.json` | 3969 | 0 | — |
| `docs/decisions/DAG-2026-11-07-step2a2-round3.json` | 4483 | 0 | — |
| `docs/decisions/DAG-2026-11-07-step2a2-round4.json` | 4931 | 2 | local-account×2 |
| `docs/decisions/DAG-2026-11-07-step2a2-round5.json` | 3497 | 2 | local-account×2 |
| `docs/decisions/DAG-EXAM-2026-11-12-round1.json` | 6479 | 0 | — |
| `docs/decisions/DAG-EXAM-2026-11-12-round2.json` | 2628 | 0 | — |
| `docs/decisions/DIET-FOLLOWUP-2026-11-12-round1.json` | 5537 | 0 | — |
| `docs/decisions/DIET-FOLLOWUP-2026-11-12-round2.json` | 3797 | 1 | user-abs-path×1 |
| `docs/decisions/DOC-SYNC-2026-11-11-round1.json` | 2930 | 0 | — |
| `docs/decisions/DOC-SYNC-2026-11-11-round2.json` | 3358 | 0 | — |
| `docs/decisions/DOC-SYNC-2026-11-11-round3.json` | 3542 | 0 | — |
| `docs/decisions/E2E-2026-11-09-round1.json` | 6285 | 0 | — |
| `docs/decisions/E2EFIX-2026-11-09-round1.json` | 8595 | 0 | — |
| `docs/decisions/E2EFIX-2026-11-09-round2.json` | 5345 | 0 | — |
| `docs/decisions/EDGE-CDP-BROWSER-2026-11-16-round1.json` | 4091 | 1 | user-abs-path×1 |
| `docs/decisions/EDGE-OFFICIAL-GUI-CANDIDATE-2026-11-16-round1.json` | 4849 | 1 | user-abs-path×1 |
| `docs/decisions/EDGE-OFFICIAL-GUI-CANDIDATE-2026-11-16-round2.json` | 4952 | 1 | user-abs-path×1 |
| `docs/decisions/EDGE-OFFICIAL-GUI-FIX-2026-11-17-round1.json` | 2839 | 2 | user-abs-path×1, proxy-port×1 |
| `docs/decisions/EDGE-OFFICIAL-HOST-2026-11-16-round1.json` | 3511 | 1 | user-abs-path×1 |
| `docs/decisions/EDGE-OFFICIAL-REAL-MODEL-2026-11-16-round1.json` | 3409 | 1 | user-abs-path×1 |
| `docs/decisions/EVIDENCE-GUARD-2026-11-10-round1.json` | 3135 | 2 | vps-ip×1, proxy-port×1 |
| `docs/decisions/EVIDENCE-GUARD-2026-11-10-round2.json` | 2764 | 2 | vps-ip×1, proxy-port×1 |
| `docs/decisions/EXEC-CONSIST-2026-11-10-round1.json` | 5549 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round1.json` | 2427 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round2.json` | 2465 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round3.json` | 2387 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round4.json` | 2216 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round5-scope.json` | 906 | 0 | — |
| `docs/decisions/FIRE-CHIEF-2026-11-07-round7.json` | 1490 | 0 | — |
| `docs/decisions/FIRE-CHIEF-GUARD-2026-11-11-round1.json` | 8675 | 0 | — |
| `docs/decisions/FIRE-CHIEF-GUARD-2026-11-11-round2.json` | 5812 | 0 | — |
| `docs/decisions/FIRE-CHIEF-GUARD-2026-11-11-round3.json` | 5338 | 0 | — |
| `docs/decisions/INC-20261110-01-fire-round1.json` | 6553 | 0 | — |
| `docs/decisions/INC-20261110-01-fire-round2.json` | 4859 | 0 | — |
| `docs/decisions/LEDGER-JOB-2026-11-11-round1.json` | 9117 | 0 | — |
| `docs/decisions/LEDGER-JOB-2026-11-11-round2.json` | 7806 | 0 | — |
| `docs/decisions/LEDGER-JOB-2026-11-11-round3.json` | 10557 | 2 | private-tool×2 |
| `docs/decisions/LEDGER-JOB-2026-11-11-round4.json` | 6483 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round2.json` | 15207 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round3.json` | 14396 | 1 | user-abs-path×1 |
| `docs/decisions/LINENO-2026-11-07-round4.json` | 11608 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round5.json` | 11253 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round6.json` | 8644 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round7.json` | 8987 | 0 | — |
| `docs/decisions/LINENO-2026-11-07-round8.json` | 17132 | 18 | vps-ip×1, lan-ip×1, proxy-port×9, private-script-path×7 |
| `docs/decisions/LINENO-2026-11-07-round9.json` | 8523 | 3 | vps-ip×1, lan-ip×1, proxy-port×1 |
| `docs/decisions/P0-2026-11-05-round1.json` | 2950 | 5 | user-abs-path×2, private-identity×3 |
| `docs/decisions/P0-2026-11-05-round2.json` | 1814 | 0 | — |
| `docs/decisions/P1-2026-11-06-LOG.json` | 5039 | 0 | — |
| `docs/decisions/P1-2026-11-06-MTIME-round2.json` | 4515 | 0 | — |
| `docs/decisions/P1-2026-11-06-MTIME.json` | 5603 | 0 | — |
| `docs/decisions/P1-2026-11-06-NBFIX.json` | 3982 | 0 | — |
| `docs/decisions/P1-2026-11-06-RULEFIX.json` | 3839 | 0 | — |
| `docs/decisions/P1-2026-11-06-SAMPLING.json` | 6284 | 0 | — |
| `docs/decisions/P1-2026-11-06-STRENGTH-round1.json` | 2672 | 0 | — |
| `docs/decisions/P1-2026-11-06-STRENGTH-round2.json` | 2583 | 0 | — |
| `docs/decisions/P1-2026-11-06-STRENGTH-round3.json` | 2248 | 0 | — |
| `docs/decisions/P1-2026-11-06-TRIGGER-round2.json` | 3180 | 0 | — |
| `docs/decisions/P1-2026-11-06-TRIGGER-round3.json` | 2538 | 0 | — |
| `docs/decisions/P1-2026-11-06-TRIGGER.json` | 2828 | 0 | — |
| `docs/decisions/P1-2026-11-06-TUNNEL-round2.json` | 5671 | 3 | user-abs-path×1, tun-subnet×1, lan-ip×1 |
| `docs/decisions/P1-2026-11-06-TUNNEL-round3.json` | 5368 | 0 | — |
| `docs/decisions/P1-2026-11-06-TUNNEL-round4.json` | 3906 | 2 | tun-subnet×1, lan-ip×1 |
| `docs/decisions/P1-2026-11-06-TUNNEL.json` | 3427 | 5 | tun-subnet×1, lan-ip×2, local-wan-ip×2 |
| `docs/decisions/P1-2026-11-06-V12.json` | 3413 | 0 | — |
| `docs/decisions/P1-2026-11-06-WARN.json` | 4322 | 0 | — |
| `docs/decisions/P2-2026-11-06-SELFCHECK.json` | 3935 | 0 | — |
| `docs/decisions/REVIEW-SNAPSHOT-2026-11-13-round1.json` | 3112 | 1 | user-abs-path×1 |
| `docs/decisions/SANDBOX-VERDICT-2026-11-10-round1.json` | 7239 | 0 | — |
| `docs/decisions/SANDBOX-VERDICT-2026-11-10-round2.json` | 5705 | 0 | — |
| `tasks/.../fake-codex\egress-ready.ps1` | 952 | 5 | proxy-port×4, private-script-path×1 |

## 脱敏引发的代码修正（后处理）

| 文件 | 为什么要改 |
|---|---|
| `engine/agents-budget.py` | 注释里的示例路径写成了可被 CI 脱敏闸门误命中、又易被读成真用户名的形式 |
| `engine/fire-chief.py` | 占位符落进了可执行代码：会拼出 http://127.0.0.1:<PROXY-PORT-TUNNEL> 这种非法 URL |
| `engine/fire-chief.py` | 同一处 docstring 仍描述旧的写死行为，与修正后的代码不一致 |

## 未公开文件及原因

| 文件 | 原因 |
|---|---|
| `AGENTS.md` | 项目层工作手册，含本人工作状态、订阅额度口径与私有环境排障，改写为 docs/ 下的通用文档 |
| `CLAUDE-SETUP.md` | VPS 与代理隧道搭建指南，属于私有运维，且已被更后的通道方案取代 |
| `SECRETS-AND-LOGIN.md` | 凭据方案 + VPS 接入细节，整体属于私有运维文档，与开源主题无关 |
| `SUBSCRIBE-GUIDE.md` | 订阅/计费方案与账号细节，属于私人运营信息 |
| `start-claude-browser.ps1` | 绑定私有隧道与本机路径的启动脚本 |

## 公开的裁决记录

共 116 份机器裁决 JSON（`decisions/*.json`）。
未包含：`*-raw.txt` 验收官原始输出（含完整会话内容）、累积台账 jsonl。
