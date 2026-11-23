<#
  scripts/ledger-check.ps1 -- 账本行格式检查器（规格 v2 / ledger-format-v2）

  判定顺序（逐行状态机；行号从 1 开始，按 LF 切物理行）：
    S0 空行：整行匹配 ^\s*$ -> blank++；openRow=false；prevForm=BLANK；跳过。
    S1 标题（^#(?:\s|$)）：
         phase=DATA -> HEADER_AFTER_DATA；
         否则 行号!=1 或已经出现过标题 -> HEADER_TITLE_MISPLACED；
         否则 header.title++、seenTitle=true。
         openRow=false；prevForm=HEADER；跳过。
    S2 引用头（^>(?:\s|$)）：
         phase=DATA -> HEADER_AFTER_DATA；
         否则 已经出现过分隔线 -> HEADER_ORDER；
         否则 header.quote++。
         openRow=false；prevForm=HEADER；跳过。
    S3 分隔线（^\s*---\s*$）：
         phase=DATA -> HEADER_AFTER_DATA；
         否则 已经出现过分隔线 -> HEADER_RULE_DUP；
         否则 header.rule++、seenRule=true。
         openRow=false；prevForm=HEADER；跳过。
    S4 日期候选（^\d{4}[-/]\d{1,2}[-/]\d{1,2}）：
         不匹配数据行前缀 '^\d{4}-\d{2}-\d{2} \| '
           -> DATE_ROW_MALFORMED；openRow=false；prevForm=VIOLATION；跳过。
         否则进入 S5。
    S5 数据行：$f = $line -split ' \| ', 4（最多 3 刀，第 4 字段收余下全文）
         $f.Count -ne 4                            -> ROW_FIELD_COUNT
         $f[0] 不是 ^\d{4}-\d{2}-\d{2}$ 或日历非法 -> ROW_DATE_INVALID
         $f[1].Trim() 为空                         -> ROW_FILES_EMPTY
         $f[3].Trim() 为空                         -> ROW_FIELD4_EMPTY
         $t = $f[2].Trim() -replace '^\*{1,2}',''
         $t -match R_DISP                          -> row.disposition++
         否则 $t -match R_TIER                     -> row.tier++
         否则                                      -> FIELD3_INVALID
         全部通过：phase=DATA；openRow=true；prevForm=DATA；跳过。
         任一步失败：openRow=false；prevForm=VIOLATION；跳过。
    S6 其余行（非空、非头、非日期候选）：
         openRow=false -> ORPHAN_CONTINUATION；prevForm=VIOLATION；跳过。
         否则 row.continuation++；prevForm!=CONT 时 continuation.blocks++；prevForm=CONT。
    S7 文件结束：不要求标题/分隔线存在，不要求每条数据行有续行，不要求续行内容。

  正则常量：R_BLANK=^\s*$  R_TITLE=^#(?:\s|$)  R_QUOTE=^>(?:\s|$)
             R_RULE=^\s*---\s*$  R_DATE_CAND=^\d{4}[-/]\d{1,2}[-/]\d{1,2}
             R_DATE_ROW='^\d{4}-\d{2}-\d{2} \| '
             R_DATE_FIELD=^\d{4}-\d{2}-\d{2}$
             R_DISP=^(再?更正|收口|救火|返工后送|补登)
             R_TIER=^(免检|留存|送审)

  读取：UTF-8 严格解码。BOM（EF BB BF）剥掉并打 [warn] file_bom；行尾 \r 去掉并打
        [warn] file_crlf；按 LF 切分，若末尾元素为空且原文以 LF 结尾则删掉该元素。
        文件不存在/打不开/不是合法 UTF-8 -> [error] ledger ...，退出码 2。

  -Baseline（可选）：UTF-8 无 BOM 的 JSON：
        {"schema":"ledger-format-v2-baseline",
         "entries":[{"code":"<第 4 节代码>","line_sha256":"<64 位小写 hex>","count":1}]}
        以 (code,line_sha256) 为键比较多重集：baseline 覆盖的条数打 [KNOWN]，
        多出来的打 [VIOLATION]（对应规格里的 [NEW]），baseline 有而本次没有的打 [FIXED]。
        退出码只看 [VIOLATION] 的条数。schema 不符 / entries 非数组 / code 未知 /
        line_sha256 不是 64 位小写 hex / count<=0 -> [error] baseline ...，退出码 2。
        -WriteBaseline <path> 另存当前违规多重集（同一 schema），不改变判定与退出码。

  汇总：RESULT=OK n=<检查过的物理行数>（无新增违规）或 RESULT=VIOLATIONS n=<新增违规数>；
        退出码 0（无新增）/ 1（有新增）/ 2（账本或 baseline 读取、校验失败）。
