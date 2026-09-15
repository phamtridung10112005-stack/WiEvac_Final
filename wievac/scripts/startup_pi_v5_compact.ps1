param(
    [string]$Python = "python",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$argsList = @("$PSScriptRoot/run_pi_v5_compact.py")
if ($CheckOnly) { $argsList += "--check-only" }
& $Python @argsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
