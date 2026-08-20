[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$MapsUrl,

    [ValidateSet("composition", "eu-isa")]
    [string]$Profile = "eu-isa",

    [ValidateSet("console", "markdown", "json", "all")]
    [string]$Format = "console",

    [switch]$StrictEvidence,

    [switch]$Offline,

    [switch]$NoAutoGeometry,

    [string]$Track,

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

function Enable-RoadProofScrollback {
    try {
        $buffer = $Host.UI.RawUI.BufferSize
        if ($buffer.Height -lt 9999) {
            $buffer.Height = 9999
            $Host.UI.RawUI.BufferSize = $buffer
        }
    } catch {
        # Windows Terminal manages scrollback itself; the persistent run log
        # below remains available when a host does not expose RawUI settings.
    }
}

Enable-RoadProofScrollback

# Read package metadata without importing NumPy: importing the incompatible
# CPython 3.14 / NumPy 2.2 MinGW build can terminate the interpreter.
& $Python -c "import importlib.metadata as m, sys; version=tuple(map(int, m.version('numpy').split('.')[:2])); raise SystemExit(0 if sys.version_info < (3, 14) or version >= (2, 3) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-RoadProof "RoadProof found an incompatible NumPy environment." Red
    Write-RoadProof "Run .\installer.ps1 once to replace it with the official binary, then retry." White
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
$LogDirectory = Join-Path $OutputDirectory "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogFile = Join-Path $LogDirectory ("roadproof-session-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss-fff"))

$env:PYTHONIOENCODING = "utf-8"
Set-Location $ProjectRoot
$RoadProofArguments = @(
    "-m", "roadproof",
    "--url", $MapsUrl,
    "--profile", $Profile,
    "--format", $Format,
    "--output-dir", $OutputDirectory,
    "--log-file", $LogFile
)
if ($StrictEvidence) {
    $RoadProofArguments += "--strict-evidence"
}
if ($Offline) {
    $RoadProofArguments += "--offline"
}
if ($NoAutoGeometry) {
    $RoadProofArguments += "--no-auto-geometry"
}
if (-not [string]::IsNullOrWhiteSpace($Track)) {
    $RoadProofArguments += @("--track", $Track)
}
& $Python @RoadProofArguments
exit $LASTEXITCODE
