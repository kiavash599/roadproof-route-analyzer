[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$MapsUrl,

    [ValidateSet("composition", "eu-isa")]
    [string]$Profile = "eu-isa",

    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$UseColor = -not (Test-Path Env:NO_COLOR)

function Write-RoadProof([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White) {
    if ($UseColor) {
        Write-Host $Message -ForegroundColor $Color
    } else {
        Write-Host $Message
    }
}

if (-not (Test-Path $Python)) {
    Write-RoadProof "RoadProof is not installed yet." Yellow
    Write-RoadProof "Run .\installer.ps1 once, then run .\start.ps1 again." White
    exit 2
}

if ([string]::IsNullOrWhiteSpace($MapsUrl)) {
    Write-RoadProof "RoadProof route analyzer" Cyan
    $MapsUrl = Read-Host "Paste the Google Maps route link"
}

if ([string]::IsNullOrWhiteSpace($MapsUrl)) {
    Write-RoadProof "No link was entered." Red
    exit 2
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot "reports"
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $ProjectRoot
& $Python -m roadproof --url $MapsUrl --profile $Profile --output-dir $OutputDirectory
exit $LASTEXITCODE
