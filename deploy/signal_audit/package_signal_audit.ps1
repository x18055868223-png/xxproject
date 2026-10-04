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
New-Item -ItemType Directory -Path (Join-Path $packageRoot "fmz") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $packageRoot "docs\astra") | Out-Null

foreach ($assetName in @("index.html", "app.js", "VERSION.json", "README.md")) {
    $assetPath = Join-Path $frontend.Path $assetName
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
        throw "missing frontend asset: $assetPath"
    }
    Copy-Item -LiteralPath $assetPath -Destination (Join-Path $packageRoot "frontend") -Force
}
Copy-Item -LiteralPath (Join-Path $frontend.Path "signal_cards") -Destination (Join-Path $packageRoot "frontend") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root.Path "demo\最新交付物\neutral_regulation_demo_fmz.py") -Destination (Join-Path $packageRoot "fmz") -Force

$releaseDoc = Join-Path $root.Path "docs\astra\80_工作台v249非FMZ阶段发布协议_20261004.md"
if (-not (Test-Path -LiteralPath $releaseDoc -PathType Leaf)) {
    throw "missing release protocol doc: $releaseDoc"
}
Copy-Item -LiteralPath $releaseDoc -Destination (Join-Path $packageRoot "docs\astra") -Force

Copy-Item -LiteralPath (Join-Path $root.Path "tools\materialize_signal_cards.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_llm_review.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_llm_review_entry.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_evidence_v2.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_change_display.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_review_v2.py") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_review_v2_runtime.py") -Destination (Join-Path $packageRoot "tools") -Force
foreach ($jointModule in @("signal_review_joint.py", "astra_joint_v11_contract.py", "astra_joint_v11_data.py", "astra_joint_v11_inference.py", "astra_joint_v11_cards.py", "astra_joint_v11_forward.py", "astra_joint_v11_evaluation.py", "astra_joint_contract.py", "astra_joint_projection.py", "astra_joint_bridge.py", "astra_joint_inference.py", "astra_joint_data.py", "astra_joint_events.py", "astra_joint_dataset.py", "astra_joint_sources.py", "astra_joint_shadow.py", "astra_joint_card_statistics.py", "astra_joint_operations.py")) {
    Copy-Item -LiteralPath (Join-Path $root.Path "tools\$jointModule") -Destination (Join-Path $packageRoot "tools") -Force
}
Copy-Item -LiteralPath (Join-Path $root.Path "tools\server_self_check_signal_stack.sh") -Destination (Join-Path $packageRoot "tools") -Force
Copy-Item -LiteralPath (Join-Path $root.Path "tools\signal_fact_semantics.py") -Destination (Join-Path $packageRoot "tools") -Force
$workbenchModules = @(
    "astra_workbench_v20.py",
    "astra_risk_qualification_v244.py",
    "astra_commercial_math_v17.py",
    "astra_underwriting_v20.py",
    "astra_underwriting_fact_context_v20.py",
    "astra_underwriting_discovery_v20.py",
    "astra_underwriting_public_quote_v20.py",
    "astra_underwriting_real_replay_v20.py",
    "astra_underwriting_store_v20.py",
    "astra_underwriting_review_v20.py",
    "astra_underwriting_guidance_v20.py",
    "astra_data_engine_v23.py",
    "astra_data_projection_v23.py",
    "astra_data_retention_v23.py",
    "astra_data_server_v23.py",
    "astra_map_sources_v24.py",
    "astra_map_v24.py",
    "astra_map_projection_v24.py",
    "astra_map_presentation_v241.py"
)
foreach ($module in $workbenchModules) {
    $modulePath = Join-Path $root.Path "tools\$module"
    if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
        throw "missing workbench module: $modulePath"
    }
    Copy-Item -LiteralPath $modulePath -Destination (Join-Path $packageRoot "tools") -Force
}
Copy-Item -LiteralPath (Join-Path $root.Path "demo\shared\data_product_registry_v23.json") -Destination (Join-Path $packageRoot "tools\data_product_registry_v23.json") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_or_update.sh") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "joint-shadow") -Destination (Join-Path $packageRoot "deploy") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-llm.env.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "apache-bitnami-signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "nginx.signal-audit-location.conf.example") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.service") -Destination (Join-Path $packageRoot "deploy") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "signal-audit-materialize.timer") -Destination (Join-Path $packageRoot "deploy") -Force
foreach ($assetName in @("workbench-v20.service", "workbench-v20.env.example", "data-engine-maintenance.service", "data-engine-maintenance.timer", "data-engine-maintenance.env.example", "install_data_engine_v23.sh")) {
    $assetPath = Join-Path $PSScriptRoot $assetName
    if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
        throw "missing workbench deployment asset: $assetPath"
    }
    Copy-Item -LiteralPath $assetPath -Destination (Join-Path $packageRoot "deploy") -Force
}

