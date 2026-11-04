# Reconstruct a missing local runtime; never overwrite an existing installation.
# Run from PowerShell with git, cargo/MSVC and uv available. No cloud calls.
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'runtime'
if (Test-Path -LiteralPath $runtime) {
    throw 'Runtime already exists. Preserve it; this script reconstructs a missing installation only.'
}
New-Item -ItemType Directory -Path $runtime | Out-Null

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit $LASTEXITCODE" }
}

$rustSource = Join-Path $runtime 'audio-analyzer-src'
Invoke-Checked 'git' @('clone', 'https://github.com/JuzzyDee/audio-analyzer-rs.git', $rustSource)
Invoke-Checked 'git' @('-C', $rustSource, 'checkout', '--detach', '997c96a0f1950f44d7cae4308f69c179155aff02')
Invoke-Checked 'git' @('-C', $rustSource, 'apply', (Join-Path $PSScriptRoot 'audio-analyzer-short-clips.patch'))
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'audio-analyzer-Cargo.lock') -Destination (Join-Path $rustSource 'Cargo.lock')
Invoke-Checked 'cargo' @('test', '--release', '--locked', '--manifest-path', (Join-Path $rustSource 'Cargo.toml'), '--lib', 'test_short_clips_do_not_panic')
Invoke-Checked 'cargo' @('build', '--release', '--locked', '--manifest-path', (Join-Path $rustSource 'Cargo.toml'), '--bin', 'mcp-server')
$binaryDir = Join-Path $runtime 'audio-analyzer'
New-Item -ItemType Directory -Path $binaryDir | Out-Null
Copy-Item -LiteralPath (Join-Path $rustSource 'target/release/mcp-server.exe') -Destination $binaryDir

Invoke-Checked 'uv' @('venv', '--python', '3.14', (Join-Path $runtime '.venv'))
Invoke-Checked 'uv' @('pip', 'install', '--python', (Join-Path $runtime '.venv/Scripts/python.exe'), '-r', (Join-Path $PSScriptRoot 'requirements-listening.lock.txt'))

$flSource = Join-Path $runtime 'flstudio-mcp'
Invoke-Checked 'git' @('clone', 'https://github.com/rosasynthesiz/flstudio-mcp.git', $flSource)
Invoke-Checked 'git' @('-C', $flSource, 'checkout', '--detach', 'e6eff37e45a076098ed81ee4e50fb7bf08aef11a')
Invoke-Checked 'git' @('-C', $flSource, 'apply', (Join-Path $PSScriptRoot 'fl-bridge-recovery.patch'))
Invoke-Checked 'git' @('-C', $flSource, 'apply', (Join-Path $PSScriptRoot 'fl-full-duplex.patch'))
Invoke-Checked 'git' @('-C', $flSource, 'apply', (Join-Path $PSScriptRoot 'fl-global-channels.patch'))
Invoke-Checked 'uv' @('venv', '--python', '3.12', (Join-Path $runtime 'fl-venv'))
Invoke-Checked 'uv' @('pip', 'install', '--python', (Join-Path $runtime 'fl-venv/Scripts/python.exe'), '-r', (Join-Path $PSScriptRoot 'requirements-fl.lock.txt'), '-e', $flSource)
Write-Output 'Runtime rebuilt. Existing FL controller files and MCP configuration were preserved. See README for initial FL setup.'

$workbench = Join-Path $runtime 'fl-workbench'
Invoke-Checked 'git' @('clone', 'https://github.com/CryptoJones/FL-Studio-MCP-Server.git', $workbench)
Invoke-Checked 'git' @('-C', $workbench, 'checkout', '--detach', 'a61aee7398dca7a604cbccc7edfef12f7a279659')
Invoke-Checked 'git' @('-C', $workbench, 'apply', (Join-Path $PSScriptRoot 'fl-playlist-preservation.patch'))
Invoke-Checked 'uv' @('venv', '--python', '3.12', (Join-Path $runtime 'workbench-venv'))
Invoke-Checked 'uv' @('pip', 'install', '--python', (Join-Path $runtime 'workbench-venv/Scripts/python.exe'), '-r', (Join-Path $PSScriptRoot 'requirements-workbench.lock.txt'), '-e', $workbench)
Invoke-Checked (Join-Path $runtime 'workbench-venv/Scripts/python.exe') @((Join-Path $PSScriptRoot 'install_kuru_backend.py'))