#>
param(
    [string]$Path = 'decisions/self/ledger.md',
    [string]$Baseline = '',
    [string]$WriteBaseline = ''
)

$script:ViolationCodes = @(
    'HEADER_TITLE_MISPLACED',
    'HEADER_AFTER_DATA',
    'HEADER_ORDER',
    'HEADER_RULE_DUP',
    'DATE_ROW_MALFORMED',
    'ROW_FIELD_COUNT',
    'ROW_DATE_INVALID',
    'ROW_FILES_EMPTY',
    'ROW_FIELD4_EMPTY',
    'FIELD3_INVALID',
    'ORPHAN_CONTINUATION'
)

function Write-LedgerError {
    param([string]$Message)
    Write-Output ('[error] ' + $Message)
}

function Get-LineSha256 {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        $sb = New-Object System.Text.StringBuilder
        foreach ($b in $hash) {
            [void]$sb.Append($b.ToString('x2'))
        }
        return $sb.ToString()
    }
    finally {
        $sha.Dispose()
    }
}

function Add-Violation {
    param(
        [string]$Code,
        [int]$LineNumber,
        [string]$Text,
        [System.Collections.ArrayList]$List
    )
    $sha = Get-LineSha256 $Text
    $item = [pscustomobject]@{
        Code = $Code
        Line = $LineNumber
        Sha = $sha
        Text = $Text
        Key = ($Code + '|' + $sha)
    }
    [void]$List.Add($item)
}

function Get-Excerpt {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    if ($Text.Length -gt 120) { return $Text.Substring(0, 120) }
    return $Text
}

function Write-BaselineFile {
    param(
        [string]$FilePath,
        [System.Collections.ArrayList]$KeyOrder,
        [hashtable]$Map
    )
    $entries = New-Object System.Collections.ArrayList
    foreach ($key in ($KeyOrder | Sort-Object)) {
        $entry = $Map[$key]
        $obj = [pscustomobject]@{
            code = $entry.Code
            line_sha256 = $entry.Sha
            count = $entry.Items.Count
        }
        [void]$entries.Add($obj)
    }
    $doc = [pscustomobject]@{
        schema = 'ledger-format-v2-baseline'
        entries = @($entries)
    }
    $json = $doc | ConvertTo-Json -Depth 5
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($FilePath, $json, $enc)
}

# ---- 读取（严格 UTF-8，剥 BOM，去行尾 CR，按 LF 切分） ----
$bytes = $null
try {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
}
catch {
    Write-LedgerError ('ledger cannot be read: ' + $Path)
    exit 2
}

$hasBom = $false
$offset = 0
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $hasBom = $true
    $offset = 3
}

$strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
try {
    $raw = $strictUtf8.GetString($bytes, $offset, $bytes.Length - $offset)
}
catch {
    Write-LedgerError ('ledger is not valid UTF-8: ' + $Path)
    exit 2
}

$rawLines = $raw -split "`n"
if ($rawLines.Count -gt 0 -and $rawLines[$rawLines.Count - 1] -eq '' -and $raw.EndsWith("`n")) {
    if ($rawLines.Count -eq 1) {
        $rawLines = @()
    }
    else {
        $rawLines = $rawLines[0..($rawLines.Count - 2)]
    }
}

$lines = New-Object System.Collections.ArrayList
$sawCrlf = $false
foreach ($rawLine in $rawLines) {
    $line = [string]$rawLine
    if ($line.EndsWith("`r")) {
        $line = $line.Substring(0, $line.Length - 1)
        $sawCrlf = $true
    }
    [void]$lines.Add($line)
}

