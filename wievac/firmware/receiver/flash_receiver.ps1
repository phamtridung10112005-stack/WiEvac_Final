# Flash script for WiEvac Receiver nodes
param(
    [string]$Port = "",
    [int]$NodeId = 0
)

$PythonExe = "C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe"
$Esptool = "C:\esp\v6.0.2\esp-idf\components\esptool_py\esptool\esptool.py"
$NvsGen = "C:\esp\v6.0.2\esp-idf\components\nvs_flash\nvs_partition_generator\nvs_partition_gen.py"
$BuildDir = Join-Path $PSScriptRoot "build"
$Binary = Join-Path $BuildDir "wievac_receiver.bin"
$Bootloader = Join-Path $BuildDir "bootloader\bootloader.bin"
$PartitionTable = Join-Path $BuildDir "partition_table\partition-table.bin"

if (-not (Test-Path $Binary)) {
    Write-Error "Khong tim thay file binary: $Binary. Vui long bien dich truoc!"
    exit 1
}

if ([string]::IsNullOrEmpty($Port)) {
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($ports.Count -eq 0) {
        Write-Error "Khong tim thay cong COM nao duoc ket noi! Vui long cam ESP32 vao may."
        exit 1
    }
    $Port = $ports[0]
}

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "Dang ket noi voi ESP32 tren cong $Port..." -ForegroundColor Yellow

# Read MAC address to identify node
$macOutput = & $PythonExe $Esptool -p $Port read_mac 2>&1 | Out-String
$mac = "chua_xac_dinh"
if ($macOutput -match "MAC:\s+([0-9a-fA-F:]{17})") {
    $mac = $matches[1].ToLower()
    Write-Host "MAC Hardware: $mac" -ForegroundColor Cyan
}

# Node ID Assignment
$assignedId = $NodeId
if ($assignedId -le 0) {
    if ($mac -eq "94:a9:90:ea:ea:10") { $assignedId = 1 }
    elseif ($mac -eq "28:84:85:48:ed:30" -or $mac -eq "28:84:85:48:db:dc") { $assignedId = 2 }
    elseif ($mac -eq "28:84:85:85:53:94") { $assignedId = 3 }
    else { $assignedId = 1 }
}

Write-Host "Thiet lap Node: Node $assignedId (Hanh lang $assignedId)" -ForegroundColor Green

# Generate NVS configuration if custom NodeId or default
$tempCsv = Join-Path $env:TEMP "wievac_nvs_$Port.csv"
$tempBin = Join-Path $env:TEMP "wievac_nvs_$Port.bin"
$csvContent = @"
key,type,encoding,value
wievac_cfg,namespace,,
rx_id,data,u32,$assignedId
"@
$csvContent | Out-File -FilePath $tempCsv -Encoding ascii
& $PythonExe $NvsGen generate $tempCsv $tempBin 0x6000 2>&1 | Out-Null

Write-Host "Dang nap firmware: Bootloader (0x0), Partitions (0x8000), Config NVS (0x9000), App (0x10000)..." -ForegroundColor Yellow
if (Test-Path $tempBin) {
    & $PythonExe $Esptool -p $Port -b 921600 write_flash 0x0 $Bootloader 0x8000 $PartitionTable 0x9000 $tempBin 0x10000 $Binary
    Remove-Item -Path $tempCsv, $tempBin -Force -ErrorAction SilentlyContinue
} else {
    & $PythonExe $Esptool -p $Port -b 921600 write_flash 0x0 $Bootloader 0x8000 $PartitionTable 0x10000 $Binary
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "===> NAP THANH CONG! Node $assignedId se tu dong khoi dong va bat dau do passability." -ForegroundColor Green
} else {
    Write-Host "===> NAP THAT BAI! Vui long kiem tra ket noi cap USB." -ForegroundColor Red
}
Write-Host "=================================================" -ForegroundColor Cyan
