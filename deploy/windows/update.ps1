param(
    [string]$Branch = "master",
    [int]$KeepImages = 3,
    [int]$TimeoutSeconds = 120,
    [switch]$Force,
    [switch]$SkipFetch,
    [switch]$SkipBackup,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Compose = @("compose", "--project-directory", $Repo)
$Service = "memsave"
$Port = if ($env:MEMSAVE_PORT) { [int]$env:MEMSAVE_PORT } else { 8780 }

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & docker @Args
    if ($LASTEXITCODE -ne 0) { throw "docker $($Args -join ' ') failed with exit code $LASTEXITCODE" }
}

function Compose-Exec {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    Invoke-Docker @($Compose + @("exec", "-T", $Service) + $Args)
}

function Running-Commit {
    try {
        $probe = "import json,urllib.request; print((json.load(urllib.request.urlopen('http://127.0.0.1:8780/api/version', timeout=3)).get('commit') or ''))"
        $dockerArgs = @("compose", "--project-directory", $Repo, "exec", "-T", $Service, "python", "-c", $probe)
        $raw = & docker @dockerArgs 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($raw | Select-Object -Last 1).Trim()
    } catch {
        return $null
    }
}

function Wait-Commit {
    param([string]$Commit)
    if (-not $Commit) { return $false }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $running = Running-Commit
        if ($running -and $running.StartsWith($Commit.Substring(0, [Math]::Min(7, $Commit.Length)))) {
            return $true
        }
    }
    return $false
}

function Restore-Previous {
    $exists = & docker image inspect "memsave:previous" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No memsave:previous image to restore." -ForegroundColor Yellow
        return $false
    }
    Write-Host "Restoring memsave:previous ..." -ForegroundColor Yellow
    Invoke-Docker @("tag", "memsave:previous", "memsave:latest")
    Invoke-Docker @($Compose + @("up", "-d", "--no-build", $Service))
    return $true
}

if (-not (Test-Path (Join-Path $Repo "docker-compose.yml"))) {
    throw "docker-compose.yml not found in $Repo"
}

Write-Host "memsave update in $Repo" -ForegroundColor Cyan

if ($Rollback) {
    if (-not (Restore-Previous)) { exit 1 }
    exit 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is not installed" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "docker is not installed" }
Invoke-Docker @("version")

if (-not $SkipFetch) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & git -C $Repo fetch --prune --tags --force origin $Branch
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 3) { throw "git fetch failed" }
        Start-Sleep -Seconds 2
    }
}

$currentBranch = (& git -C $Repo rev-parse --abbrev-ref HEAD).Trim()
if ($currentBranch -ne $Branch) {
    throw "working copy is on '$currentBranch', expected '$Branch'"
}

& git -C $Repo merge --ff-only "origin/$Branch"
if ($LASTEXITCODE -ne 0) { throw "fast-forward merge failed; resolve the working copy manually" }

$target = (& git -C $Repo rev-parse HEAD).Trim()
$remote = (& git -C $Repo rev-parse "origin/$Branch").Trim()
if ($target -ne $remote) { throw "HEAD ($target) differs from origin/$Branch ($remote)" }

$versionFile = Join-Path $Repo "memsave\__init__.py"
$versionMatch = Select-String -Path $versionFile -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $versionMatch) { throw "could not read __version__ from $versionFile" }
$version = $versionMatch.Matches[0].Groups[1].Value
Write-Host "Target: $Branch $($target.Substring(0,7)) v$version" -ForegroundColor Cyan

$tagged = & git -C $Repo tag --points-at $target
if (-not ($tagged -match "v$([regex]::Escape($version))")) {
    Write-Host "Warning: target commit has no v$version tag." -ForegroundColor Yellow
}

$running = Running-Commit
if (-not $Force -and $running -and $running.StartsWith($target.Substring(0,7))) {
    Write-Host "Already running $($target.Substring(0,7)); nothing to do (use -Force to rebuild)." -ForegroundColor Green
    exit 0
}

if (-not $SkipBackup) {
    Write-Host "Backing up the archive before update ..." -ForegroundColor Cyan
    try {
        Compose-Exec "python" "-m" "memsave" "backup"
    } catch {
        throw "backup failed; update aborted. Use -SkipBackup only if you have a backup."
    }
}

& docker image inspect "memsave:latest" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Invoke-Docker @("tag", "memsave:latest", "memsave:previous")
}

$short = $target.Substring(0, 7)
$commitDate = (& git -C $Repo show -s --format=%cI $target).Trim()
$created = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$worktree = Join-Path ([System.IO.Path]::GetTempPath()) ("memsave-build-" + [guid]::NewGuid().ToString("N"))

try {
    & git -C $Repo worktree add --detach $worktree $target
    if ($LASTEXITCODE -ne 0) { throw "could not create a clean worktree" }
    Invoke-Docker @(
        "build",
        "-t", "memsave:latest",
        "-t", "memsave:$short",
        "--build-arg", "MEMSAVE_COMMIT=$target",
        "--build-arg", "MEMSAVE_COMMIT_DATE=$commitDate",
        "--build-arg", "MEMSAVE_VERSION=$version",
        "--build-arg", "MEMSAVE_CREATED=$created",
        $worktree
    )
} finally {
    if (Test-Path $worktree) {
        & git -C $Repo worktree remove --force $worktree 2>$null | Out-Null
    }
}

Invoke-Docker @($Compose + @("up", "-d", "--no-build", $Service))

if (-not (Wait-Commit -Commit $target)) {
    Write-Host "New container did not report $($target.Substring(0,7)); rolling back." -ForegroundColor Red
    if ((Restore-Previous) -and $running) {
        Wait-Commit -Commit $running | Out-Null
    }
    exit 1
}

Write-Host "memsave is running $($target.Substring(0,7)) v$version" -ForegroundColor Green

try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health.StatusCode -ne 200) {
        Write-Host "Warning: published port returned $($health.StatusCode)." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Warning: published port $Port does not answer /health." -ForegroundColor Yellow
}

try {
    $old = & docker images --format "{{.Repository}}:{{.Tag}} {{.ID}}" "memsave:*" |
        Where-Object { $_ -match "^memsave:([0-9a-f]{7,40})\s" } |
        Select-Object -Skip $KeepImages
    foreach ($line in $old) {
        $id = ($line -split "\s+")[-1]
        if ($id) { & docker rmi $id 2>$null | Out-Null }
    }
} catch {
    Write-Host "Could not prune old images (not fatal)." -ForegroundColor Yellow
}

Write-Host "Update complete." -ForegroundColor Green

