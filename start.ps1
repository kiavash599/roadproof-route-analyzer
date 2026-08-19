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

if (-not (Test-Path $Python)) {
    Write-Host "RoadProof is not installed yet." -ForegroundColor Yellow
    Write-Host "Run .\installer.ps1 once, then run .\start.ps1 again." -ForegroundColor White
    exit 2
}

if ([string]::IsNullOrWhiteSpace($MapsUrl)) {
    Write-Host "RoadProof route analyzer" -ForegroundColor Cyan
    $MapsUrl = Read-Host "Paste the Google Maps route link"
}

if ([string]::IsNullOrWhiteSpace($MapsUrl)) {
    Write-Host "No link was entered." -ForegroundColor Red
    exit 2
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot "reports"
}

$env:PYTHONIOENCODING = "utf-8"
Set-Location $ProjectRoot
& $Python -m roadproof --url $MapsUrl --profile $Profile --output-dir $OutputDirectory
exit $LASTEXITCODE
