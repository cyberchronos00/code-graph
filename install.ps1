# cg installer for Windows (PowerShell 5.1+): installs (or updates / removes) the `cg` command with uv or pipx, then
# sets up the Node / PHP / Dart extractor dependencies in the user cache. Never elevates; prints each step.
#
#   irm https://raw.githubusercontent.com/cyberchronos00/code-graph/main/install.ps1 | iex
#   .\install.ps1 [-Update] [-Version vX.Y.Z] [-Source PATH|URL] [-With rust,c,kotlin,swift] [-NoExtractors] [-Uninstall]
#
# Untested on Windows so far (written alongside install.sh, which is tested); report problems as issues.
[CmdletBinding()]
param(
    [switch]$Update,
    [switch]$Uninstall,
    [string]$Version = "",
    [string]$Source = "",
    [string]$With = "",
    [switch]$NoExtractors
)
$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/cyberchronos00/code-graph"
$Pkg = "codegraph"
$MinPy = [version]"3.11"

function Say($msg) { Write-Host "cg-install: $msg" }
function Run([string]$exe, [string[]]$argv) {
    Say "+ $exe $($argv -join ' ')"
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe exited with $LASTEXITCODE" }
}
function Have($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-Spec {
    if ($Source) {
        if ($Source -match '^(git\+|https?://)' -or $Source -match '\.(whl|tar\.gz)$') { $s = $Source }
        elseif (Test-Path (Join-Path $Source "pyproject.toml")) { $s = (Resolve-Path $Source).Path }
        else { throw "-Source ${Source}: no pyproject.toml there" }
    } else { $s = "git+$RepoUrl" }
    if ($Version) {
        if ($s -notmatch '^git\+') { throw "-Version only applies to the git source" }
        $s = "$s@$Version"
    }
    return $s
}

function Find-Python {
    foreach ($c in @("py -3", "python3", "python")) {
        $parts = $c.Split(" ")
        if (-not (Have $parts[0])) { continue }
        try {
            $v = & $parts[0] @($parts[1..($parts.Length)] | Where-Object { $_ }) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and ([version]$v -ge $MinPy)) { return $c }
        } catch { }
    }
    return $null
}

function Installed-With {
    if ((Have "uv") -and ((uv tool list 2>$null) -match "^$Pkg ")) { return "uv" }
    if ((Have "pipx") -and ((pipx list --short 2>$null) -match "^$Pkg ")) { return "pipx" }
    return "none"
}

$localBin = Join-Path $HOME ".local\bin"
$env:Path = "$localBin;$(Join-Path $HOME '.cargo\bin');$env:Path"

if ($Uninstall) {
    switch (Installed-With) {
        "uv" { Run "uv" @("tool", "uninstall", $Pkg) }
        "pipx" { Run "pipx" @("uninstall", $Pkg) }
        default { Say "cg is not installed with uv or pipx (nothing to uninstall)" }
    }
    $cache = if ($env:CODEGRAPH_CACHE) { $env:CODEGRAPH_CACHE } else { Join-Path $env:LOCALAPPDATA "codegraph" }
    $ex = Join-Path $cache "extractors"
    if (Test-Path $ex) { Say "removing the extractor dependencies in $ex"; Remove-Item -Recurse -Force $ex }
    Say "left in place: index caches in $cache, uv / pipx themselves"
    exit 0
}

$installer = if (Have "uv") { "uv" } elseif (Have "pipx") { "pipx" } else { "none" }
if ($installer -eq "none") {
    Say "neither uv nor pipx found: installing uv for this user (https://astral.sh/uv, no elevation)"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $installer = "uv"
}
$spec = Get-Spec
$py = Find-Python
Say "installing with $installer from $spec"
if ($installer -eq "uv") {
    if (-not $py) { Say "no Python >= $MinPy found: uv downloads a managed Python for cg's environment" }
    if ($Update -and -not $Source -and -not $Version -and (Installed-With) -eq "uv") { Run "uv" @("tool", "upgrade", $Pkg) }
    else { Run "uv" @("tool", "install", "--force", "--reinstall", "--python", ">=$MinPy", $spec) }
    try { uv tool update-shell | Out-Null } catch { }
} else {
    if (-not $py) { throw "Python >= $MinPy not found (install it from python.org, or install uv)" }
    # pipx upgrade only reinstalls a git install when its version changed: reinstall to get the latest commit
    Run "pipx" @("install", "--force", $spec)
}

$cg = (Get-Command cg -ErrorAction SilentlyContinue).Source
if (-not $cg) { $cg = Join-Path $localBin "cg.exe" }
if (-not $NoExtractors) {
    Say "setting up extractor dependencies (TypeScript: npm, PHP: Composer, Dart: pub) for the toolchains present"
    & $cg setup
    if ($LASTEXITCODE -ne 0) { Say "some extractor dependencies were not installed; ``cg doctor`` says which and how" }
}
foreach ($w in ($With.Split(",") | Where-Object { $_ })) {
    switch ($w.Trim()) {
        "rust" {
            if (Have "rust-analyzer") { Say "rust: rust-analyzer already installed" }
            elseif (Have "rustup") { Run "rustup" @("component", "add", "rust-analyzer") }
            else { Say "rust: install Rust from https://rustup.rs, then: rustup component add rust-analyzer" }
        }
        { $_ -in "c", "cpp" } { Say "c: scip-clang has no Windows release binary; C / C++ stays heuristic (docs/native.md)" }
        "kotlin" { Say "kotlin: install a JDK 17+ (https://adoptium.net) and coursier (https://get-coursier.io), then: cs install scip-java; opt in with CODEGRAPH_KOTLIN_SCIP=1" }
        "swift" { Say "swift: install the Swift toolchain (https://www.swift.org/install/windows/); opt in with CODEGRAPH_SWIFT_INDEX=1" }
        default { Say "-With: unknown '$w' (rust, c, kotlin, swift)" }
    }
}
& $cg doctor
Say "done. Update later with: .\install.ps1 -Update   (or: uv tool upgrade $Pkg / pipx upgrade $Pkg)"
