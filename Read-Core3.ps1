$base = "C:\Users\48641\Desktop\Xiaomi_RE_20260816_010105"
$targets = @("MiService", "MiDeviceService", "EcIoSdk", "XiaomiEcIo", "PcControlCenter", "XiaomiGCommandCenter", "XiaomiPcManager")

foreach ($t in $targets) {
  $csv = $base + "\" + $t + "\keywords_hits.csv"
  if (Test-Path $csv) {
    $data = Import-Csv $csv
    if ($data.Count -gt 0) {
      Write-Host "=== $t : $($data.Count) hits ===" -Fore Cyan
      $data | Select-Object LineNumber, @{N='Line';E={$_.Line.Substring(0,[Math]::Min(200,$_.Line.Length))}} | Format-Table -AutoSize -Wrap
    } else {
      Write-Host "=== $t : 0 hits ===" -Fore Gray
    }
  } else {
    Write-Host "=== $t : NO CSV ===" -Fore Red
  }
}