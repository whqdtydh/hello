$OutDir = "$env:USERPROFILE\Desktop\Xiaomi_RE_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Output Dir: $OutDir"

# 核心目标文件
$coreFiles = @(
  "C:\Program Files\MI\Xiaomi G Command Center\1.0.2.310\XiaomiGCommandCenter.exe",
  "C:\Program Files (x86)\Timi Personal Computing\MiService\4.1.1.121\MiService.exe",
  "C:\Program Files (x86)\Timi Personal Computing\MiService\4.1.1.121\MiDeviceService\MiDeviceService.exe",
  "C:\Program Files\MI\XiaomiPCManager\5.4.0.620\EcIoSdk.dll",
  "C:\Program Files\MI\XiaomiPCManager\5.4.0.620\Drivers\EcIo\XiaomiEcIo.sys",
  "C:\Program Files\MI\XiaomiPCManager\5.4.0.620\PcControlCenter.dll",
  "C:\Program Files\MI\XiaomiPCManager\5.4.0.620\XiaomiPcManager.exe"
)

$keywords = 'Fan','Thermal','EC','ACPI','WMI','IOCTL','DeviceIoControl','PNP0C09',
            'SetFan','Performance','Beast','Mode','SmartFan','FanCurve','PWM','RPM',
            'Xiaomi','MiService','MiDevice','IoControlCode','InputBuffer','OutputBuffer'
$pattern = $keywords -join '|'

foreach ($fpath in $coreFiles) {
  if (-not (Test-Path $fpath)) { Write-Host "Missing: $fpath" -Fore Yellow; continue }
  $name = [IO.Path]::GetFileNameWithoutExtension($fpath)
  $sub = Join-Path $OutDir $name
  New-Item -ItemType Directory -Force -Path $sub | Out-Null
  
  try {
    $bytes = [IO.File]::ReadAllBytes($fpath)
    $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    $regex = '[ -~]{4,}'
    [Regex]::Matches($text, $regex).Value | Out-File "$sub\strings_ascii.txt" -Encoding utf8
    $text16 = [System.Text.Encoding]::Unicode.GetString($bytes)
    [Regex]::Matches($text16, $regex).Value | Out-File "$sub\strings_utf16.txt" -Encoding utf8
    
    Select-String -Path "$sub\strings_ascii.txt","$sub\strings_utf16.txt" -Pattern $pattern -CaseSensitive:$false |
      Select-Object LineNumber,Line,Path | Export-Csv "$sub\keywords_hits.csv" -NoTypeInformation
    
    try {
      $asm = [Reflection.AssemblyName]::GetAssemblyName($fpath)
      "MANAGED: $asm" | Out-File "$sub\managed_info.txt"
    } catch {}
    
    Write-Host "[+] $name Done" -Fore Green
  } catch {
    Write-Host "[-] $name Failed: $_" -Fore Red
  }
}
Write-Host "=== Done ===" -Fore Green
Write-Host "Results in: $OutDir"