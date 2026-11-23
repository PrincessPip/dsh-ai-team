<#
  scripts/ledger-check-test.ps1 -- ledger-check.ps1 的断言台（规格 v2 / ledger-format-v2）

  用法（外部验收命令）：
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ledger-check-test.ps1

  约定：
  - 每个 fixture 建在 $env:TEMP\ledger-check-test-<32hex>\ 下，用例结束即删；
    只往该临时目录写文件，工程目录不落任何临时文件。
  - PASS/FAIL 逐例打印；最后一行 TESTS=<n> FAILED=<m>；exit 0 仅当 FAILED=0。

  覆盖矩阵（用例名 -> 规格条目）：
  - accept-all-forms-...                 -> S0 空行 / S1 标题 / S2 引用头 / S3 分隔线 /
                                            S5 row.tier + row.disposition / S6 续行 + continuation.blocks
  - accept-field4-contains-...           -> 硬边界 2：-split ' \| ', 4（正则），第 4 段允许含裸 ' | '
  - accept-header-forms-optional-...     -> S7：不要求标题/分隔线存在
  - accept-empty-file-...                -> S0：空文件 = 1 条空行
  - read-bom-and-crlf-...                -> 第 0 节：BOM/CRLF 只警告、不判违规
  - default-path-...                     -> -Path 默认值 decisions/self/ledger.md
  - orphan-continuation-...（2 例）      -> S6 / 第 5 节：孤儿续行算违规
  - violation-bad-date-...               -> S4 DATE_ROW_MALFORMED
  - violation-calendar-date-...          -> S5 ROW_DATE_INVALID
  - violation-field-count-...            -> S5 ROW_FIELD_COUNT
  - violation-tier-word-...              -> S5 FIELD3_INVALID
  - violation-tier-negation-...          -> R_TIER **段首锚定**（否定词开头的行不匹配；后顾已按 X1 删除）
  - violation-checklist-missing-...      -> S5 ROW_FIELD4_EMPTY（规格 v2 的「清单号缺失」= 第 4 段为空）
  - violation-files-empty-...            -> S5 ROW_FILES_EMPTY
  - violation-header-title-misplaced-... -> S1 HEADER_TITLE_MISPLACED
  - violation-header-after-data-...      -> S1/S2/S3 HEADER_AFTER_DATA
  - violation-header-order-...           -> S2 HEADER_ORDER
  - violation-header-rule-dup-...        -> S3 HEADER_RULE_DUP
  - baseline-known-violation-not-new     -> 第 6 节：[KNOWN] 不计新增，exit 0
  - baseline-new-violation-still-red     -> 第 6 节：已知不红 + 新增仍红，exit 1
  - baseline-fixed-entry-not-new         -> 第 6 节：[FIXED]
  - baseline-error-schema-mismatch-...   -> 第 6/7 节：schema 不符 exit 2
  - file-not-found-...                   -> 第 0/7 节：文件不存在 exit 2
  - invalid-utf8-...                     -> 第 0/7 节：非法 UTF-8 exit 2
#>

$ErrorActionPreference = 'Stop'

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:Utf8Bom = New-Object System.Text.UTF8Encoding($true)

$checker = Join-Path $PSScriptRoot 'ledger-check.ps1'
$engine = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $engine)) {
    $winPs = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $winPs) {
        $engine = $winPs
    }
    else {
        $engine = (Get-Process -Id $PID).Path
    }
}

function Get-LineSha256 {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($script:Utf8NoBom.GetBytes($Text))
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally {
        $sha.Dispose()
    }
}

function Write-BaselineFile {
    param(
        [string]$Path,
        [object[]]$Entries
    )
    $doc = [pscustomobject]@{
        schema = 'ledger-format-v2-baseline'
        entries = @($Entries)
    }
    $json = $doc | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($Path, $json, $script:Utf8NoBom)
}

function Test-CaseResult {
    param(
        [object]$Case,
        [object[]]$Actual,
        [int]$Code
    )
    if ($Code -ne [int]$Case.Code) {
        return ('expected exit ' + $Case.Code + ', got ' + $Code + '. Output: ' + ($Actual -join ' | '))
    }
    $joined = $Actual -join "`n"
    foreach ($needle in @($Case.Expect)) {
        if ($null -ne $needle -and -not $joined.Contains([string]$needle)) {
            return ('missing expected text: ' + $needle + '. Output: ' + ($Actual -join ' | '))
        }
    }
    foreach ($needle in @($Case.Reject)) {
        if ($null -ne $needle -and $joined.Contains([string]$needle)) {
            return ('unexpected text present: ' + $needle + '. Output: ' + ($Actual -join ' | '))
        }
    }
    return $null
}

