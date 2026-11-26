# Shared by Windows Setup and the Start menu repair shortcut.
function Read-GuidedSetupJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @{} }
    $document=Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($document -isnot [pscustomobject]) { throw "Expected a JSON object in $Path." }
    $values=@{}
    foreach ($property in $document.PSObject.Properties) { $values[$property.Name]=$property.Value }
    return $values
}

function Save-GuidedSetupJson([string]$Path,[hashtable]$Document) {
    [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path))
    $pending=$Path+'.'+[Guid]::NewGuid().ToString('N')+'.pending'
    try {
        [IO.File]::WriteAllText($pending,($Document | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($pending,$Path,[NullString]::Value) }
        else { [IO.File]::Move($pending,$Path) }
    } finally {
        if ([IO.File]::Exists($pending)) { [IO.File]::Delete($pending) }
    }
}

function Invoke-GuidedSetupCommand([string]$Executable,[string[]]$Arguments) {
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "Required executable was not found: $Executable" }
    # Windows PowerShell can surface ordinary native stderr output as an ErrorRecord.
    $ErrorActionPreference='Continue'
    $lastLine=''; $lastError=''
    & $Executable @Arguments 2>&1 | ForEach-Object {
        $line=([string]$_).Trim()
        if ($line) {
            $lastLine=$line
            if ($_ -is [Management.Automation.ErrorRecord] -and $_.FullyQualifiedErrorId -notmatch '^NativeCommandErrorMessage') {
                $lastError=$line
            }
            if ($line -match '^(INSTALLATION STOPPED|PIXAL3D SETUP STOPPED|TOPOS SETUP STOPPED|HI3D SETUP STOPPED|MULTIVIEW TEXTURE SETUP STOPPED|ULTRATEX SETUP STOPPED|NATIVE WORKFLOWS SETUP STOPPED|Kimodo installation failed)\s*:') {
                $lastError=$line
            }
            Write-Host $line
        }
    }
    $code=$LASTEXITCODE
    if ($code -ne 0) {
        $detail=if ($lastError) {$lastError} else {$lastLine}
        throw "Component command exited with code $code. $detail"
    }
}

function Set-GuidedComponentResult([string]$Component,[string]$Status,[string]$Message) {
    $setupState.components[$Component]=@{status=$Status;message=$Message;checked_at=[DateTime]::UtcNow.ToString('o')}
    Save-GuidedSetupJson $setupStatePath $setupState
    Write-Host "$($Component): $Status. $Message"
}

function Invoke-GuidedComponent([string]$Component,[scriptblock]$Install,[scriptblock]$Verify,[string]$DeferredReason='') {
    if ($DeferredReason) {
        Set-GuidedComponentResult $Component 'deferred' $DeferredReason
        return
    }
    $previous=$setupState.components[$Component]
    Set-GuidedComponentResult $Component 'installing' 'Checking prerequisites and existing files.'
    try {
        $verified=$false
        if ($previous.status -eq 'ready' -and $Verify) {
            try { & $Verify; $verified=$true }
            catch { Write-Host "Existing $Component check failed. Preparing only this component again." }
        }
        if (-not $verified) { & $Install }
        Set-GuidedComponentResult $Component 'ready' 'Verification completed.'
    } catch {
        Set-GuidedComponentResult $Component 'failed' $_.Exception.Message
    }
}

