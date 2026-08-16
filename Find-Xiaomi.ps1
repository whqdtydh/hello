# Find-Xiaomi.ps1 - Locate Xiaomi binaries
# Via shortcut
$lnk = 'C:\Users\Public\Desktop\Xiaomi G Command Center.lnk'
if (Test-Path $lnk) {
  $sh = New-Object -ComObject WScript.Shell
  $shortcut = $sh.CreateShortcut($lnk)
  Write-Host "Shortcut Target: $($shortcut.TargetPath)"
  Write-Host "Working Dir: $($shortcut.WorkingDirectory)"
}

# Via Uninstall registry
Write-Host "`n=== Uninstall Registry ==="
Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\* |
  Where-Object {$_.DisplayName -match 'Xiaomi|MiService|G Command'} |
  Select-Object DisplayName, InstallLocation, UninstallString | Format-List

# Via Services
Write-Host "`n=== Services ==="
Get-CimInstance Win32_Service -Filter "Name LIKE '%Mi%' OR Name LIKE '%Xiaomi%'" |
  Select-Object Name, PathName, StartName | Format-List

# Via Process
Write-Host "`n=== Running Processes ==="
Get-Process -Name "*Mi*","*Xiaomi*","*GCommand*" -ErrorAction SilentlyContinue |
  Select-Object ProcessName, Id, Path | Format-Table -AutoSize