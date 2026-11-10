param([switch]$SkipRig,[switch]$NoLaunch,[switch]$Plan,[switch]$VerifyOnly,[switch]$AcceptDinoV3License,[string]$WslDistribution='Ubuntu-22.04',[switch]$WorkshopOnly)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$env:PYTHONUTF8='1'
$env:UV_PYTHON_INSTALL_DIR=Join-Path $taskRoot '.tools\python'
$env:UV_CACHE_DIR=Join-Path $taskRoot '.tools\uv-cache'
$env:UV_MANAGED_PYTHON='1'
if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
$taskManifest=Get-Content -LiteralPath (Join-Path $taskRoot 'installer\manifest.json') -Raw | ConvertFrom-Json
if ($Plan) { $taskManifest | ConvertTo-Json -Depth 8;return }
if (-not $WorkshopOnly -and -not $VerifyOnly -and -not $AcceptDinoV3License) {
    Write-Host 'TRELLIS Studio code: MIT. Model dependencies retain their own terms.'
    Write-Host 'DINOv3 is governed by licenses\DINOv3-LICENSE.md, including use restrictions.'
    Write-Host 'Read: https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md'
    if ((Read-Host 'After reading it, type ACCEPT to accept the DINOv3 license, or anything else to exit') -cne 'ACCEPT') { Write-Host 'No installation performed.';exit 1 }
    $AcceptDinoV3License=$true
}
$taskUv=Join-Path $taskRoot '.tools\uv\uv.exe'
if (-not (Test-Path -LiteralPath $taskUv)) {
    if ($VerifyOnly) { throw 'uv is not installed. Run Install TRELLIS Studio.cmd first.' }
    $taskUvDir=Split-Path -Parent $taskUv
    New-Item -ItemType Directory -Force -Path $taskUvDir | Out-Null
    $taskZip=Join-Path $taskUvDir 'uv.zip'
    Invoke-WebRequest -Uri $taskManifest.sources.uv.url -OutFile $taskZip -UseBasicParsing -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash.ToLowerInvariant() -cne $taskManifest.sources.uv.sha256) { throw 'uv checksum mismatch. No downloaded executable was run.' }
    Expand-Archive -LiteralPath $taskZip -DestinationPath $taskUvDir -Force
}
if ($VerifyOnly) { $taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe' }
else {
    & $taskUv python install 3.11 --no-bin
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 provisioning failed.' }
    $taskPython=(& $taskUv python find 3.11).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 was not found.' }
}
$taskArguments=@((Join-Path $taskRoot 'installer\setup.py'),'--uv',$taskUv,'--wsl-distribution',$WslDistribution)
if ($AcceptDinoV3License) { $taskArguments+='--accept-dinov3-license' }
if ($SkipRig) { $taskArguments+='--skip-rig' }
if ($VerifyOnly) { $taskArguments+='--verify-only' }
if ($WorkshopOnly) { $taskArguments+='--workshop-only' }
& $taskPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'Installation did not complete. Fix the reported prerequisite and run the same command again.' }
if (-not $NoLaunch -and -not $VerifyOnly) { & (Join-Path $taskRoot 'Start-Studio.ps1') }
