param(
    [string]$FrontendArchive = "",
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")
if (-not $FrontendArchive) {
    $FrontendArchive = Join-Path $PSScriptRoot "frontend"
}
$frontend = Resolve-Path -LiteralPath $FrontendArchive
$distRoot = Join-Path $root.Path $OutputDir
$packageRoot = Join-Path $distRoot "signal-audit-deploy"
$zipPath = Join-Path $distRoot "signal-audit-deploy.zip"

if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $packageRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "frontend") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "tools") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "deploy") | Out-Null

Copy-Item -LiteralPath (Join-Path $frontend.Path "index.html") -Destination (Join-Path $packageRoot "frontend") -Force
Copy-Item -LiteralPath (Join-Path $frontend.Path "app.js") -Destination (Join-Path $packageRoot "frontend") -Force
Copy-Item -LiteralPath (Join-Path $frontend.Path "signal_cards") -Destination (Join-Path $packageRoot "frontend") -Recurse -Force

Copy-Item -LiteralPath (Join-Path $root.Path "tools\materialize_signal_cards.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\gemini_signal_llm_review.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_or_update.sh") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "apache-bitnami-signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit-location.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.service") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.timer") -Destination (Join-Path $packageRoot "deploy") -Force

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -Force

Write-Output "package_root=$packageRoot"
Write-Output "zip=$zipPath"
