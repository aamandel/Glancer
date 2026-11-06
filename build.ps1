$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath "bin\mpv.exe")) {
    throw "bin\mpv.exe is missing. Add mpv.exe before building."
}

python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean FrameStepper.spec
Copy-Item -LiteralPath "README.md" -Destination "dist\README.md" -Force
Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination "dist\THIRD_PARTY_NOTICES.md" -Force

Write-Host "Built the standalone application in dist\"