if ($hasBom) { Write-Output '[warn] file_bom' }
if ($sawCrlf) { Write-Output '[warn] file_crlf' }

# ---- 逐行状态机 ----
$totalLines = $lines.Count
$blank = 0
$titleCount = 0
$quoteCount = 0
$ruleCount = 0
$rowTier = 0
$rowDisposition = 0
$rowContinuation = 0
$contBlocks = 0
$violations = New-Object System.Collections.ArrayList

$phase = 'PREAMBLE'
$openRow = $false
$seenTitle = $false
$seenRule = $false
$prevForm = 'NONE'

for ($idx = 0; $idx -lt $lines.Count; $idx++) {
    $line = [string]$lines[$idx]
    $lineNo = $idx + 1

    # S0 空行
    if ($line -match '^\s*$') {
        $blank++
        $openRow = $false
        $prevForm = 'BLANK'
        continue
    }

    # S1 标题
    if ($line -match '^#(?:\s|$)') {
        if ($phase -eq 'DATA') {
            Add-Violation -Code 'HEADER_AFTER_DATA' -LineNumber $lineNo -Text $line -List $violations
        }
        elseif ($lineNo -ne 1 -or $seenTitle) {
            Add-Violation -Code 'HEADER_TITLE_MISPLACED' -LineNumber $lineNo -Text $line -List $violations
        }
        else {
            $titleCount++
            $seenTitle = $true
        }
        $openRow = $false
        $prevForm = 'HEADER'
        continue
    }

    # S2 引用头
    if ($line -match '^>(?:\s|$)') {
        if ($phase -eq 'DATA') {
            Add-Violation -Code 'HEADER_AFTER_DATA' -LineNumber $lineNo -Text $line -List $violations
        }
        elseif ($seenRule) {
            Add-Violation -Code 'HEADER_ORDER' -LineNumber $lineNo -Text $line -List $violations
        }
        else {
            $quoteCount++
        }
        $openRow = $false
        $prevForm = 'HEADER'
        continue
    }

    # S3 分隔线
    if ($line -match '^\s*---\s*$') {
        if ($phase -eq 'DATA') {
            Add-Violation -Code 'HEADER_AFTER_DATA' -LineNumber $lineNo -Text $line -List $violations
        }
        elseif ($seenRule) {
            Add-Violation -Code 'HEADER_RULE_DUP' -LineNumber $lineNo -Text $line -List $violations
        }
        else {
            $ruleCount++
            $seenRule = $true
        }
        $openRow = $false
        $prevForm = 'HEADER'
        continue
    }

    # S4 日期候选 / S5 数据行
    if ($line -match '^\d{4}[-/]\d{1,2}[-/]\d{1,2}') {
        if ($line -notmatch '^\d{4}-\d{2}-\d{2} \| ') {
            Add-Violation -Code 'DATE_ROW_MALFORMED' -LineNumber $lineNo -Text $line -List $violations
            $openRow = $false
            $prevForm = 'VIOLATION'
            continue
        }

        $rowOk = $false
        $f = @($line -split ' \| ', 4)

        if ($f.Count -ne 4) {
            Add-Violation -Code 'ROW_FIELD_COUNT' -LineNumber $lineNo -Text $line -List $violations
        }
        else {
            $dt = [datetime]::MinValue
            $dateOk = $false
            if ($f[0] -match '^\d{4}-\d{2}-\d{2}$') {
                $dateOk = [datetime]::TryParseExact(
                    $f[0],
                    'yyyy-MM-dd',
                    [System.Globalization.CultureInfo]::InvariantCulture,
                    [System.Globalization.DateTimeStyles]::None,
                    [ref]$dt
                )
            }

            if (-not $dateOk) {
                Add-Violation -Code 'ROW_DATE_INVALID' -LineNumber $lineNo -Text $line -List $violations
            }
            elseif ([string]::IsNullOrEmpty($f[1].Trim())) {
                Add-Violation -Code 'ROW_FILES_EMPTY' -LineNumber $lineNo -Text $line -List $violations
            }
            elseif ([string]::IsNullOrEmpty($f[3].Trim())) {
                Add-Violation -Code 'ROW_FIELD4_EMPTY' -LineNumber $lineNo -Text $line -List $violations
            }
            else {
                $t = $f[2].Trim() -replace '^\*{1,2}', ''
                if ($t -match '^(再?更正|收口|救火|返工后送|补登)') {
                    $rowDisposition++
                    $rowOk = $true
                }
                elseif ($t -match '^(免检|留存|送审)') {
                    $rowTier++
                    $rowOk = $true
                }
                else {
                    Add-Violation -Code 'FIELD3_INVALID' -LineNumber $lineNo -Text $line -List $violations
                }
            }
        }

        if ($rowOk) {
            $phase = 'DATA'
            $openRow = $true
            $prevForm = 'DATA'
        }
        else {
            $openRow = $false
            $prevForm = 'VIOLATION'
        }
        continue
    }

    # S6 其余行：续行或孤儿
    if (-not $openRow) {
        Add-Violation -Code 'ORPHAN_CONTINUATION' -LineNumber $lineNo -Text $line -List $violations
        $prevForm = 'VIOLATION'
    }
    else {
        $rowContinuation++
        if ($prevForm -ne 'CONT') {
            $contBlocks++
        }
        $prevForm = 'CONT'
    }
}