function Invoke-GuidedSetup {
    param(
        [Parameter(Mandatory=$true)][string]$InstallRoot,
        [string]$RequestedComponents='',
        [string]$BlenderPath='',
        [string]$GpuUuid='',
        [string]$WslDistribution='',
        [switch]$Retry,
        [switch]$NeedsWorkshop,
        [switch]$WithKimodo,
        [switch]$AcceptKimodoModelLicenses,
        [switch]$AcceptHi3dModelLicenses,
        [switch]$SkipHi3d,
        [switch]$AcceptMultiviewTextureModelLicenses,
        [switch]$SkipMultiviewTexture,
        [switch]$AcceptUltraTexModelLicenses,
        [switch]$SkipUltraTex,
        [switch]$SkipTopos,
        [switch]$AcceptNativeWorkflowModelLicenses,
        [switch]$SkipNativeWorkflows
    )
    $env:UV_LINK_MODE='copy'
    $env:UV_NO_CACHE='1'
    . (Join-Path $PSScriptRoot 'Setup-Preflight.ps1')
    $setupOrder=@('trellis','topos','native-workflows','pixal3d','hi3d','multiview-texture','ultratex','unirig','kimodo')
    $setupStatePath=Join-Path $InstallRoot 'config\component-install.json'
    $setupState=Read-GuidedSetupJson $setupStatePath
    if ($setupState.Count -and $setupState.schema -ne 1) { throw 'Unsupported component setup receipt. Existing settings were preserved.' }
    $setupState.schema=1
    foreach ($section in @('components','licenses')) {
        $entries=@{}
        if ($setupState[$section]) {
            foreach ($entry in $setupState[$section].PSObject.Properties) { $entries[$entry.Name]=$entry.Value }
        }
        $setupState[$section]=$entries
    }
    $requested=@($RequestedComponents -split ',' | ForEach-Object {$_.Trim().ToLowerInvariant()} | Where-Object {$_})
    if ($Retry -and -not $requested.Count) { $requested=@($setupState.requested | Where-Object {$_}) }
    if ($WithKimodo) { $requested+=@('kimodo') }
    if ($SkipTopos) {
        $requested=@($requested | Where-Object { $_ -ne 'topos' })
        $setupState.requested=@($setupState.requested | Where-Object { $_ -ne 'topos' })
    }
    if ($SkipNativeWorkflows) {
        $requested=@($requested | Where-Object { $_ -ne 'native-workflows' })
        $setupState.requested=@($setupState.requested | Where-Object { $_ -ne 'native-workflows' })
    }
    if ($SkipHi3d) {
        $requested=@($requested | Where-Object { $_ -ne 'hi3d' })
        $setupState.requested=@($setupState.requested | Where-Object { $_ -ne 'hi3d' })
    }
    if ($SkipMultiviewTexture) {
        $requested=@($requested | Where-Object { $_ -ne 'multiview-texture' })
        $setupState.requested=@($setupState.requested | Where-Object { $_ -ne 'multiview-texture' })
    }
    if ($SkipUltraTex) {
        $requested=@($requested | Where-Object { $_ -ne 'ultratex' })
        $setupState.requested=@($setupState.requested | Where-Object { $_ -ne 'ultratex' })
    }
    foreach ($name in $requested) {
        if ($name -notin $setupOrder) { throw "Unknown setup component: $name." }
    }
    if (@($requested | Where-Object { $_ -in @('native-workflows','pixal3d','hi3d','multiview-texture','ultratex','topos') }).Count) { $requested+=@('trellis') }
    $selected=@($setupOrder | Where-Object {$_ -in $requested})
    $setupState.requested=@($setupOrder | Where-Object {$_ -in $selected -or $_ -in $setupState.requested})
    Save-GuidedSetupJson $setupStatePath $setupState

    $setupConfigPath=Join-Path $InstallRoot 'config\runtime.json'
    $config=Read-GuidedSetupJson $setupConfigPath
    $probeArgs=@{InstallRoot=$InstallRoot}
    if ($GpuUuid) { $probeArgs.GpuUuid=$GpuUuid }
    if ($BlenderPath) { $probeArgs.BlenderPath=$BlenderPath }
    if ($WslDistribution) { $probeArgs.WslDistribution=$WslDistribution }
    $probe=Get-SetupPreflight @probeArgs
    $nativeWorkflowReason=''
    if ('native-workflows' -in $selected) {
        try {
            $manifest=Read-SetupGpuManifest
            $license=Join-Path $InstallRoot 'licenses\FLUX.2-klein-base-9B-LICENSE.md'
            $licenseHash=(Get-FileHash -LiteralPath $license -Algorithm SHA256).Hash
            if ($licenseHash -cne $manifest.ultratex.license_sha256) {
                throw 'The packaged FLUX.2 [klein] 9B model terms could not be verified. Use the complete Setup package.'
            }
            $meshLibRevision=$manifest.native_workflows.meshlib_license_revision
            if ($meshLibRevision -cnotmatch '^[0-9a-f]{40}$') {
                throw 'The pinned MeshLib terms could not be verified. Use the complete Setup package.'
            }
            if ($AcceptNativeWorkflowModelLicenses) {
                $setupState.licenses.native_workflows=$licenseHash
                $setupState.licenses.meshlib=$meshLibRevision
                Save-GuidedSetupJson $setupStatePath $setupState
            }
            if (-not $probe.NativeWorkflows.Supported) {
                $nativeWorkflowReason=$probe.NativeWorkflows.Status
            } elseif ($setupState.licenses.native_workflows -cne $licenseHash -and $setupState.licenses.ultratex -cne $licenseHash) {
                $nativeWorkflowReason='The FLUX.2 [klein] 9B model terms have not been accepted. Run Setup again to select High Detail and Game Asset.'
            } elseif ($setupState.licenses.meshlib -cne $meshLibRevision) {
                $nativeWorkflowReason='The MeshLib terms have not been accepted. Run Setup again to select High Detail and Game Asset.'
            }
        } catch { $nativeWorkflowReason=$_.Exception.Message }
    }
    if ($BlenderPath -and -not $probe.Blender.Ready) { throw $probe.Blender.Status }
    if ($probe.Blender.Ready) { $config.blender_path=$probe.Blender.Path }
    if ($probe.WSL.Ready) { $config.wsl_distribution=$probe.WSL.Distribution }
    if ($probe.Gpu.Supported -and @($selected | Where-Object {$_ -ne 'kimodo'}).Count) {
        if ($config.gpu_uuid -and $config.gpu_uuid -cne $probe.Gpu.Uuid) {
            foreach ($name in ($setupOrder | Where-Object {$_ -ne 'kimodo'})) {
                if ($setupState.components.ContainsKey($name)) {
                    Set-GuidedComponentResult $name 'deferred' 'GPU selection changed. Run Repair or resume components to verify it on the new GPU.'
                }
            }
        }
        $config.gpu_uuid=$probe.Gpu.Uuid
        $config.gpu_profile=$probe.Gpu.Profile
        if ($probe.Gpu.WindowsRuntime) { $config.windows_runtime=$probe.Gpu.WindowsRuntime }
        else { $config.Remove('windows_runtime') }
        if ($probe.Gpu.UniRigRuntime) { $config.unirig_runtime=$probe.Gpu.UniRigRuntime }
        else { $config.Remove('unirig_runtime') }
    }
    if ($config.Count) { Save-GuidedSetupJson $setupConfigPath $config }

    $windowsPowerShell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $bootstrapArgs=@('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $InstallRoot 'Install-Studio.ps1'),'-NoLaunch')
    $nativePython=Join-Path $InstallRoot '.venv\Scripts\python.exe'
    $pixalSetup=Join-Path $InstallRoot 'pixal3d_setup.py'
    $kimodoSetup=Join-Path $PSScriptRoot 'Install-Kimodo.ps1'
    $workshopInstall={Invoke-GuidedSetupCommand $windowsPowerShell ($bootstrapArgs+@('-WorkshopOnly'))}
    if ($NeedsWorkshop -or $setupState.components.workshop.status -ne 'ready') {
        Invoke-GuidedComponent 'workshop' $workshopInstall
        if ($setupState.components.workshop.status -ne 'ready') {
            throw 'Workshop preparation failed. See logs/component-setup.log and open Repair or resume components from the Start menu.'
        }
    }

    $animationArgs=@('-B',(Join-Path $InstallRoot 'installer\setup.py'),'--animations-only')
    $animationsInstall={Invoke-GuidedSetupCommand $nativePython $animationArgs}
    $animationsVerify={Invoke-GuidedSetupCommand $nativePython ($animationArgs+@('--verify-only'))}
    Invoke-GuidedComponent 'animations' $animationsInstall $animationsVerify

    if ($probe.Gpu.VramNotice) { Write-Host $probe.Gpu.VramNotice }
    $gpuReason=if ($probe.Gpu.Supported) {''} else {$probe.Gpu.Status}
    $nativeReason=$gpuReason
    if ('trellis' -in $selected -and -not $nativeReason) {
        $license=Join-Path $InstallRoot 'licenses\DINOv3-LICENSE.md'
        $licenseHash=(Get-FileHash -LiteralPath $license -Algorithm SHA256).Hash
        if ($setupState.licenses.dinov3 -cne $licenseHash) {
            Write-Host "TRELLIS and Pixal3D use DINOv3. Read the model license at $license."
            Write-Host 'https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md'
            if ((Read-Host 'Type ACCEPT after reading the DINOv3 license') -ceq 'ACCEPT') {
                $setupState.licenses.dinov3=$licenseHash
                Save-GuidedSetupJson $setupStatePath $setupState
            } else { $nativeReason='The DINOv3 license has not been accepted.' }
        }
    }
    if ('trellis' -in $selected) {
        $nativeInstall={Invoke-GuidedSetupCommand $windowsPowerShell ($bootstrapArgs+@('-SkipRig','-AcceptDinoV3License'))}
        $nativeVerify={Invoke-GuidedSetupCommand $windowsPowerShell ($bootstrapArgs+@('-SkipRig','-VerifyOnly'))}
        Invoke-GuidedComponent 'trellis' $nativeInstall $nativeVerify $nativeReason
    }
    if ('topos' -in $selected) {
        $toposReason=$gpuReason
        if (-not $toposReason -and $setupState.components.trellis.status -ne 'ready') {
            $toposReason='Verify TRELLIS.2 generation on the selected GPU first.'
        }
        $toposSetup=Join-Path $InstallRoot 'installer\install_topos.py'
        $toposInstall={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$toposSetup)}
        $toposVerify={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$toposSetup,'--verify-only')}
        Invoke-GuidedComponent 'topos' $toposInstall $toposVerify $toposReason
    }
    if ('native-workflows' -in $selected) {
        Write-Host 'Preparing the standalone High Detail and Game Asset workflows with native UltraTex. The optional six-view UltraTex preview is a separate component.'
        if (-not $nativeWorkflowReason -and $setupState.components.trellis.status -ne 'ready') {
            $nativeWorkflowReason='Verify TRELLIS.2 generation on the selected GPU first.'
        }
        $nativeWorkflowSetup=Join-Path $InstallRoot 'installer\install_native_workflows.py'
        $nativeWorkflowArgs=@('-B','-u',$nativeWorkflowSetup,'--root',$InstallRoot,
            '--accept-ultratex-license','--accept-meshlib-license')
        $nativeWorkflowInstall={Invoke-GuidedSetupCommand $nativePython $nativeWorkflowArgs}
        $nativeWorkflowVerify={Invoke-GuidedSetupCommand $nativePython ($nativeWorkflowArgs+@('--verify-only'))}
        Invoke-GuidedComponent 'native-workflows' $nativeWorkflowInstall $nativeWorkflowVerify $nativeWorkflowReason
    }
    if ('pixal3d' -in $selected) {
        $pixalReason=if ($setupState.components.trellis.status -ne 'ready') {'Verify TRELLIS generation on the selected GPU first.'} else {''}
        $pixalInstall={Invoke-GuidedSetupCommand $nativePython @('-u',$pixalSetup,'--accept-dinov3-license')}
        $pixalVerify={Invoke-GuidedSetupCommand $nativePython @('-u',$pixalSetup,'--verify-only')}
        Invoke-GuidedComponent 'pixal3d' $pixalInstall $pixalVerify $pixalReason
    }

    if ('hi3d' -in $selected) {
        $hi3dReason=if ($probe.Hi3D.Supported) {''} else {$probe.Hi3D.Status}
        if ($probe.Hi3D.VramNotice) { Write-Host $probe.Hi3D.VramNotice }
        if (-not $hi3dReason -and $setupState.components.trellis.status -ne 'ready') {
            $hi3dReason='Verify TRELLIS.2 generation on the selected GPU first.'
        }
        $hi3dLicense=Join-Path $InstallRoot 'licenses\Hi3D-1.0-LICENSE.md'
        $hi3dHash=(Get-FileHash -LiteralPath $hi3dLicense -Algorithm SHA256).Hash
        if ($AcceptHi3dModelLicenses) {
            $setupState.licenses.hi3d=$hi3dHash
            Save-GuidedSetupJson $setupStatePath $setupState
        }
        if (-not $hi3dReason -and $setupState.licenses.hi3d -cne $hi3dHash) {
            $hi3dReason='The Hi3D, DINOv2 and CLIP terms have not been accepted. Run Setup and select Hi3D again.'
        }
        $hi3dSetup=Join-Path $InstallRoot 'installer\install_hi3d.py'
        $hi3dInstall={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$hi3dSetup,'--accept-model-licenses','--migrate-default-storage')}
        $hi3dVerify={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$hi3dSetup,'--verify-only','--migrate-default-storage')}
        Invoke-GuidedComponent 'hi3d' $hi3dInstall $hi3dVerify $hi3dReason
    }

    if ('multiview-texture' -in $selected) {
        $textureReason=if ($probe.MultiviewTexture.Supported) {''} else {$probe.MultiviewTexture.Status}
        if ($probe.MultiviewTexture.VramNotice) { Write-Host $probe.MultiviewTexture.VramNotice }
        if (-not $textureReason -and $setupState.components.trellis.status -ne 'ready') {
            $textureReason='Verify TRELLIS.2 generation on the selected GPU first.'
        }
        $textureLicense=Join-Path $InstallRoot 'licenses\Stable-Diffusion-2.1-OpenRAIL++-M.txt'
        $textureHash=(Get-FileHash -LiteralPath $textureLicense -Algorithm SHA256).Hash
        if ($AcceptMultiviewTextureModelLicenses) {
            $setupState.licenses.multiview_texture=$textureHash
            Save-GuidedSetupJson $setupStatePath $setupState
        }
        if (-not $textureReason -and $setupState.licenses.multiview_texture -cne $textureHash) {
            $textureReason='The Stable Diffusion 2.1 and MV-Adapter terms have not been accepted. Run Setup and select multiview texture refinement.'
        }
        $textureSetup=Join-Path $InstallRoot 'installer\install_multiview_texture.py'
        $textureInstall={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$textureSetup,'--accept-model-licenses')}
        $textureVerify={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$textureSetup,'--verify-only')}
        Invoke-GuidedComponent 'multiview-texture' $textureInstall $textureVerify $textureReason
    }

    if ('ultratex' -in $selected) {
        $ultraTexReason=if ($probe.UltraTex.Supported) {''} else {$probe.UltraTex.Status}
        Write-Host $probe.UltraTex.Guidance
        if (-not $ultraTexReason -and $setupState.components.trellis.status -ne 'ready') {
            $ultraTexReason='Verify TRELLIS.2 generation on the selected GPU first.'
        }
        if (-not $ultraTexReason) {
            try {
                $ultraTexLicense=Join-Path $InstallRoot 'licenses\FLUX.2-klein-base-9B-LICENSE.md'
                $ultraTexHash=(Get-FileHash -LiteralPath $ultraTexLicense -Algorithm SHA256).Hash
                if ($ultraTexHash -cne (Read-SetupGpuManifest).ultratex.license_sha256) {
                    throw 'The packaged FLUX.2 [klein] 9B model terms could not be verified. Use the complete Setup package.'
                }
                if ($AcceptUltraTexModelLicenses) {
                    $setupState.licenses.ultratex=$ultraTexHash
                    Save-GuidedSetupJson $setupStatePath $setupState
                }
                if ($setupState.licenses.ultratex -cne $ultraTexHash) {
                    $ultraTexReason='The FLUX.2 [klein] 9B model terms have not been accepted. Run Setup again to select UltraTex.'
                }
            } catch { $ultraTexReason=$_.Exception.Message }
        }
        $ultraTexSetup=Join-Path $InstallRoot 'installer\install_ultratex.py'
        $ultraTexInstall={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$ultraTexSetup,'--accept-model-licenses')}
        $ultraTexVerify={Invoke-GuidedSetupCommand $nativePython @('-B','-u',$ultraTexSetup,'--verify-only')}
        Invoke-GuidedComponent 'ultratex' $ultraTexInstall $ultraTexVerify $ultraTexReason
    }

    if ('unirig' -in $selected) {
        $rigReason=$gpuReason
        if (-not $rigReason -and -not $probe.Gpu.UniRigSupported) {
            $rigReason=if ($probe.Gpu.UniRigStatus) { $probe.Gpu.UniRigStatus } else { 'This test build has no validated UniRig wheels for the selected GPU.' }
        }
        if (-not $rigReason -and -not $probe.Blender.Ready) { $rigReason=$probe.Blender.Status }
        if (-not $rigReason -and -not $probe.WSL.Ready) { $rigReason=$probe.WSL.Status }
        if (-not $rigReason) {
            $wsl=(Get-Command wsl.exe -ErrorAction SilentlyContinue).Source
            if (-not $wsl) { $rigReason='WSL is unavailable. Install and initialize Ubuntu 22.04 under WSL2, then resume UniRig.' }
            else {
                $test=Invoke-SetupProcess -FilePath $wsl -Arguments @('-d',$probe.WSL.Distribution,'--exec','true') -TimeoutSeconds 20
                if ($test.ExitCode -ne 0) { $rigReason='Open and finish the first-run setup of the selected WSL2 distribution, then resume UniRig.' }
            }
        }
        $rigArgs=$bootstrapArgs+@('-RigOnly','-WslDistribution',$probe.WSL.Distribution)
        $rigInstall={Invoke-GuidedSetupCommand $windowsPowerShell $rigArgs}
        $rigVerify={Invoke-GuidedSetupCommand $windowsPowerShell ($rigArgs+@('-VerifyOnly'))}
        Invoke-GuidedComponent 'unirig' $rigInstall $rigVerify $rigReason
    }
    if ('kimodo' -in $selected) {
        $kimodoHash=(Get-FileHash -LiteralPath (Join-Path $InstallRoot 'installer\kimodo-manifest.json') -Algorithm SHA256).Hash
        if ($AcceptKimodoModelLicenses) {
            $setupState.licenses.kimodo=$kimodoHash
            Save-GuidedSetupJson $setupStatePath $setupState
        }
        $kimodoArgs=@('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',$kimodoSetup,'-InstallRoot',$InstallRoot)
        if ($setupState.licenses.kimodo -ceq $kimodoHash) { $kimodoArgs+=@('-AcceptModelLicenses') }
        Invoke-GuidedComponent 'kimodo' {
            Invoke-GuidedSetupCommand $windowsPowerShell $kimodoArgs
            $kimodo=Read-GuidedSetupJson (Join-Path $InstallRoot 'config\generated-animation-runtime.json')
            if (-not $kimodo.enabled) { throw 'Kimodo is not enabled in the existing runtime settings.' }
            $setupState.licenses.kimodo=$kimodoHash
        }
    }
    $pending=@((@('animations')+$selected) | Where-Object {$setupState.components[$_].status -ne 'ready'})
    if ($pending.Count) {
        Write-Host ('Components to resume: '+($pending -join ', ')+'. Open Repair or resume components from the Start menu.')
        return 3
    }
    Write-Host 'The selected components are ready.'
    return 0
}
