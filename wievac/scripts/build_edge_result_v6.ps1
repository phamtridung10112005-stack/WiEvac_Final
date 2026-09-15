<##
.SYNOPSIS
Build the active EdgeResult V6 receiver image in an isolated output tree.

.DESCRIPTION
This script only builds the active V6 receiver image. It never flashes a
device or starts a network updater.
##>
[CmdletBinding()]
param(
    [ValidateSet(1,2)][int]$RxId = 1,
    [string]$IdfPath = 'C:\D\esp32_s3\Espressif\frameworks\esp-idf-v5.4.4',
    [string]$BuildRoot,
    [string]$ConfigPath
)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($BuildRoot)) { $BuildRoot = Join-Path $PSScriptRoot '..\.edge-result-v6-build' }
function Fail([string]$Message) { throw "EdgeResult V6 build blocked: $Message" }
if (-not (Test-Path -LiteralPath $IdfPath)) { Fail "ESP-IDF source not found: $IdfPath" }
$IdfPath = (Resolve-Path -LiteralPath $IdfPath).Path
$idfPy = Join-Path $IdfPath 'tools\idf.py'
$activate = Join-Path $IdfPath 'tools\activate.py'
if (-not (Test-Path -LiteralPath $idfPy)) { Fail "idf.py missing under $IdfPath" }
$versionFile = Join-Path $IdfPath 'tools\cmake\version.cmake'
$vtxt = Get-Content -LiteralPath $versionFile -Raw
$m = [regex]::Match($vtxt, 'IDF_VERSION_MAJOR\s+(\d+).*?IDF_VERSION_MINOR\s+(\d+).*?IDF_VERSION_PATCH\s+(\d+)', 'Singleline')
if (-not $m.Success) { Fail 'Could not determine ESP-IDF version.' }
$idfVersion = "$($m.Groups[1].Value).$($m.Groups[2].Value).$($m.Groups[3].Value)"
if ($idfVersion -ne '5.4.4') { Fail "Expected ESP-IDF 5.4.4, found $idfVersion" }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$project = Join-Path $repoRoot 'firmware\receiver'
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $repoRoot ("config\local\rx-{0:00}.sdkconfig" -f $RxId) }
if (-not (Test-Path -LiteralPath $ConfigPath)) { Fail "Missing RX config: $ConfigPath" }
if (-not [IO.Path]::IsPathRooted($BuildRoot)) { $BuildRoot = Join-Path (Get-Location).Path $BuildRoot }
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
$out = Join-Path $BuildRoot ("rx-{0}-v6" -f $RxId)
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path $out | Out-Null
$effectiveConfig = Join-Path $out 'sdkconfig'
Copy-Item -LiteralPath $ConfigPath -Destination $effectiveConfig -Force
function Set-Config([string]$Path,[string]$Key,[string]$Value) {
    $t = Get-Content -LiteralPath $Path -Raw
    $p = "(?m)^$([regex]::Escape($Key)).*\r?$"
    $line = "$Key$Value"
    if ([regex]::IsMatch($t,$p)) { $t = [regex]::Replace($t,$p,$line) }
    else { $t = $t.TrimEnd()+"`r`n$line`r`n" }
    Set-Content -LiteralPath $Path -Value $t -Encoding ASCII
}
Set-Config $effectiveConfig 'CONFIG_PARTITION_TABLE_CUSTOM=' 'n'
Set-Config $effectiveConfig 'CONFIG_PARTITION_TABLE_SINGLE_APP=' 'y'
# Keep the flash-size value from the per-node sdkconfig; it must match the physical ESP.
Set-Config $effectiveConfig 'CONFIG_WIEVAC_RX_ID=' ([string]$RxId)

