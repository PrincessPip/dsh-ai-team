<#
manager.ps1 —— DAG 编排 Step 1 · 经理层入口（薄壳）

它只做三件事，别往里加逻辑：
  1. 跑验收前置自检 `claude-egress-check.ps1`，**不是 READY 就直接停**（提前失败，体验好一点）；
  2. 把参数原样转给 `manager.py`（真正的逻辑在那边，用 Python 是因为 PowerShell 会转坏
     `--json-schema` 的 JSON 参数 —— DESIGN §2.2 的 M6，2026-11-06 实测）；
  3. 透传退出码。

⚠️ 2026-11-07 修正（第 1 轮验收 B2）：这一段过去**不是**硬拦 —— 脚本不存在时只打一行黄字就
继续跑 claude，而且有个 `-SkipEgressCheck` 开关。现在：
  - 脚本不存在 → exit 4（不再「跳过检查」）；
  - `-SkipEgressCheck` 已删除，**没有跳过开关**；
  - 真正的硬拦在 `manager.py`（`check_egress()`）：绕过这个 ps1 直接跑 py 也拦得住，
    并且自检结果会写进 `RUN.json` 的 `egress` 字段。

为什么不把逻辑写在 PowerShell 里：
  `claude -p --json-schema <一大坨 JSON>` 经过 PowerShell 的 argv 转义会被转坏。
  subprocess 的 argv 直传不经过 shell，这是唯一稳的路。

用法：
  powershell -NoProfile -File scripts/manager.ps1 -Brief runs/xxx/BRIEF.md
  powershell -NoProfile -File scripts/manager.ps1 -Brief runs/xxx/BRIEF.md -DryRun
  powershell -NoProfile -File scripts/manager.ps1 -Brief ... -Model opus -Repair 1

退出码：跟 manager.py 一致（0 过 Gate / 1 被拒收 / 2 环境问题 / 3 调用失败 / 4 前置自检没过）。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Brief,
    [string]$RunId,
    [string]$Model = 'opus',
    [int]$Repair = 1,
    [int]$TimeoutSec = 600,
    [string]$MaxUsd,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Write-Error '找不到 python'; exit 2 }

$pyArgs = @("scripts/manager.py", $Brief)
if ($RunId)     { $pyArgs += @('--run-id', $RunId) }
if ($Model)     { $pyArgs += @('--model', $Model) }
if ($null -ne $Repair) { $pyArgs += @('--repair', [string]$Repair) }
if ($null -ne $TimeoutSec) { $pyArgs += @('--timeout', [string]$TimeoutSec) }
if ($MaxUsd)    { $pyArgs += @('--max-usd', $MaxUsd) }
if ($DryRun)    { $pyArgs += '--dry-run' }

# --- 前置自检：dry-run 不调 claude 所以不查；其余一律查，**脚本缺失也算没过** ---
if (-not $DryRun) {
    $check = Join-Path $env:USERPROFILE '.dsh\scripts\cdp\claude-egress-check.ps1'
    if (-not (Test-Path $check)) {
        Write-Host "[manager] 前置自检脚本不存在：$check" -ForegroundColor Red
        Write-Host '[manager] 这条不能跳过 —— 它保证 claude 走伪装出口。先把脚本修回来。' -ForegroundColor Yellow
        exit 4
    }
    & $check | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host "[manager] 前置自检没过（退出码 $LASTEXITCODE）—— 不跑 claude。" -ForegroundColor Red
        Write-Host '[manager] 隧道没起就加 -Start 现拉一次：' -ForegroundColor Yellow
        Write-Host '  & "$env:USERPROFILE\.dsh\scripts\cdp\claude-egress-check.ps1" -Start' -ForegroundColor Yellow
        exit 4
    }
}

& $python.Source @pyArgs
exit $LASTEXITCODE
