param(
    [ValidateSet('Workshop','Generation','Rig','Pixal3D','Full','Stop','Server','ServerSetup','ConfigureServer','StopServer')]
    [string]$Mode='Workshop',
    [switch]$NoBrowser,
    [switch]$FullStudio
)
$ErrorActionPreference='Stop'
$taskInstallFull = $Mode -eq 'Full' -or ($Mode -eq 'ServerSetup' -and $FullStudio)
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'bundled-web.json'))) { throw 'Run this launcher from the Windows Setup installation.' }
if (-not $env:TRELLIS_PORT) { $env:TRELLIS_PORT='7870' }
if (-not $env:TRELLIS_MCP_PORT) { $env:TRELLIS_MCP_PORT='7871' }
if ($Mode -eq 'Stop') { & (Join-Path $taskRoot 'Arreter-TRELLIS.ps1') -WithBridge -IdleOnly; return }
if ($Mode -eq 'StopServer') { & (Join-Path $taskRoot 'Stop-Server.ps1'); return }
if ($Mode -in @('Server','ConfigureServer')) {
    & (Join-Path $taskRoot 'Start-Server.ps1') -NoBrowser:$NoBrowser -Configure:($Mode -eq 'ConfigureServer')
    return
}

$taskBootstrap=Join-Path $taskRoot 'Install-Studio.ps1'
$taskMarker=Join-Path $taskRoot '.studio-workshop-ready'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskMcpPython=Join-Path $taskRoot '.mcp-venv\Scripts\python.exe'

function Install-Pixal3D {
    param([switch]$AcceptDinoV3License)
    if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $taskRoot 'engine\MODELS\dinov3\model.safetensors') -PathType Leaf)) {
        throw 'Install TRELLIS generation before adding Pixal3D.'
    }
    Write-Host 'Pixal3D downloads about 8.25 GiB before Python packages.'
    $taskPixalArguments=@('-u',(Join-Path $taskRoot 'pixal3d_setup.py'))
    if ($AcceptDinoV3License) { $taskPixalArguments+='--accept-dinov3-license' }
    & $taskPython @taskPixalArguments
    if ($LASTEXITCODE -ne 0) { throw 'Pixal3D installation stopped. See the diagnostic above. Run the installer again to resume.' }
}

