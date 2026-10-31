$ErrorActionPreference = 'Stop'
python -m PyInstaller scrappy.spec --noconfirm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
