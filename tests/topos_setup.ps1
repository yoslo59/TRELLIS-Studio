# Exercise the component plan and repair receipt without downloading the GPU runtime.
$ErrorActionPreference='Stop'
$scripts=Join-Path $PSScriptRoot '..\installer\windows'
. (Join-Path $scripts 'Setup-Preflight.ps1')
$tempRoot=[IO.Path]::GetTempPath()
$folder=Join-Path $tempRoot ('trellis-topos-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $folder | Out-Null
try {
    if ((Get-SetupInstallPlan $folder).Topos) { throw 'TOPOS should be optional on a new installation.' }
    # Only hardware probing and external process execution are replaced in this test.
    Copy-Item -LiteralPath (Join-Path $scripts 'Install-Components.ps1') -Destination $folder
    Set-Content -LiteralPath (Join-Path $folder 'Setup-Preflight.ps1') -Value @'
function Get-SetupPreflight {
    return @{Gpu=@{Supported=$script:gpuReady; Status='CUDA unavailable'; Uuid='fixture'; Profile='test'}}
}
'@
    . (Join-Path $folder 'Install-Components.ps1')
    $script:gpuReady=$true
    $script:toposCommands=@()
    function Invoke-GuidedSetupCommand([string]$Executable,[string[]]$Arguments) {
        if ($Arguments -match 'install_topos\.py$') {
            $script:toposCommands+=,[pscustomobject]@{Verify='--verify-only' -in $Arguments; Exe=$Executable}
        }
    }
    $licenses=New-Item -ItemType Directory -Path (Join-Path $folder 'licenses')
    $license=Join-Path $licenses 'DINOv3-LICENSE.md'
    Set-Content -LiteralPath $license -Value 'fixture'
    $receiptPath=Join-Path $folder 'config\component-install.json'
    Save-GuidedSetupJson $receiptPath @{schema=1;requested=@();licenses=@{dinov3=(Get-FileHash $license).Hash}}

    $result=Invoke-GuidedSetup -InstallRoot $folder -RequestedComponents 'topos'
    $state=Read-GuidedSetupJson $receiptPath
    if ($result -ne 0 -or ($state.requested -join ',') -ne 'trellis,topos' -or
        $state.components.topos.status -ne 'ready' -or -not (Get-SetupInstallPlan $folder).Topos -or
        $script:toposCommands.Count -ne 1 -or $script:toposCommands[0].Verify -or
        $script:toposCommands[0].Exe -ne (Join-Path $folder '.venv\Scripts\python.exe')) {
        throw 'Selecting TOPOS must install it after TRELLIS and restore the saved selection.'
    }
    $result=Invoke-GuidedSetup -InstallRoot $folder -Retry
    if ($result -ne 0 -or $script:toposCommands.Count -ne 2 -or -not $script:toposCommands[1].Verify) {
        throw 'Repair must verify the existing TOPOS installation offline.'
    }
    $script:gpuReady=$false
    $result=Invoke-GuidedSetup -InstallRoot $folder -Retry
    if ($result -ne 3 -or (Read-GuidedSetupJson $receiptPath).components.topos.status -ne 'deferred' -or
        $script:toposCommands.Count -ne 2) { throw 'A missing CUDA GPU must defer the download.' }
    $script:gpuReady=$true
    $result=Invoke-GuidedSetup -InstallRoot $folder -RequestedComponents 'trellis' -SkipTopos
    if ($result -ne 0 -or (Get-SetupInstallPlan $folder).Topos -or $script:toposCommands.Count -ne 2) {
        throw 'Deselecting TOPOS must clear the saved request.'
    }
    Write-Host 'PASS: TOPOS selection, dependencies, verification, deferred download and deselection.'
} finally {
    if ((Split-Path -Parent $folder) -eq $tempRoot.TrimEnd('\') -and (Split-Path -Leaf $folder) -match '^trellis-topos-[0-9a-f]{32}$') {
        Remove-Item -LiteralPath $folder -Recurse -Force
    }
}