# Locate the compatible environment without assuming the missing 3.12 env.
$espRoot = Split-Path (Split-Path $IdfPath -Parent) -Parent
$envRoot = Join-Path $espRoot 'python_env'
$pythonExe = $null
$preferred = Join-Path $envRoot 'idf5.4_py3.11_env\Scripts\python.exe'
if (Test-Path -LiteralPath $preferred) { $pythonExe = $preferred }
if (-not $pythonExe) {
    $candidate = Get-ChildItem -LiteralPath $envRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'idf5.4_py*' -and (Test-Path (Join-Path $_.FullName 'Scripts\python.exe')) } |
        Select-Object -First 1
    if ($candidate) { $pythonExe = Join-Path $candidate.FullName 'Scripts\python.exe' }
}
if (-not $pythonExe) { Fail 'ESP_IDF_SOURCE=FOUND; IDF_PY=FOUND; ESP_IDF_PYTHON_ENV=MISSING' }
$env:IDF_PATH = $IdfPath
$env:IDF_PYTHON_ENV_PATH = Split-Path (Split-Path $pythonExe -Parent) -Parent
# Export cmake/toolchain paths into this PowerShell process (export.ps1 itself
# assumes the absent 3.12 environment, so use IDF's activate.py directly).
$exportTemp = [IO.Path]::GetTempFileName()
$savedEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$exports = & $pythonExe $activate '--export' 2> $exportTemp
$ErrorActionPreference = $savedEap
if ($LASTEXITCODE -ne 0) { $err = Get-Content $exportTemp -Raw; Remove-Item $exportTemp -Force; Fail "ESP-IDF activation failed: $err" }
$exportScript = $exports | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $exportScript) { $err = Get-Content $exportTemp -Raw; Remove-Item $exportTemp -Force; Fail "ESP-IDF activation returned no export script: $err" }
Invoke-Expression (Get-Content -LiteralPath $exportScript -Raw)
Remove-Item $exportTemp -Force -ErrorAction SilentlyContinue

Push-Location $project
try {
    # SDKCONFIG must be passed as a CMake definition; positional -D is not
    # accepted reliably by all idf.py wrappers and can skip sdkconfig.h generation.
    & $pythonExe $idfPy '-B' $out '-D' "SDKCONFIG=$effectiveConfig" '-D' "SDKCONFIG_DEFAULTS=$effectiveConfig" 'reconfigure'
    if ($LASTEXITCODE -ne 0) { Fail 'idf.py reconfigure failed.' }
    & $pythonExe $idfPy '-B' $out 'build'
    if ($LASTEXITCODE -ne 0) { Fail 'idf.py build failed.' }
} finally { Pop-Location }
$bin = Join-Path $out 'wievac_receiver.bin'
if (-not (Test-Path -LiteralPath $bin)) { Fail "Missing receiver artifact: $bin" }
$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $bin).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $bin).Length
$manifest = [ordered]@{
    build_id = "edge-result-v6-rx-$RxId-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
    generated_utc = [DateTime]::UtcNow.ToString('o')
    protocol_version = 5
    feature_schema_version = '6'
    magic = 'WIV5'
    role = 'rx'; device_id = 'device-1'; hardware = 'esp32-s3'
    node_id = "rx-$RxId"; link_id = "link-$RxId"; rx_id = $RxId; tx_id = 'tx-1'
    formula_version = 'formula-flex-v5.2-rx-median-mad'
    model_version = 'NOT_READY'; model_hash = ''
    idf_version = $idfVersion; idf_path = $IdfPath
    partition_table = 'single_app'
    binary = (Resolve-Path -LiteralPath $bin).Path; size = $size; sha256 = $sha
    sdkconfig = (Resolve-Path -LiteralPath $effectiveConfig).Path
    build_dir = (Resolve-Path -LiteralPath $out).Path
    hardware_status = 'built-not-flashed'
}
$manifestPath = Join-Path $BuildRoot 'manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "EDGE_RESULT_V6_BUILD=PASS"
Write-Host "ARTIFACT=$bin"
Write-Host "SHA256=$sha"
Write-Host "MANIFEST=$manifestPath"
