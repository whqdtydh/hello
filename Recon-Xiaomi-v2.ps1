$OutDir = "$env:USERPROFILE\Desktop\Xiaomi_RE_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "Output Dir: $OutDir" -Fore Green

$searchRoots = @(
  "C:\Program Files\MI\Xiaomi G Command Center\1.0.2.310",
  "C:\Program Files (x86)\Timi Personal Computing\MiService\4.1.1.121",
  "C:\Program Files\MI\XiaomiPCManager\5.4.0.620"
)

$targets = @()
foreach ($root in $searchRoots) {
  if (Test-Path $root) {
    $targets += Get-ChildItem $root -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object {$_.Extension -match '\.(exe|dll|sys)$'}
  }
}
Write-Host "Found $($targets.Count) target files" -Fore Cyan
$targets | Select-Object FullName,Length,Extension | Format-Table -AutoSize

$keywords = 'Fan','Thermal','EC','ACPI','WMI','IOCTL','DeviceIoControl','PNP0C09',
            'SetFan','Performance','Beast','Mode','SmartFan','FanCurve','PWM','RPM',
            'Xiaomi','MiService','MiDevice','IoControlCode','InputBuffer','OutputBuffer'
$pattern = $keywords -join '|'

foreach ($f in $targets) {
  $name = [IO.Path]::GetFileNameWithoutExtension($f)
  $sub = Join-Path $OutDir $name
  New-Item -ItemType Directory -Force -Path $sub | Out-Null
  
  try {
    $bytes = [IO.File]::ReadAllBytes($f.FullName)
    # ASCII strings
    $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    $regex = '[ -~]{4,}'
    [Regex]::Matches($text, $regex).Value | Out-File "$sub\strings_ascii.txt" -Encoding utf8
    # UTF-16LE strings
    $text16 = [System.Text.Encoding]::Unicode.GetString($bytes)
    [Regex]::Matches($text16, $regex).Value | Out-File "$sub\strings_utf16.txt" -Encoding utf8
    
    # Keyword filter
    Select-String -Path "$sub\strings_ascii.txt","$sub\strings_utf16.txt" -Pattern $pattern -CaseSensitive:$false |
      Select-Object LineNumber,Line,Path | Export-Csv "$sub\keywords_hits.csv" -NoTypeInformation
    
    # .NET check
    try {
      $asm = [Reflection.AssemblyName]::GetAssemblyName($f.FullName)
      "MANAGED: $asm" | Out-File "$sub\managed_info.txt"
    } catch {}
    
    Write-Host "[+] $name Done" -Fore Green
  } catch {
    Write-Host "[-] $name Failed: $_" -Fore Red
  }
}
Write-Host "=== Recon Done ===" -Fore Green
Write-Host "Results in: $OutDir"