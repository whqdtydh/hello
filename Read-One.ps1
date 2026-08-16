$base = "C:\Users\48641\Desktop\Xiaomi_RE_20260816_010105"
$t = "MiService"
$csv = $base + "\" + $t + "\keywords_hits.csv"
if (Test-Path $csv) {
  $data = Import-Csv $csv
  Write-Host "=== $t : $($data.Count) hits ===" -Fore Cyan
  $data | Select-Object LineNumber, @{N='Line';E={$_.Line.Substring(0,[Math]::Min(300,$_.Line.Length))}} | Format-Table -AutoSize -Wrap
}