$lineBadDate = '2026/11/10 | f | 留存 | #2'
$lineTierBad = '2026-11-10 | f | unknown | #2'
$shaBadDate = Get-LineSha256 $lineBadDate
$staleSha = ('a' * 64)

$cases = @(
    @{
        Name = 'accept-all-forms-blank-title-quote-rule-tier-disposition-continuation'
        Text = (@(
            '# 交付账本',
            '> 规则',
            '> 格式',
            '---',
            '',
            '2026-11-10 | scripts/a.ps1 | **留存** | **#2** did a thing.',
            'continuation line one',
            'continuation line two',
            '',
            '2026-11-11 | scripts/b.ps1 | 免检 | 无',
            '2026-11-12 | scripts/c.ps1 | 送审 | #4 #5 #6',
            '2026-11-13 | scripts/d.ps1 | **更正第 3 个交付单元** | 更正说明',
            '2026-11-14 | scripts/e.ps1 | 再更正 | more',
            '2026-11-15 | scripts/f.ps1 | 收口 | done',
            '2026-11-16 | scripts/g.ps1 | 救火 | fire',
            '2026-11-17 | scripts/h.ps1 | 返工后送 | rework'
        ) -join "`n") + "`n"
        Code = 0
        Expect = @(
            'RESULT=OK n=16',
            '[summary] lines=16 blank=2 header.title=1 header.quote=2 header.rule=1 row.tier=3 row.disposition=5 row.data=8 row.continuation=2 continuation.blocks=1 violations=0 new=0 known=0 fixed=0'
        )
        Reject = @('[VIOLATION]', '[error]')
    },
    @{
        Name = 'accept-field4-contains-literal-separator-split-limit-4'
        Text = '2026-11-10 | f | 留存 | 引用了格式行 `日期 | scripts/x.ps1 · PROGRESS.md` 后面还有正文' + "`n"
        Code = 0
        Expect = @(
            'RESULT=OK n=1',
            'row.data=1',
            'violations=0'
        )
        Reject = @('[VIOLATION]', 'ROW_FIELD_COUNT')
    },
    @{
        Name = 'accept-header-forms-optional-no-title-or-rule'
        Text = "2026-11-10 | f | 留存 | #2`n2026-11-11 | g | 送审 | #4`n"
        Code = 0
        Expect = @(
            'RESULT=OK n=2',
            'header.title=0 header.quote=0 header.rule=0 row.tier=2 row.disposition=0 row.data=2'
        )
        Reject = @('[VIOLATION]')
    },
    @{
        Name = 'accept-empty-file-counts-one-blank-line'
        Text = ''
        Code = 0
        Expect = @(
            'RESULT=OK n=1',
            'blank=1'
        )
        Reject = @('[VIOLATION]')
    },
    @{
        Name = 'read-bom-and-crlf-warn-only-not-violation'
        Text = "2026-11-10 | f | 留存 | #2`n"
        Bom = $true
        Crlf = $true
        Code = 0
        Expect = @(
            '[warn] file_bom',
            '[warn] file_crlf',
            'RESULT=OK n=1',
            'row.tier=1',
            'violations=0'
        )
        Reject = @('[VIOLATION]', '[error]')
    },
    @{
        Name = 'default-path-decisions-self-ledger'
        Text = "2026-11-10 | f | 留存 | #2`n"
        DefaultPath = $true
        Code = 0
        Expect = @('RESULT=OK n=1')
        Reject = @('[VIOLATION]', '[error]')
    },
    @{
        Name = 'orphan-continuation-before-any-data-row'
        Text = "这是没有前驱的续行`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ORPHAN_CONTINUATION line=1',
            'row.continuation=0',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'orphan-continuation-after-blank-line'
        Text = "2026-11-10 | f | 留存 | #2`n`n这是空行后的孤儿`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ORPHAN_CONTINUATION line=3',
            'row.continuation=0',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-bad-date-DATE_ROW_MALFORMED'
        Text = $lineBadDate + "`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=DATE_ROW_MALFORMED line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-calendar-date-ROW_DATE_INVALID'
        Text = "2026-02-30 | f | 留存 | #2`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ROW_DATE_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-field-count-ROW_FIELD_COUNT'
        Text = "2026-11-10 | f | 留存`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ROW_FIELD_COUNT line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-tier-word-FIELD3_INVALID'
        Text = $lineTierBad + "`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=FIELD3_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-tier-negation-prefix-FIELD3_INVALID'
        Text = "2026-11-10 | f | 不留存 | #2`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=FIELD3_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'accept-disposition-budeng-marker'
        Text = "2026-11-11 | f | 补登 · 漏记的单元 | 无`n"
        Code = 0
        Expect = @(
            'RESULT=OK n=1',
            'row.disposition=1'
        )
    },
    @{
        Name = 'violation-tier-missing-prefix-FIELD3_INVALID'
        Text = "2026-11-11 | f | 没送审 | 无`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=FIELD3_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-tier-not-send-but-hold-FIELD3_INVALID'
        Text = "2026-11-11 | f | 不送审（但按留存记） | 无`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=FIELD3_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-not-send-with-correction-FIELD3_INVALID'
        Text = "2026-11-11 | f | 不送审（按更正行处置） | 无`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=FIELD3_INVALID line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'accept-tier-word-at-start-stripped-stars'
        Text = "2026-11-11 | f | **留存**（从 #2 降级） | 无`n"
        Code = 0
        Expect = @(
            'RESULT=OK n=1',
            'row.tier=1'
        )
    },
    @{
        Name = 'violation-checklist-missing-ROW_FIELD4_EMPTY'
        Text = '2026-11-10 | f | 留存 |   ' + "`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ROW_FIELD4_EMPTY line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-files-empty-ROW_FILES_EMPTY'
        Text = "2026-11-10 |  | 留存 | #2`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=ROW_FILES_EMPTY line=1',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-header-title-misplaced-HEADER_TITLE_MISPLACED'
        Text = "`n# t`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=HEADER_TITLE_MISPLACED line=2',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-header-after-data-HEADER_AFTER_DATA'
        Text = "# t`n2026-11-10 | f | 留存 | #2`n> q`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=HEADER_AFTER_DATA line=3',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-header-order-HEADER_ORDER'
        Text = "# t`n---`n> q`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=HEADER_ORDER line=3',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'violation-header-rule-dup-HEADER_RULE_DUP'
        Text = "# t`n---`n---`n"
        Code = 1
        Expect = @(
            '[VIOLATION] code=HEADER_RULE_DUP line=3',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'baseline-known-violation-not-new'
        Text = $lineBadDate + "`n"
        BaselineEntries = @(
            [pscustomobject]@{ code = 'DATE_ROW_MALFORMED'; line_sha256 = $shaBadDate; count = 1 }
        )
        Code = 0
        Expect = @(
            '[KNOWN] code=DATE_ROW_MALFORMED line=1',
            'violations=1 new=0 known=1 fixed=0',
            'RESULT=OK n=1'
        )
        Reject = @('[VIOLATION]')
    },
    @{
        Name = 'baseline-new-violation-still-red'
        Text = $lineBadDate + "`n" + $lineTierBad + "`n"
        BaselineEntries = @(
            [pscustomobject]@{ code = 'DATE_ROW_MALFORMED'; line_sha256 = $shaBadDate; count = 1 }
        )
        Code = 1
        Expect = @(
            '[KNOWN] code=DATE_ROW_MALFORMED line=1',
            '[VIOLATION] code=FIELD3_INVALID line=2',
            'violations=2 new=1 known=1 fixed=0',
            'RESULT=VIOLATIONS n=1'
        )
    },
    @{
        Name = 'baseline-fixed-entry-not-new'
        Text = "2026-11-10 | f | 留存 | #2`n"
        BaselineEntries = @(
            [pscustomobject]@{ code = 'ROW_FIELD_COUNT'; line_sha256 = $staleSha; count = 2 }
        )
        Code = 0
        Expect = @(
            ('[FIXED] code=ROW_FIELD_COUNT sha256=' + $staleSha + ' count=2'),
            'fixed=2',
            'RESULT=OK n=1'
        )
        Reject = @('[VIOLATION]')
    },
    @{
        Name = 'baseline-error-schema-mismatch-exit-2'
        Text = "2026-11-10 | f | 留存 | #2`n"
        BaselineRaw = '{"schema":"ledger-format-v2-baseline-wrong","entries":[]}'
        Code = 2
        Expect = @('[error] baseline schema mismatch')
        Reject = @('[summary]')
    },
    @{
        Name = 'file-not-found-exit-2'
        MissingLedger = $true
        Code = 2
        Expect = @('[error] ledger cannot be read')
        Reject = @('[summary]')
    },
    @{
        Name = 'invalid-utf8-exit-2'
        InvalidUtf8 = $true
        Code = 2
        Expect = @('[error] ledger is not valid UTF-8')
        Reject = @('[summary]')
    }
)

$checkerMissing = -not (Test-Path -LiteralPath $checker)

$failed = 0
foreach ($case in $cases) {
    $fixtureRoot = Join-Path $env:TEMP ('ledger-check-test-' + [guid]::NewGuid().ToString('N'))
    $problem = $null
    $pushed = $false
    try {
        if ($checkerMissing) {
            throw ('checker not found: ' + $checker)
        }
        [void][System.IO.Directory]::CreateDirectory($fixtureRoot)
        $ledgerPath = Join-Path $fixtureRoot 'ledger.md'
        if ($case.DefaultPath) {
            $ledgerPath = Join-Path $fixtureRoot 'decisions/self/ledger.md'
            [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $ledgerPath))
        }

        if ($case.MissingLedger) {
            # 故意不创建账本：验证文件不存在分支。
        }
        elseif ($case.InvalidUtf8) {
            [System.IO.File]::WriteAllBytes($ledgerPath, [byte[]](0xC3, 0x28, 0x0A))
        }
        else {
            $text = [string]$case.Text
            if ($case.Crlf) {
                $text = $text -replace "`n", "`r`n"
            }
            if ($case.Bom) {
                [System.IO.File]::WriteAllText($ledgerPath, $text, $script:Utf8Bom)
            }
            else {
                [System.IO.File]::WriteAllText($ledgerPath, $text, $script:Utf8NoBom)
            }
        }

        $argList = @()
        if (-not $case.DefaultPath) {
            $argList += @('-Path', $ledgerPath)
        }
        if ($case.ContainsKey('BaselineEntries')) {
            $baselinePath = Join-Path $fixtureRoot 'baseline.json'
            Write-BaselineFile -Path $baselinePath -Entries @($case.BaselineEntries)
            $argList += @('-Baseline', $baselinePath)
        }
        elseif ($case.ContainsKey('BaselineRaw')) {
            $baselinePath = Join-Path $fixtureRoot 'baseline.json'
            [System.IO.File]::WriteAllText($baselinePath, [string]$case.BaselineRaw, $script:Utf8NoBom)
            $argList += @('-Baseline', $baselinePath)
        }

        Push-Location -LiteralPath $fixtureRoot
        $pushed = $true
        $actual = @(& $engine -NoProfile -ExecutionPolicy Bypass -File $checker @argList 2>&1)
        $code = $LASTEXITCODE
        $problem = Test-CaseResult -Case $case -Actual $actual -Code $code
    }
    catch {
        $problem = $_.Exception.Message
    }
    finally {
        if ($pushed) {
            Pop-Location
        }
        try {
            # 递归删除前先证实解析出的目录确实在 TEMP 下且是本断言台的命名。
            if (Test-Path -LiteralPath $fixtureRoot) {
                $resolved = (Resolve-Path -LiteralPath $fixtureRoot).ProviderPath
                $tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
                $leaf = [System.IO.Path]::GetFileName($resolved)
                if (-not $resolved.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
                    $leaf -notmatch '^ledger-check-test-[0-9a-f]{32}$') {
                    throw ('Unsafe fixture cleanup path: ' + $resolved)
                }
                Remove-Item -LiteralPath $resolved -Recurse -Force
            }
        }
        catch {
            $problem = ($problem + ' Cleanup failed: ' + $_.Exception.Message)
        }
    }

    if ($null -eq $problem) {
        Write-Output ('PASS ' + $case.Name)
    }
    else {
        $failed++
        Write-Output ('FAIL ' + $case.Name)
        Write-Output ('  ' + $problem)
    }
}

Write-Output ('TESTS=' + $cases.Count + ' FAILED=' + $failed)
if ($failed -gt 0) {
    exit 1
}
exit 0