function Get-RelativePackagePath {
    param(
        [Parameter(Mandatory = $true)][string]$PackageRootValue,
        [Parameter(Mandatory = $true)][string]$FilePath
    )
    $rootFull = Normalize-FullPath $PackageRootValue
    $fileFull = Normalize-FullPath $FilePath
    if (-not (Test-IsDescendantPath -ChildPath $fileFull -ParentPath $rootFull)) {
        throw "manifest file escaped package root: $fileFull"
    }
    return $fileFull.Substring($rootFull.Length + 1).Replace(
        [System.IO.Path]::DirectorySeparatorChar, '/'
    )
}

function Invoke-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & git -C $repoRoot @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return (($output -join "`n").Trim())
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$FilePath)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($FilePath)
    try {
        $hashBytes = $sha256.ComputeHash($stream)
    } finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
    return ([System.BitConverter]::ToString($hashBytes) -replace "-", "").ToLowerInvariant()
}

$gitTopLevel = Invoke-GitText @("rev-parse", "--show-toplevel")
$gitRootMatches = $false
$gitHead = $null
$gitBranch = $null
$gitStatus = $null
if ($gitTopLevel) {
    $gitRootMatches = (Normalize-FullPath $gitTopLevel).Equals(
        (Normalize-FullPath $repoRoot),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}
if ($gitRootMatches) {
    $gitHead = Invoke-GitText @("rev-parse", "HEAD")
    $gitBranch = Invoke-GitText @("rev-parse", "--abbrev-ref", "HEAD")
    $gitStatus = Invoke-GitText @("status", "--porcelain")
}
$sourceState = "not_git_root"
if ($gitRootMatches -and $gitHead) {
    if ([string]::IsNullOrWhiteSpace($gitStatus)) {
        $sourceState = "clean"
    } else {
        $sourceState = "dirty"
    }
}

$versionJsonPath = Join-Path $packageRoot "frontend\VERSION.json"
$versionInfo = Get-Content -Raw -Encoding UTF8 -LiteralPath $versionJsonPath | ConvertFrom-Json
$reviewToolText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $root.Path "tools\astra_underwriting_review_v20.py")
$reviewProtocol = $null
if ($reviewToolText -match 'RESPONSE_PROTOCOL\s*=\s*"([^"]+)"') {
    $reviewProtocol = $Matches[1]
}
$deterministicFinancialsSchema = $null
if ($reviewToolText -match 'DETERMINISTIC_FINANCIALS_SCHEMA\s*=\s*"([^"]+)"') {
    $deterministicFinancialsSchema = $Matches[1]
}
$fmzReferencePath = Join-Path $packageRoot "fmz\neutral_regulation_demo_fmz.py"

$manifestPath = Join-Path $packageRoot "package-manifest.json"
$fileEntries = @(Get-ChildItem -LiteralPath $packageRoot -File -Recurse | ForEach-Object {
    $relativePath = Get-RelativePackagePath -PackageRootValue $packageRoot -FilePath $_.FullName
    if ($relativePath -eq "package-manifest.json") {
        return
    }
    [pscustomobject]@{
        path = $relativePath
        bytes = $_.Length
        sha256 = Get-Sha256Hex $_.FullName
    }
} | Sort-Object -Property path)

$manifest = [pscustomobject]@{
    manifest_schema = "signal_audit_package_manifest@1"
    generated_at_utc = [System.DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssK")
    source = [pscustomobject]@{
        repo_root = $repoRoot
        git_top_level = $gitTopLevel
        git_root_matches_package_root = $gitRootMatches
        head = $gitHead
        branch = $gitBranch
        source_state = $sourceState
        release_commit = $null
        release_commit_verification = "not_performed_by_packager"
    }
    frontend = [pscustomobject]@{
        backup_version = $versionInfo.backup_version
        frontend_cache_token = $versionInfo.frontend_cache_token
        local_review_revision = $versionInfo.local_review_revision
        workbench_schema = $versionInfo.workbench_schema
    }
    protocol = [pscustomobject]@{
        review_response_protocol = $reviewProtocol
        deterministic_financials_schema = $deterministicFinancialsSchema
    }
    fmz = [pscustomobject]@{
        reference_only = $true
        producer_version_reference = $versionInfo.producer_version
        packaged_file = "fmz/neutral_regulation_demo_fmz.py"
        packaged_file_sha256 = Get-Sha256Hex $fmzReferencePath
        deployment_scope = "reference-only; installer does not deploy FMZ runtime"
    }
    release_documents = @($releaseDoc | ForEach-Object {
        "docs/astra/" + [System.IO.Path]::GetFileName($_)
    })
    files = $fileEntries
}
$manifestJson = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    $manifestPath,
    $manifestJson + [System.Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $packageRoot "*") -DestinationPath $zipPath -Force
$zipSha256 = Get-Sha256Hex $zipPath

Write-Output "package_root=$packageRoot"
Write-Output "zip=$zipPath"
Write-Output "zip_sha256=$zipSha256"
