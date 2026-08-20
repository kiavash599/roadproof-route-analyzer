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
        foreach ($minor in 14, 13, 12, 11, 10) {
            $candidates += ,@("py.exe", "-3.$minor")
        }
        $candidates += ,@("py.exe", "-3")
    }
    if (Get-Command python.exe -ErrorAction SilentlyContinue) {
        $candidates += ,@("python.exe")
    }
    foreach ($minor in 314, 313, 312, 311, 310) {
        $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$minor\python.exe"
        if (Test-Path $localPython) {
            $candidates += ,@($localPython)
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $command = $candidate[0]
            $prefix = @($candidate | Select-Object -Skip 1)
            & $command @prefix -c "import platform, sys; assert (3, 10) <= sys.version_info[:2] < (3, 15); assert platform.python_implementation() == 'CPython'" 2>$null
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
        throw "CPython 3.10-3.14 was not found and winget is unavailable. Install 64-bit Python from https://www.python.org/downloads/windows/ and run installer.ps1 again."
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

Write-Step "Updating Python installation tooling"
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not update pip." }

Write-Step "Installing the official NumPy binary"
$numpyRequirement = & $VenvPython -c "import sys; print('numpy==2.3.5' if sys.version_info >= (3, 14) else 'numpy==2.2.6')"
& $VenvPython -m pip install --disable-pip-version-check --upgrade --force-reinstall `
    --only-binary=:all: $numpyRequirement
if ($LASTEXITCODE -ne 0) {
    throw "No official NumPy wheel is available for this Python installation. Install 64-bit CPython 3.12 or 3.14 from python.org, remove .venv, and rerun installer.ps1."
}

Write-Step "Installing RoadProof requirements"
& $VenvPython -m pip install --disable-pip-version-check --upgrade -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Requirement installation failed." }

Write-Step "Verifying compiled route-analysis dependencies"
& $VenvPython -c "import warnings; warnings.filterwarnings('error', message=r'Numpy built with MINGW-W64.*'); import numpy, shapely, mapbox_vector_tile; print(f'NumPy {numpy.__version__}; Shapely {shapely.__version__}; vector-tile decoder ready')"
if ($LASTEXITCODE -ne 0) {
    throw "A compiled dependency could not start safely. Rerun installer.ps1; if this repeats, install 64-bit CPython 3.12 from python.org first."
}

Write-Step "Running the built-in self-check"
& $VenvPython -m roadproof --self-check
if ($LASTEXITCODE -ne 0) { throw "RoadProof self-check failed." }

Write-Step "Checking live public-service connectivity"
& $VenvPython -m roadproof --self-check --network-check
if ($LASTEXITCODE -ne 0) {
    Write-RoadProof "One or more public services are temporarily unavailable; installation is complete, but live analysis may need to be retried later." Yellow
}

Write-RoadProof "`nRoadProof is ready." Green
Write-RoadProof "Run .\start.ps1 or start.bat and paste a Google Maps route link." White
Get-ChildItem -Path $ProjectRoot -Filter "*.ps1" | Unblock-File -ErrorAction SilentlyContinue
