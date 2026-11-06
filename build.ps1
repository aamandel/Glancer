$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

if (-not (Test-Path -LiteralPath "bin\libmpv-2.dll")) {
    throw "bin\libmpv-2.dll is missing. Add it before building (see README)."
}

python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean Glancer.spec
Copy-Item -LiteralPath "README.md" -Destination "dist\README.md" -Force
Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination "dist\THIRD_PARTY_NOTICES.md" -Force

Write-Host "Built the standalone application in dist\"