# ---- 违规多重集 V：键 (code, line_sha256) ----
$vMap = @{}
$vOrder = New-Object System.Collections.ArrayList
foreach ($vio in $violations) {
    if (-not $vMap.ContainsKey($vio.Key)) {
        $vMap[$vio.Key] = [pscustomobject]@{
            Code = $vio.Code
            Sha = $vio.Sha
            Items = (New-Object System.Collections.ArrayList)
        }
        [void]$vOrder.Add($vio.Key)
    }
    [void]$vMap[$vio.Key].Items.Add($vio)
}

# ---- baseline B ----
$bMap = @{}
$bOrder = New-Object System.Collections.ArrayList
if (-not [string]::IsNullOrEmpty($Baseline)) {
    if (-not [System.IO.File]::Exists($Baseline)) {
        Write-LedgerError ('baseline not found: ' + $Baseline)
        exit 2
    }
    try {
        $bRaw = [System.IO.File]::ReadAllText($Baseline, (New-Object System.Text.UTF8Encoding($false, $true)))
    }
    catch {
        Write-LedgerError ('baseline cannot be read as UTF-8: ' + $Baseline)
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($bRaw)) {
        Write-LedgerError ('baseline is empty: ' + $Baseline)
        exit 2
    }
    try {
        $bObj = ConvertFrom-Json -InputObject $bRaw
    }
    catch {
        Write-LedgerError ('baseline is not valid JSON: ' + $Baseline)
        exit 2
    }

    $schemaValue = $null
    if ($null -ne $bObj) {
        $schemaProp = $bObj.PSObject.Properties['schema']
        if ($null -ne $schemaProp) {
            $schemaValue = [string]$schemaProp.Value
        }
    }
    if ($schemaValue -cne 'ledger-format-v2-baseline') {
        Write-LedgerError ('baseline schema mismatch: ' + $Baseline)
        exit 2
    }

    $entriesValue = $null
    if ($null -ne $bObj) {
        $entriesProp = $bObj.PSObject.Properties['entries']
        if ($null -ne $entriesProp) {
            $entriesValue = $entriesProp.Value
        }
    }
    if ($null -eq $entriesValue -or -not ($entriesValue -is [System.Array])) {
        Write-LedgerError ('baseline entries must be an array: ' + $Baseline)
        exit 2
    }

    foreach ($entry in $entriesValue) {
        if ($null -eq $entry -or -not ($entry -is [System.Management.Automation.PSCustomObject])) {
            Write-LedgerError ('baseline entry is not an object: ' + $Baseline)
            exit 2
        }
        $codeProp = $entry.PSObject.Properties['code']
        $shaProp = $entry.PSObject.Properties['line_sha256']
        if ($null -eq $codeProp -or $null -eq $shaProp) {
            Write-LedgerError ('baseline entry missing code or line_sha256: ' + $Baseline)
            exit 2
        }
        $code = [string]$codeProp.Value
        $sha = [string]$shaProp.Value

        if (-not ($script:ViolationCodes -ccontains $code)) {
            Write-LedgerError ('baseline has unknown code: ' + $code)
            exit 2
        }
        if ($sha -cnotmatch '^[0-9a-f]{64}$') {
            Write-LedgerError ('baseline line_sha256 must be 64 lowercase hex: ' + $code)
            exit 2
        }

        $cnt = [long]1
        $countProp = $entry.PSObject.Properties['count']
        if ($null -ne $countProp) {
            $cv = $countProp.Value
            $okNum = $false
            $num = 0.0
            if ($null -ne $cv -and -not ($cv -is [string]) -and -not ($cv -is [bool]) -and -not ($cv -is [System.Management.Automation.PSCustomObject])) {
                try {
                    $num = [double]$cv
                    $okNum = $true
                }
                catch {
                    $okNum = $false
                }
            }
            if (-not $okNum -or $num -le 0 -or $num -ne [math]::Floor($num)) {
                Write-LedgerError ('baseline count must be a positive integer: ' + $code)
                exit 2
            }
            $cnt = [long]$num
        }

        $key = $code + '|' + $sha
        if ($bMap.ContainsKey($key)) {
            $bMap[$key].Count = $bMap[$key].Count + $cnt
        }
        else {
            $bMap[$key] = [pscustomobject]@{
                Code = $code
                Sha = $sha
                Count = $cnt
            }
            [void]$bOrder.Add($key)
        }
    }
}

