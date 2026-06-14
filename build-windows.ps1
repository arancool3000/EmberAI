# Build Ember.exe on Windows with PyInstaller.
# Usage (from a Developer PowerShell):  ./build-windows.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=== Ember Windows build ===" -ForegroundColor Cyan

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

# Ember.spec is platform-aware: on Windows it bundles win32/uiautomation/comtypes
# and produces dist\Ember\Ember.exe.
pyinstaller --noconfirm Ember.spec

Write-Host "`nBuild complete. Output: dist\Ember\Ember.exe" -ForegroundColor Green