$taskNeedsWorkshop= -not ((Test-Path -LiteralPath $taskMarker) -and (Test-Path -LiteralPath $taskPython) -and (Test-Path -LiteralPath $taskMcpPython))
if (-not $taskNeedsWorkshop) {
    try {
        $taskInstalled=Get-Content -LiteralPath $taskMarker -Raw | ConvertFrom-Json
        foreach ($taskFile in @('installer/setup.py','requirements-windows.txt','requirements-mcp-lock.txt')) {
            $taskHash=(Get-FileHash -LiteralPath (Join-Path $taskRoot $taskFile) -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($taskInstalled.$taskFile -cne $taskHash) { $taskNeedsWorkshop=$true; break }
        }
    } catch { $taskNeedsWorkshop=$true }
}
if ($Mode -ne 'Workshop' -or $taskNeedsWorkshop) {
    & (Join-Path $PSScriptRoot 'check-idle.ps1') -InstallRoot $taskRoot -IgnoreCurrentProcess
    $taskLockPath=Join-Path $taskRoot '.component-install.lock'
    try { $taskLock=[IO.File]::Open($taskLockPath,'OpenOrCreate','ReadWrite','None') }
    catch [IO.IOException] { throw 'Another TRELLIS component installation is running.' }
    $taskTranscript=$false
    try {
        if ($Mode -eq 'Generation') {
            Write-Host 'TRELLIS generation downloads about 15 GiB before Python packages. NVIDIA CUDA and Blender are required.'
            & $taskBootstrap -SkipRig -NoLaunch
            if ($LASTEXITCODE -ne 0) { throw 'TRELLIS generation installation stopped.' }
        } elseif ($Mode -eq 'Rig' -or $taskInstallFull) {
            $taskRigOptions=@{ NoLaunch=$true }
            if ($taskInstallFull) {
                $taskLogDirectory=Join-Path $taskRoot 'logs'
                New-Item -ItemType Directory -Path $taskLogDirectory -Force | Out-Null
                Start-Transcript -LiteralPath (Join-Path $taskLogDirectory 'full-setup.log') -Append | Out-Null
                $taskTranscript=$true
                Write-Host 'Full setup installs TRELLIS generation, UniRig and Pixal3D. About 29 GiB of model downloads plus Python packages.'
                Write-Host 'Keep about 100 GiB free. NVIDIA CUDA, Blender and a configured WSL2 distribution are required.'
                $taskLicensePath=Join-Path $taskRoot 'licenses\DINOv3-LICENSE.md'
                if (-not (Test-Path -LiteralPath $taskLicensePath -PathType Leaf)) { throw 'The DINOv3 license file is missing from this installation.' }
                Write-Host 'TRELLIS Studio code: MIT. TRELLIS generation and Pixal3D use DINOv3 under its own terms.'
                Write-Host "Read the license, including its use restrictions: $taskLicensePath"
                Write-Host 'https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md'
                if ((Read-Host 'Type ACCEPT after reading the DINOv3 license to accept it for both engines') -cne 'ACCEPT') {
                    throw 'DINOv3 license was declined. Full setup stopped before installing the engines.'
                }
                $taskRigOptions.AcceptDinoV3License=$true
                Write-Host 'Step 1 of 2: TRELLIS generation and UniRig.'
            }
            Write-Host 'TRELLIS and UniRig download about 21 GiB before Python packages. Set up Ubuntu 22.04 in WSL first.'
            $taskRuntimeConfig=Join-Path $taskRoot 'config\runtime.json'
            if (Test-Path -LiteralPath $taskRuntimeConfig -PathType Leaf) {
                $taskRuntime=Get-Content -LiteralPath $taskRuntimeConfig -Raw | ConvertFrom-Json
                if ($taskRuntime.wsl_distribution) { $taskRigOptions.WslDistribution=[string]$taskRuntime.wsl_distribution }
            }
            & $taskBootstrap @taskRigOptions
            if ($LASTEXITCODE -ne 0) { throw 'TRELLIS generation or UniRig installation stopped. Run the installer again to resume.' }
            if ($taskInstallFull) {
                Write-Host 'Step 2 of 2: Pixal3D.'
                Install-Pixal3D -AcceptDinoV3License
                Write-Host 'Full Studio components are ready.'
            }
        } elseif ($Mode -eq 'Pixal3D') {
            if ($taskNeedsWorkshop) { throw 'Install TRELLIS generation before adding Pixal3D.' }
            Install-Pixal3D
        } elseif ($taskNeedsWorkshop) {
            Write-Host 'Preparing the local workshop. No generation models will be downloaded.'
            & $taskBootstrap -WorkshopOnly -NoLaunch
            if ($LASTEXITCODE -ne 0) { throw 'Workshop installation stopped.' }
        }
    } catch {
        if ($taskTranscript) { Write-Host ('Full setup stopped. ' + $_.Exception.Message) }
        throw
    } finally {
        try { if ($taskTranscript) { Stop-Transcript | Out-Null } }
        finally { $taskLock.Dispose() }
    }
}
if ($Mode -eq 'ServerSetup') {
    $taskConfigure = -not (Test-Path -LiteralPath (Join-Path $taskRoot 'config\server.json') -PathType Leaf)
    & (Join-Path $taskRoot 'Start-Server.ps1') -Configure:$taskConfigure -NoBrowser:$NoBrowser
} elseif ($Mode -eq 'Workshop') { & (Join-Path $taskRoot 'Start-Studio.ps1') -SansNavigateur:$NoBrowser }
elseif ($Mode -ne 'Full') { Write-Host 'Component ready. Reopen TRELLIS Studio from the Start menu.' }
