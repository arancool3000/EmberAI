# Build + publish the WINDOWS Ember release and update the auto-update manifest.
#
#   ./RELEASE-windows.ps1            # release the version currently in version.py (no bump)
#   ./RELEASE-windows.ps1 -Version 1.1.0
#
# Run this for the SAME version as the macOS release (RELEASE.command). It uploads
# Ember-Windows.zip to the same GitHub release and merges its entry into latest.json,
# preserving the macOS download. One-time setup is in PUBLISH_SETUP.md.
param([string]$Version = "")
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$owner = (python -c "import version; print(version.GITHUB_OWNER)").Trim()
if ($owner -eq "YOUR_GITHUB_USERNAME") {
  Write-Host "Set GITHUB_OWNER in version.py first (see PUBLISH_SETUP.md). Aborting." -ForegroundColor Red
  exit 1
}
$repo = (python -c "import version; print(version.GITHUB_REPO)").Trim()

if ($Version -ne "") { python _release_helper.py bump $Version | Out-Null }
$ver = (python -c "import version; print(version.__version__)").Trim()
$tag = "v$ver"
Write-Host "=== Releasing Ember $tag (Windows) ===" -ForegroundColor Cyan

python _release_helper.py sync-site | Out-Null

Write-Host "Building Ember.exe (first build: 3-6 min)..." -ForegroundColor Cyan
python -m pip install --upgrade pip | Out-Null
python -m pip install -r requirements.txt
python -m pip install pyinstaller | Out-Null
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
pyinstaller --noconfirm Ember.spec

# Zip the onedir folder contents as Ember-Windows.zip (the updater extracts with zipfile).
$zip = "dist\Ember-Windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\Ember\*" -DestinationPath $zip
Write-Host "Packed $zip" -ForegroundColor Green

$pub = (Get-Date -Format "yyyy-MM-dd")
$sha = (python _release_helper.py manifest windows $ver $zip RELEASE_NOTES.md $pub).Trim()
Write-Host "sha256: $($sha.Substring(0,16))..."

# Publish the release (create the tag, or upload to it if the mac release already made it).
$hasGh = $false
try { gh --version *> $null; gh auth status *> $null; $hasGh = ($LASTEXITCODE -eq 0) } catch { $hasGh = $false }
if ($hasGh) {
  $exists = $false
  try { gh release view $tag *> $null; $exists = ($LASTEXITCODE -eq 0) } catch { $exists = $false }
  if ($exists) {
    gh release upload $tag $zip dist\latest.json --clobber
  } else {
    gh release create $tag $zip dist\latest.json --title "Ember $tag" --notes-file RELEASE_NOTES.md
  }
  Write-Host "Release $tag: Ember-Windows.zip + latest.json" -ForegroundColor Green
} else {
  Write-Host "gh CLI not available/authed - upload manually:" -ForegroundColor Yellow
  Write-Host "  https://github.com/$owner/$repo/releases  (tag $tag)"
  Write-Host "  Upload: $zip  and  dist\latest.json  (winget: 'winget install GitHub.cli' to automate)"
}

# Push the version bump + site so GitHub Pages updates.
if (Test-Path .git) {
  git add version.py docs RELEASE_NOTES.md
  git commit -m "Release $tag (windows)" 2>$null
  git push origin HEAD
  Write-Host "Pushed; GitHub Pages redeploys in ~1 min" -ForegroundColor Green
} else {
  Write-Host "No git repo yet - see PUBLISH_SETUP.md, then commit + push version.py + docs/." -ForegroundColor Yellow
}

Write-Host "`nDone. Site: https://$owner.github.io/$repo/" -ForegroundColor Green
