$ErrorActionPreference = "Stop"

python -m PyInstaller --clean --noconfirm idle_butler.spec
Write-Host "Built dist/IdleButler.exe"

