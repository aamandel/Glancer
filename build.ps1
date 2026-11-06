# Build Glancer:
#   1. run the tests
#   2. PyInstaller  -> dist\Glancer\        (portable folder)
#   3. Inno Setup   -> dist\GlancerSetup.exe (only if Inno Setup is installed)
$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

# Native tools report failure by exit code. (With "Stop", Windows PowerShell 5.1
# would also treat any stderr output, such as pip's notices, as fatal when
# output is redirected, so run them with "Continue" and check the code.)
function Invoke-Native([string]$what, [scriptblock]$command) {
    $ErrorActionPreference = "Continue"
    & $command
    if ($LASTEXITCODE) { throw "$what failed (exit code $LASTEXITCODE)" }
}

foreach ($name in "libmpv-2.dll", "ffmpeg.exe", "ffprobe.exe") {
    if (-not (Test-Path -LiteralPath "bin\$name")) {
        throw "bin\$name is missing. Add it before building (see README)."
    }
}

Invoke-Native "pip install" { python -m pip install -q -r requirements-dev.txt }
Invoke-Native "Tests" { python -m pytest -q }
Invoke-Native "PyInstaller" { python -m PyInstaller --noconfirm --clean Glancer.spec }
Copy-Item -LiteralPath "README.md" -Destination "dist\Glancer\README.md" -Force
Copy-Item -LiteralPath "THIRD_PARTY_NOTICES.md" -Destination "dist\Glancer\THIRD_PARTY_NOTICES.md" -Force
Write-Host "Portable app: dist\Glancer\Glancer.exe"

# Inno Setup: on PATH, or in its default install folders.
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($iscc) {
    Invoke-Native "Writing installer\associations.iss" {
        python -m glancer.win.associations --inno | Out-File -Encoding utf8 "installer\associations.iss"
    }
    Invoke-Native "Inno Setup" { & $iscc /Qp "installer\glancer.iss" }
    Write-Host "Installer: dist\GlancerSetup.exe"
} else {
    Write-Host "Inno Setup not found: skipped the installer (winget install JRSoftware.InnoSetup)."
}