# ---- 比对并输出 ----
$knownCount = 0
$newCount = 0
$fixedCount = 0

foreach ($key in $vOrder) {
    $vEntry = $vMap[$key]
    $vCount = $vEntry.Items.Count
    $bCount = [long]0
    if ($bMap.ContainsKey($key)) {
        $bCount = [long]$bMap[$key].Count
    }
    $knownHere = [long][math]::Min([long]$vCount, $bCount)

    for ($k = 0; $k -lt $vCount; $k++) {
        $item = $vEntry.Items[$k]
        if ($k -lt $knownHere) {
            $prefix = '[KNOWN]'
            $knownCount++
        }
        else {
            $prefix = '[VIOLATION]'
            $newCount++
        }
        Write-Output ($prefix + ' code=' + $item.Code + ' line=' + $item.Line + ' sha256=' + $item.Sha + ' text=' + (Get-Excerpt $item.Text))
    }
}

foreach ($key in $bOrder) {
    $bEntry = $bMap[$key]
    $vCount = 0
    if ($vMap.ContainsKey($key)) {
        $vCount = $vMap[$key].Items.Count
    }
    if ($bEntry.Count -gt $vCount) {
        $fixedCount += ($bEntry.Count - $vCount)
        Write-Output ('[FIXED] code=' + $bEntry.Code + ' sha256=' + $bEntry.Sha + ' count=' + ($bEntry.Count - $vCount))
    }
}

# ---- 可选：把当前 V 写成 baseline ----
if (-not [string]::IsNullOrEmpty($WriteBaseline)) {
    try {
        Write-BaselineFile -FilePath $WriteBaseline -KeyOrder $vOrder -Map $vMap
    }
    catch {
        Write-LedgerError ('baseline cannot be written: ' + $WriteBaseline)
        exit 2
    }
}

$rowData = $rowTier + $rowDisposition
Write-Output ('[summary] lines=' + $totalLines + ' blank=' + $blank + ' header.title=' + $titleCount + ' header.quote=' + $quoteCount + ' header.rule=' + $ruleCount + ' row.tier=' + $rowTier + ' row.disposition=' + $rowDisposition + ' row.data=' + $rowData + ' row.continuation=' + $rowContinuation + ' continuation.blocks=' + $contBlocks + ' violations=' + $violations.Count + ' new=' + $newCount + ' known=' + $knownCount + ' fixed=' + $fixedCount)

if ($newCount -gt 0) {
    Write-Output ('RESULT=VIOLATIONS n=' + $newCount)
    exit 1
}
Write-Output ('RESULT=OK n=' + $totalLines)
exit 0
