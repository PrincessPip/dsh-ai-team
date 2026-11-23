# review-backfill.ps1 - recover verdict JSON from the raw reviewer text
#
# PURPOSE: some review rounds were saved as "-raw.txt" only; the structured
#          verdict JSON was never written. This extracts the FIRST fenced
#          ```json block from each *-raw.txt and writes "<id>.json" next to it.
# WHY: P2 needs a queryable decision log. A log with holes cannot be counted.
#      Backfilled files are NOT rewrites of history: the raw text stays as the
#      source of truth, and every recovered file is listed in decisions/BACKFILL.md.
#
# SAFETY: never overwrites an existing .json. Validates the block with
#         ConvertFrom-Json before writing. Fails loudly (exit 1) if any raw
#         file has no parsable JSON block.
#
# USAGE: powershell -NoProfile -File scripts/review-backfill.ps1
# EXIT:  0 = all missing verdicts recovered (or nothing to do) | 1 = at least one failed
#
# ASCII-only on purpose: this machine runs Windows PowerShell 5.1, which
# decodes a BOM-less file as code page 936 and would mangle non-ASCII text.

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dir = Join-Path $root 'decisions'
$utf8 = New-Object System.Text.UTF8Encoding($false)

$filled = 0
$skipped = 0
$failed = 0
$report = New-Object System.Collections.ArrayList

foreach ($raw in (Get-ChildItem -LiteralPath $dir -Filter '*-raw.txt' -File | Sort-Object Name)) {
    $targetName = ($raw.Name -replace '-raw\.txt$', '.json')
    $target = Join-Path $dir $targetName

    if (Test-Path -LiteralPath $target) {
        Write-Output ("[skip]  {0}  (structured verdict already exists)" -f $raw.Name)
        $skipped++
        continue
    }

    $txt = [System.IO.File]::ReadAllText($raw.FullName, $utf8)
    $m = [regex]::Match($txt, '(?s)```json\s*(\{.*?\})\s*```')
    if (-not $m.Success) {
        Write-Output ("[FAIL]  {0}  (no fenced json block)" -f $raw.Name)
        $failed++
        continue
    }

    $json = $m.Groups[1].Value.Trim()
    $parsed = $null
    try { $parsed = $json | ConvertFrom-Json } catch {
        Write-Output ("[FAIL]  {0}  (block does not parse: {1})" -f $raw.Name, $_.Exception.Message)
        $failed++
        continue
    }

    [System.IO.File]::WriteAllText($target, $json + "`n", $utf8)
    Write-Output ("[fill]  {0} -> {1}  (verdict={2}, confidence={3})" -f $raw.Name, $targetName, $parsed.verdict, $parsed.confidence)
    [void]$report.Add([pscustomobject]@{ Source = $raw.Name; Recovered = $targetName; Verdict = $parsed.verdict; Confidence = $parsed.confidence })
    $filled++
}

Write-Output ""
Write-Output ("RESULT=filled={0} skipped={1} failed={2}" -f $filled, $skipped, $failed)

if ($report.Count -gt 0) {
    Write-Output ""
    Write-Output "Recovered this run:"
    foreach ($r in $report) {
        Write-Output ("  {0}  ->  {1}  [{2} / {3}]" -f $r.Source, $r.Recovered, $r.Verdict, $r.Confidence)
    }
    Write-Output ""
    Write-Output "NOTE: append these lines to decisions/BACKFILL.md by hand (this script does not edit markdown)."
}

if ($failed -gt 0) { exit 1 }
exit 0
