# 发版脚本：打包 → 清理体积 → zip 资产 → 可选推 GitHub tag
# 用法：.\build.ps1 或 .\build.ps1 1.2.0
# 依赖：PyInstaller（pip install pyinstaller）、git 已配置远程仓库
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 版本号：参数优先，否则读 app/config.py 的 APP_VERSION
if ($args.Count -gt 0) {
    $ver = $args[0]
} else {
    $m = Select-String -Path "app\config.py" -Pattern 'APP_VERSION = "([^"]+)"'
    $ver = $m.Matches[0].Groups[1].Value
}
Write-Host "==> 打包版本 $ver"

# 1) 打包（onedir）
python -m PyInstaller 发票助手.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# 2) 产物校验：exe 存在
$dist = "dist\发票助手"
if (-not (Test-Path "$dist\发票助手.exe")) { throw "未找到 exe，打包异常" }

# 3) 清理临时产物（可选）：删除 *.pyc / __pycache__
Get-ChildItem $dist -Recurse -Filter "__pycache__" -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $dist -Recurse -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

# 4) 统计体积
$size = (Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("==> 产物体积 {0:N1} MB" -f $size)

# 5) zip 资产（供 GitHub Releases 自动更新下载）
$zip = "dist\发票助手-v$ver.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$dist\*" -DestinationPath $zip -CompressionLevel Optimal
Write-Host "==> 资产已生成: $zip"
Get-FileHash $zip -Algorithm SHA256 | Select-Object Hash | Format-List

# 6) 可选：推 tag 到 GitHub（git tag vX && git push origin vX）
$push = Read-Host "推送到 GitHub Releases 标签 v$ver ？(y/N)"
if ($push -eq "y") {
    git add -A
    git commit -m "release v$ver"
    git tag "v$ver"
    git push origin master --tags
    Write-Host "==> 已推送，请在 GitHub 创建 Release 并上传 $zip"
} else {
    Write-Host "==> 未推送。手动发版：GitHub -> Releases -> 新建 v$ver，上传 $zip"
}
