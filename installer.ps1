[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$UseColor = -not (Test-Path Env:NO_COLOR)

function Write-RoadProof([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White) {
    if ($UseColor) {
        Write-Host $Message -ForegroundColor $Color
    } else {
        Write-Host $Message
    }
}

function Write-Step([string]$Message) {
    Write-RoadProof "`n==> $Message" Cyan
}

function Find-Python {
    $candidates = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $candidates += ,@("py.exe", "-3")
    }
    if (Get-Command python.exe -ErrorAction SilentlyContinue) {
        $candidates += ,@("python.exe")
    }
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $localPython) {
        $candidates += ,@($localPython)
    }

    foreach ($candidate in $candidates) {
        try {
            $command = $candidate[0]
            $prefix = @($candidate | Select-Object -Skip 1)
            & $command @prefix -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [PSCustomObject]@{
                    Command = $command
                    Prefix = $prefix
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

Set-Location $ProjectRoot
Write-RoadProof "RoadProof cross-platform setup" Green
Write-RoadProof "Project: $ProjectRoot" DarkGray

$python = Find-Python
if (-not $python) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Python 3.10+ was not found and winget is unavailable. Install Python from https://www.python.org/downloads/windows/ and run installer.ps1 again."
    }

    Write-Step "Installing Python 3.12 for the current Windows user"
    & winget.exe install --id Python.Python.3.12 --exact --scope user --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Python installation failed with exit code $LASTEXITCODE."
    }
    $python = Find-Python
    if (-not $python) {
        throw "Python was installed but is not visible yet. Close PowerShell, open it again, and rerun installer.ps1."
    }
}

$pythonCommand = $python.Command
$pythonPrefix = @($python.Prefix)
$version = & $pythonCommand @pythonPrefix -c "import platform; print(platform.python_version())"
Write-RoadProof "Python $version detected" Green

Write-Step "Creating an isolated RoadProof environment"
if (-not (Test-Path $VenvPython)) {
    & $pythonCommand @pythonPrefix -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python environment."
    }
}

Write-Step "Installing RoadProof requirements"
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Requirement installation failed." }

Write-Step "Running the built-in self-check"
& $VenvPython -m roadproof --self-check
if ($LASTEXITCODE -ne 0) { throw "RoadProof self-check failed." }

Write-RoadProof "`nRoadProof is ready." Green
Write-RoadProof "Run .\start.ps1 or start.bat and paste a Google Maps route link." White
Get-ChildItem -Path $ProjectRoot -Filter "*.ps1" | Unblock-File -ErrorAction SilentlyContinue
