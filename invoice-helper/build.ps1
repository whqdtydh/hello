# Build script: PyInstaller -> copy runtime assets -> zip -> optional tag
# Usage: .\build.ps1 [version]
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Version: from arg or app/config.py
if ($args.Count -gt 0) { $ver = $args[0] }
else {
    $m = Select-String -Path "app\config.py" -Pattern 'APP_VERSION = "([^"]+)"'
    $ver = $m.Matches[0].Groups[1].Value
}
Write-Host "==> Building version $ver"

# 1) PyInstaller
python -m PyInstaller invoice-helper.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 2) Copy runtime assets (bypass PyInstaller path validation bugs)
$dist = "dist\invoice-helper"

# WebView2Loader.dll
if (Test-Path "WebView2Loader.dll") {
    Copy-Item "WebView2Loader.dll" "$dist\WebView2Loader.dll" -Force
    Write-Host "Copied WebView2Loader.dll"
}

# WebView2 managed DLLs (Core + WinForms)
$managedSrc = "webview2sdk\lib\net462"
if (Test-Path $managedSrc) {
    $dlls = Get-ChildItem $managedSrc -Filter "*.dll" | Where-Object { $_.Name -in @("Microsoft.Web.WebView2.Core.dll","Microsoft.Web.WebView2.WinForms.dll") }
    $destDir = "$dist\webview2_managed"
    if (-not (Test-Path "$dist\webview2_managed")) { New-Item -ItemType Directory -Path "$dist\webview2_managed" | Out-Null }
    foreach ($dll in $dlls) { Copy-Item $dll.FullName "$dist\webview2_managed\" -Force }
    Write-Host "Copied WebView2 managed DLLs"
}

# OCR models (optional pre-seed)
$modelsSrc = "app\engine\ocr_light\models"
if (Test-Path $modelsSrc) {
    Copy-Item $modelsSrc "$dist\app\engine\ocr_light\" -Recurse -Force
    Write-Host "Seeded OCR models"
}

# Verify exe
$dist = "dist\invoice-helper"
if (-not (Test-Path "$dist\invoice-helper.exe")) { throw "EXE not found" }

# Clean temp
Get-ChildItem $dist -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $dist -Recurse -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

# Size
$size = (Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("==> Size {0:N1} MB" -f $size)

# Zip
$zip = "dist\invoice-helper-v$ver.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$dist\*" -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Asset: $zip"
Get-FileHash $zip -Algorithm SHA256 | Select-Object Hash | Format-List

# Optional push tag
$push = Read-Host "Push tag v$ver to GitHub? (y/N)"
if ($push -eq "y") {
    git add -A
    git commit -m "release v$ver"
    git tag "v$ver"
    git push origin master --tags
    Write-Host "Pushed. Create Release on GitHub and upload $zip"
} else {
    Write-Host "Skipped push. Manual: GitHub Releases -> New v$ver -> upload $zip"
}