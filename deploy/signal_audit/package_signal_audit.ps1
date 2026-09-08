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

function Normalize-FullPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)
    $separator = [System.IO.Path]::DirectorySeparatorChar
    return [System.IO.Path]::GetFullPath($PathValue).Replace(
        [System.IO.Path]::AltDirectorySeparatorChar, $separator
    ).TrimEnd($separator)
}

function Resolve-OutputPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$PathValue
    )
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        throw "OutputDir must not be empty"
    }
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return Normalize-FullPath $PathValue
    }
    return Normalize-FullPath (Join-Path $BasePath $PathValue)
}

function Test-IsDescendantPath {
    param(
        [Parameter(Mandatory = $true)][string]$ChildPath,
        [Parameter(Mandatory = $true)][string]$ParentPath
    )
    $childFull = Normalize-FullPath $ChildPath
    $parentFull = Normalize-FullPath $ParentPath
    $prefix = $parentFull + [System.IO.Path]::DirectorySeparatorChar
    return $childFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-SafeOutputPath {
    param(
        [Parameter(Mandatory = $true)][string]$CandidatePath,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string[]]$ForbiddenRoots,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $candidateFull = Normalize-FullPath $CandidatePath
    $repoFull = Normalize-FullPath $RepoRoot
    if (-not (Test-IsDescendantPath -ChildPath $candidateFull -ParentPath $repoFull)) {
        throw "$Label must stay under repository root: $candidateFull"
    }
    foreach ($forbidden in $ForbiddenRoots) {
        $forbiddenFull = Normalize-FullPath $forbidden
        if ($candidateFull.Equals($forbiddenFull, [System.StringComparison]::OrdinalIgnoreCase) -or
            (Test-IsDescendantPath -ChildPath $candidateFull -ParentPath $forbiddenFull)) {
            throw "$Label must not be inside source directory: $candidateFull"
        }
    }
}

$repoRoot = Normalize-FullPath $root.Path
$deploySource = Normalize-FullPath $PSScriptRoot
$frontendSource = Normalize-FullPath $frontend.Path
$distRoot = Resolve-OutputPath -BasePath $repoRoot -PathValue $OutputDir
$forbiddenOutputRoots = @(
    $deploySource,
    $frontendSource,
    (Normalize-FullPath (Join-Path $repoRoot "tools")),
    (Normalize-FullPath (Join-Path $repoRoot "demo")),
    (Normalize-FullPath (Join-Path $repoRoot "docs"))
)
Assert-SafeOutputPath -CandidatePath $distRoot -RepoRoot $repoRoot -ForbiddenRoots $forbiddenOutputRoots -Label "OutputDir"

$packageRoot = Normalize-FullPath (Join-Path $distRoot "signal-audit-deploy")
$zipPath = Normalize-FullPath (Join-Path $distRoot "signal-audit-deploy.zip")
Assert-SafeOutputPath -CandidatePath $packageRoot -RepoRoot $repoRoot -ForbiddenRoots $forbiddenOutputRoots -Label "Package output"
Assert-SafeOutputPath -CandidatePath $zipPath -RepoRoot $repoRoot -ForbiddenRoots $forbiddenOutputRoots -Label "Zip output"
if (-not (Test-IsDescendantPath -ChildPath $packageRoot -ParentPath $distRoot)) {
    throw "Package output must stay under OutputDir: $packageRoot"
}
if (-not (Test-IsDescendantPath -ChildPath $zipPath -ParentPath $distRoot)) {
    throw "Zip output must stay under OutputDir: $zipPath"
}

if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
New-Item -ItemType Directory -Path $packageRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "frontend") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "tools") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "deploy") | Out-Null

foreach ($assetName in @("index.html", "app.js", "VERSION.json", "README.md")) {
    $assetPath = Join-Path $frontend.Path $assetName
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
        throw "missing frontend asset: $assetPath"
    }
    Copy-Item -LiteralPath $assetPath -Destination (Join-Path $packageRoot "frontend") -Force
}
Copy-Item -LiteralPath (Join-Path $frontend.Path "signal_cards") -Destination (Join-Path $packageRoot "frontend") -Recurse -Force

Copy-Item -LiteralPath (Join-Path $root.Path "tools\materialize_signal_cards.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_llm_review.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_llm_review_entry.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_evidence_v2.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_review_v2.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_review_v2_runtime.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_llm_review_canary_release.sh") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\server_self_check_signal_stack.sh") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_fact_semantics.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_or_update.sh") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run_signal_llm_review.sh") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-llm.env.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "apache-bitnami-signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit-location.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.service") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.timer") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-llm-review.service") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-llm-review.timer") -Destination (Join-Path $packageRoot "deploy") -Force

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -Force

Write-Output "package_root=$packageRoot"
Write-Output "zip=$zipPath"
