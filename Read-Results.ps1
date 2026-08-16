$base = "$env:USERPROFILE\Desktop\Xiaomi_RE_20260816_010105"
Get-ChildItem $base -Directory | ForEach-Object {
  $csv = Join-Path $_.FullName "keywords_hits.csv"
  if (Test-Path $csv) {
    $count = (Import-Csv $csv).Count
    if ($count -gt 0) {
      Write-Host "=== $($_.Name) : $count hits ===" -Fore Cyan
      Import-Csv $csv | Select-Object LineNumber, Line | Format-Table -AutoSize -Wrap
    }
  }
}