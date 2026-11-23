# ledger-audit.ps1 - read-only audit of the delivery ledger
#
# PURPOSE: find files that changed after the ledger was created but have no
#          corresponding entry in decisions/self/ledger.md (work not logged).
# WHY: the ledger is written by the worker, so whole entries can be skipped.
#      File mtimes cannot lie, so cross-check the ledger against them.
#      (Suggested by the reviewer's C6 finding, 2026-11-06; partner approved.)
#
# USAGE: powershell -NoProfile -File scripts/ledger-audit.ps1
# EXIT:  0 = LEDGER_OK | 1 = LEDGER_SUSPECT | 2 = NO_LEDGER
#
# RULES THIS SCRIPT ASSUMES:
#   - Only changes AFTER the ledger file was created are audited. History
#     predating the ledger is never retro-flagged (it cannot be logged).
#   - Ledger line = "date | files | tier | rule#". The files column is split
#     on the middle dot (U+00B7).
#   - A token ending with "/" covers that whole DIRECTORY.
#     A token WITHOUT a trailing "/" covers exactly that one FILE.
#     (v2 fix: the old fuzzy substring match let one logged file bless its
#      entire directory -- a probe file was silently missed.)
#   - A token starting with "~" is resolved against %USERPROFILE% instead of
#     the project root. That is how out-of-project objects are recorded.
#     (v3: matching is done on ABSOLUTE, normalized paths. v3 also dropped the
#      old "$f.Name -eq $token" fallback -- it let same-named files in
#      different directories mask each other, which got much worse once
#      external roots were added.)
#
# EXTERNAL SCOPE (v3):
#   scripts/ledger-external.txt lists extra roots/files to audit outside the
#   project root (directory = recursive, single file = itself). Only
#   *.ps1 *.json *.yml *.yaml *.md *.py *.mjs are audited there too. *.log never is.
#   No list file, or an empty one = no external scope (exactly the v2
#   behaviour). The audit window still applies, so old out-of-project scripts
#   are not retro-flagged.
#
# ASCII-only on purpose: this machine runs Windows PowerShell 5.1, which
# decodes a BOM-less file as code page 936 and would mangle non-ASCII text.
#
# NOTE: heuristic, not proof. It compares ABSOLUTE paths only, so a file that
#       was moved still shows up as new; it cannot see an edit that restored
#       the original mtime; and it never audits the ledger file itself, so
#       rewriting a PAST ledger line is invisible here (2026-11-06: happened
#       once and was only caught because the worker owned up).
#       Changing THIS script hits trigger rule #2 (must be reviewed).
#
# WARN LINES (v3.2):
#   Degrading scope used to be SILENT -- a missing or empty external list
#   printed one "[scope] external = (none)" line and still exited 0. v3.2 keeps
#   the exit-code contract (0/1/2 unchanged) but prints "[warn]" lines for:
#     - external list missing / present but with no usable entry
#     - external list entries that do not exist on disk
#     - a broad DIRECTORY token in the ledger that covers >=3 files
#   "SILENT SCOPE LOSS" is the thing being fixed here, not the exit code.
#   The *.log blind spot is also printed every run as a fixed scope line, so
#   it is visible without being a warning.

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$rootN = $root.Replace('\', '/')
$ledgerPath = Join-Path $root 'decisions\self\ledger.md'
$externalList = Join-Path $root 'scripts\ledger-external.txt'
$externalListN = ([System.IO.Path]::GetFullPath($externalList)).Replace('\', '/')
$userHomeN = ([System.IO.Path]::GetFullPath($env:USERPROFILE)).Replace('\', '/')
$warn = New-Object System.Collections.ArrayList

if (-not (Test-Path -LiteralPath $ledgerPath)) {
    Write-Output "RESULT=NO_LEDGER"
    exit 2
}

# 0. audit window starts when the ledger was born (30 min grace for clock skew)
$ledgerItem = Get-Item -LiteralPath $ledgerPath
$ledgerStart = $ledgerItem.CreationTime.AddMinutes(-30)

# Normalize a ledger token (or a list entry) to an absolute, slash-separated path.
# "~" = %USERPROFILE%, anything else relative = project root.
function Convert-ToAbs([string]$t) {
    $p = $t.Trim()
    if (-not $p) { return $null }
    if ($p.StartsWith('~')) {
        $rest = $p.Substring(1).TrimStart('/', '\')
        if ($rest) { $p = Join-Path $env:USERPROFILE $rest } else { $p = $env:USERPROFILE }
    } elseif (-not [System.IO.Path]::IsPathRooted($p)) {
        $p = Join-Path $root $p
    }
    try { return ([System.IO.Path]::GetFullPath($p)).Replace('\', '/') } catch { return $null }
}

# Short display form: "~/..." for home, project-relative for the project root.
function Convert-ToDisplay([string]$abs) {
    if (-not $abs) { return '?' }
    if ($abs -eq $userHomeN) { return '~' }
    if ($abs.StartsWith($userHomeN + '/')) { return '~/' + $abs.Substring($userHomeN.Length + 1) }
    if ($abs -eq $rootN) { return '.' }
    if ($abs.StartsWith($rootN + '/')) { return $abs.Substring($rootN.Length + 1) }
    return $abs
}

# 1. parse the ledger into absolute file-tokens and dir-tokens
$fileTokens = New-Object System.Collections.ArrayList
$dirTokens = New-Object System.Collections.ArrayList
$dateSet = @{}
$lineCount = 0
$dot = [char]0x00B7

foreach ($line in Get-Content -LiteralPath $ledgerPath -Encoding UTF8) {
    if ($line -notmatch '^\s*(\d{4}-\d{2}-\d{2})\s*\|') { continue }
    $lineCount++
    $dateSet[$Matches[1]] = $true
    $cols = $line -split '\|'
    if ($cols.Count -lt 2) { continue }
    foreach ($tok in ($cols[1] -split $dot)) {
        $t = $tok.Trim()
        if (-not $t) { continue }
        # v3: the files column is sometimes annotated with a trailing note,
        # e.g. a path followed by a full-width parenthesis pair (U+FF08/U+FF09).
        # Strip a TRAILING parenthetical note (ASCII or full-width) before
        # resolving. Only trailing: an inner pair belongs to the path.
        $t = $t -replace '[\uFF08][^\uFF09]*[\uFF09]$', ''
        $t = $t -replace '\([^)]*\)$', ''
        $t = $t.Trim()
        if (-not $t) { continue }
        # Glob tokens (e.g. "direct-tunnel-test*.log") are NOT expanded - they
        # simply never match. Logs are not audited anyway.
        if ($t.Contains('*') -or $t.Contains('?')) { continue }
        $isDir = $t.EndsWith('/')
        if ($isDir) { $t = $t.TrimEnd('/') }
        $abs = Convert-ToAbs $t
        if (-not $abs) { continue }
        if ($isDir) { [void]$dirTokens.Add($abs) } else { [void]$fileTokens.Add($abs) }
    }
}

# v3.3 (2026-11-07): add *.py and *.mjs. Before this, runner.py / manager.py /
#       dag.workflow.mjs and every harness were INVISIBLE to the audit - the
#       ledger line for them existed only because I remembered to write it.
#       That is exactly the failure mode this script is supposed to catch.
#       Cost: ~/.dsh/scripts also has .mjs files; they are only reported when
#       they changed inside the audit window, so old untouched ones stay quiet.
$auditExt = @('*.md', '*.ps1', '*.json', '*.yml', '*.yaml', '*.py', '*.mjs')

# 2a. scan the project (skip node_modules and the ledger itself)
# v3.1: exclude the ledger by FULL PATH, not by file name. The old
#       "$_.Name -ne 'ledger.md'" also blessed any other file called
#       ledger.md anywhere under the project (reviewer N1, 2026-11-06).
$projFiles = @(Get-ChildItem -Path $root -Recurse -File -Include $auditExt -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\node_modules\\' -and $_.FullName -ne $ledgerPath })

# 2b. scan the external scope, if any
$extRoots = New-Object System.Collections.ArrayList
$extFiles = New-Object System.Collections.ArrayList
$extEntries = 0
$extMissing = 0
if (Test-Path -LiteralPath $externalList) {
    foreach ($raw in (Get-Content -LiteralPath $externalList -Encoding UTF8)) {
        $entry = $raw.Trim()
        if (-not $entry -or $entry.StartsWith('#')) { continue }
        $extEntries++
        $abs = Convert-ToAbs $entry
        if (-not $abs) { continue }
        $native = $abs.Replace('/', '\')
        if (Test-Path -LiteralPath $native -PathType Container) {
            [void]$extRoots.Add($abs)
            $found = @(Get-ChildItem -Path $native -Recurse -File -Include $auditExt -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '\\node_modules\\' })
            foreach ($f in $found) { [void]$extFiles.Add($f) }
        } elseif (Test-Path -LiteralPath $native -PathType Leaf) {
            [void]$extRoots.Add($abs)
            [void]$extFiles.Add((Get-Item -LiteralPath $native))
        } else {
            [void]$extRoots.Add("$abs  (missing)")
            $extMissing++
        }
    }
    if ($extEntries -eq 0) {
        [void]$warn.Add(("external list has NO usable entry -- external scope OFF: {0}" -f (Convert-ToDisplay $externalListN)))
    }
    if ($extMissing -gt 0) {
        [void]$warn.Add(("{0} external list entry(ies) do not exist -- scope is narrower than it looks" -f $extMissing))
    }
} else {
    [void]$warn.Add(("external list NOT FOUND -- external scope OFF, audit silently narrowed: {0}" -f (Convert-ToDisplay $externalListN)))
}

$allFiles = @($projFiles) + @($extFiles)
$recent = @($allFiles | Where-Object { $_.LastWriteTime -ge $ledgerStart })

# 3. cross-check on absolute paths
$suspect = New-Object System.Collections.ArrayList
$dirHits = @{}
foreach ($f in $recent) {
    $d = $f.LastWriteTime.ToString('yyyy-MM-dd')
    $abs = ($f.FullName).Replace('\', '/')
    $rel = Convert-ToDisplay $abs

    $mentioned = $false
    foreach ($t in $fileTokens) {
        if ($abs -eq $t) { $mentioned = $true; break }
    }
    if (-not $mentioned) {
        foreach ($t in $dirTokens) {
            if ($abs -eq $t -or $abs.StartsWith($t + '/')) {
                $mentioned = $true
                if ($dirHits.ContainsKey($t)) { $dirHits[$t]++ } else { $dirHits[$t] = 1 }
                break
            }
        }
    }

    if (-not $mentioned) {
        [void]$suspect.Add("$rel  (mtime $d) -- never mentioned in ledger")
    } elseif (-not $dateSet.ContainsKey($d)) {
        [void]$suspect.Add("$rel  (mtime $d) -- ledger has no entry dated $d")
    }
}

# 3b. a DIRECTORY token blesses everything beneath it. "decisions/" therefore
#     exempts every new file in that tree from this audit. Report the broad ones
#     instead of letting them pass as normal bookkeeping.
foreach ($t in $dirHits.Keys) {
    if ($dirHits[$t] -ge 3) {
        $disp = if ($t.StartsWith($rootN + '/')) { $t.Substring($rootN.Length + 1) + '/' } else { Convert-ToDisplay $t }
        [void]$warn.Add(("broad DIR token in ledger: {0} covers {1} in-window file(s)" -f $disp, $dirHits[$t]))
    }
}

$scopeExt = if ($extRoots.Count -gt 0) { ($extRoots | ForEach-Object { Convert-ToDisplay $_ }) -join ', ' } else { '(none)' }
Write-Output ("[ledger] {0} line(s); dates: {1}; audited since {2}" -f `
    $lineCount, (($dateSet.Keys | Sort-Object) -join ', '), $ledgerStart.ToString('yyyy-MM-dd HH:mm'))
Write-Output ("[scope]  project  = {0}" -f (Convert-ToDisplay $rootN))
Write-Output ("[scope]  external = {0}" -f $scopeExt)
Write-Output ("[scan] {0} file(s) total ({1} project + {2} external); {3} changed inside the audit window" -f `
    $allFiles.Count, $projFiles.Count, $extFiles.Count, $recent.Count)
Write-Output ("[scope]  logs     = never audited (*.log excluded on purpose)")
if ($warn.Count -gt 0) {
    foreach ($w in $warn) { Write-Output ("[warn] {0}" -f $w) }
}

if ($suspect.Count -eq 0) {
    Write-Output "[suspect] none"
    Write-Output "RESULT=LEDGER_OK"
    exit 0
}

Write-Output "[suspect]"
$suspect | ForEach-Object { Write-Output "  - $_" }
Write-Output "RESULT=LEDGER_SUSPECT"
exit 1
