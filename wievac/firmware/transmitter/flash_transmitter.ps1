# Flash script for WiEvac Transmitter (TX) node
param(
    [string]$Port = ""
)

$PythonExe = "C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe"
$Esptool = "C:\esp\v6.0.2\esp-idf\components\esptool_py\esptool\esptool.py"
$BuildDir = Join-Path $PSScriptRoot "build"
$Binary = Join-Path $BuildDir "wievac_transmitter.bin"
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
Write-Host "Dang ket noi voi ESP32 tren cong $Port de nap TRANSMITTER (TX)..." -ForegroundColor Yellow

# Read MAC address
$macOutput = & $PythonExe $Esptool -p $Port read_mac 2>&1 | Out-String
if ($macOutput -match "MAC:\s+([0-9a-fA-F:]{17})") {
    $mac = $matches[1].ToLower()
    Write-Host "Thiet bi duoc chon lam TRANSMITTER co MAC: $mac" -ForegroundColor Green
}

Write-Host "Dang nap firmware Transmitter: Bootloader (0x0), Partitions (0xa000), App (0x20000)..." -ForegroundColor Yellow
& $PythonExe $Esptool -p $Port -b 921600 write_flash 0x0 $Bootloader 0xa000 $PartitionTable 0x20000 $Binary

if ($LASTEXITCODE -eq 0) {
    Write-Host "===> NAP THANH CONG! Board nay da thanh TRANSMITTER (TX), se tu dong khoi dong va phat xung CSI 100Hz." -ForegroundColor Green
} else {
    Write-Host "===> NAP THAT BAI! Vui long kiem tra ket noi cap USB." -ForegroundColor Red
}
Write-Host "=================================================" -ForegroundColor Cyan
