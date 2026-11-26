param(
    [Alias('Action')][ValidateSet('Probe','ValidateBlender')][string]$PreflightAction='Probe',
    [Alias('OutputPath')][string]$PreflightOutputPath,
    [Alias('InstallRoot')][string]$PreflightInstallRoot,
    [Alias('BlenderPath')][string]$PreflightBlenderPath,
    [Alias('GpuUuid')][string]$PreflightGpuUuid,
    [Alias('WslDistribution')][string]$PreflightWslDistribution
)

function ConvertTo-SetupProcessArgument([string]$Argument) {
    # WSL parses its host options from the raw command line and expects bare flags.
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }
    # Quote empty arguments and arguments with spaces or quotes for Windows argv.
    $escaped=[regex]::Replace($Argument, '(\\*)"', '$1$1\"')
    return '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
}

function Invoke-SetupProcess {
    param([string]$FilePath, [string[]]$Arguments, [ValidateRange(1,600)][int]$TimeoutSeconds=8)

    $process=New-Object System.Diagnostics.Process
    $process.StartInfo.FileName=$FilePath
    $process.StartInfo.Arguments=($Arguments | ForEach-Object { ConvertTo-SetupProcessArgument $_ }) -join ' '
    $process.StartInfo.UseShellExecute=$false
    $process.StartInfo.RedirectStandardOutput=$true
    $process.StartInfo.RedirectStandardError=$true
    $process.StartInfo.CreateNoWindow=$true
    try {
        [void]$process.Start()
        $stdout=$process.StandardOutput.ReadToEndAsync()
        $stderr=$process.StandardError.ReadToEndAsync()
        $deadline=[Diagnostics.Stopwatch]::StartNew()
        $exited=$process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $exited -and -not $process.HasExited) {
            try { $process.Kill() } catch [InvalidOperationException] { }
        }
        $remaining=[Math]::Max(0, $TimeoutSeconds * 1000 - [int]$deadline.ElapsedMilliseconds)
        if (-not $exited -or -not [Threading.Tasks.Task]::WaitAll(@($stdout,$stderr), $remaining)) {
            return [pscustomobject]@{ExitCode=124; Output=''; Error='The check timed out.'; TimedOut=$true}
        }
        return [pscustomobject]@{ExitCode=$process.ExitCode; Output=$stdout.Result; Error=$stderr.Result; TimedOut=$false}
    } catch {
        return [pscustomobject]@{ExitCode=1; Output=''; Error=$_.Exception.Message; TimedOut=$false}
    } finally {
        $process.Dispose()
    }
}

function Read-SetupJson([string]$Root, [string]$RelativePath) {
    if ($Root) {
        try {
            return Get-Content -LiteralPath (Join-Path $Root $RelativePath) -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        } catch { }
    }
    return $null
}

function Read-SetupGpuManifest {
    # NSIS extracts the manifest beside the temporary script before copying the app.
    $manifestPath=Join-Path $PSScriptRoot 'gpu-runtime-profiles.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        $manifestPath=Join-Path $PSScriptRoot '..\gpu-runtime-profiles.json'
    }
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'Setup GPU runtime manifest is missing. Run a complete Setup package.'
    }
    try {
        $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Setup GPU runtime manifest could not be read. Run a complete Setup package.'
    }
    if ($manifest.schema -ne 1 -or $manifest.gpus -isnot [pscustomobject] -or
        $manifest.windows -isnot [pscustomobject] -or $manifest.unirig -isnot [pscustomobject]) {
        throw 'Setup GPU runtime manifest has an unsupported format. Run a complete Setup package.'
    }
    return $manifest
}

function Get-SetupGpu([string]$RequestedUuid, [switch]$AllowReselect) {
    $unavailable=[pscustomobject][ordered]@{
        Name=''; MemoryMiB=0; Driver=''; Compute=''; Uuid=''; Profile=''; WindowsRuntime=''; UniRigRuntime=''
        Supported=$false; UniRigSupported=$false; UniRigStatus=''; Experimental=$false; VramNotice=''
        Status='No NVIDIA CUDA GPU was detected. Workshop is available.'
    }
    $nativeSystemDir=if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        'Sysnative'
    } else {
        'System32'
    }
    $smi=Join-Path $env:SystemRoot (Join-Path $nativeSystemDir 'nvidia-smi.exe')
    if (-not (Test-Path -LiteralPath $smi -PathType Leaf)) {
        $smi=(Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    }
    if (-not $smi) {
        $unavailable.Status='The NVIDIA driver utility (nvidia-smi) was not found. Workshop is available.'
        return $unavailable
    }
    $probe=Invoke-SetupProcess -FilePath $smi -Arguments @('--query-gpu=uuid,name,memory.total,driver_version,compute_cap','--format=csv,noheader,nounits')
    if ($probe.ExitCode -ne 0) {
        $unavailable.Status='The NVIDIA detection check failed. Check the Windows NVIDIA driver.'
        return $unavailable
    }
    $manifest=$null
    $manifestError=''
    try { $manifest=Read-SetupGpuManifest } catch { $manifestError=$_.Exception.Message }
    $gpus=@(foreach ($line in $probe.Output -split '\r?\n') {
        $parts=@($line -split ',' | ForEach-Object { $_.Trim() })
        if ($parts.Count -ne 5 -or $parts[0] -notmatch '^GPU-[0-9a-f-]{36}$') { continue }
        $memory=0
        $driver=$null
        [void][int]::TryParse($parts[2], [ref]$memory)
        $driverKnown=[version]::TryParse($parts[3], [ref]$driver)
        $profile=''
        $windowsRuntime=''
        $unirigRuntime=''
        $supported=$false
        $unirigSupported=$false
        $unirigStatus=''
        $experimental=$false
        $vramNotice=''
        if ($manifestError) {
            $status=$manifestError
        } else {
            $plan=$manifest.gpus.PSObject.Properties[$parts[4]].Value
            if (-not $plan) {
                $status='No test runtime is available for this GPU architecture.'
            } else {
                $profile=[string]$plan.id
                $experimental=$plan.experimental -eq $true
                $windowsRuntime=[string]$plan.windows
                $unirigRuntime=[string]$plan.unirig
                $windowsPlan=$manifest.windows.PSObject.Properties[$windowsRuntime].Value
                $unirigPlan=$manifest.unirig.PSObject.Properties[$unirigRuntime].Value
                $driverFloor=$null
                if (-not $profile -or ($windowsRuntime -and ($windowsPlan -isnot [pscustomobject] -or -not [version]::TryParse($windowsPlan.driver_min, [ref]$driverFloor)))) {
                    $status='The Setup GPU runtime manifest has an invalid Windows profile for this GPU.'
                } elseif (-not $windowsRuntime) {
                    $status='Windows GPU packages are unvalidated. Workshop is available. Kimodo uses the CPU.'
                } elseif (-not $driverKnown -or $driver -lt $driverFloor) {
                    $status="Update the NVIDIA Windows driver to $driverFloor or newer for $windowsRuntime."
                } else {
                    $supported=$true
                    if ($memory -gt 0 -and $memory -lt [int]$windowsPlan.reference_vram_mib) {
                        $vramNotice='Below the 12 GB VRAM reference. Try TRELLIS.2 at 512. Pixal3D may run out of memory.'
                    }
                    if ($experimental) { $status="$windowsRuntime is experimental. Setup will test the GPU kernels." }
                    else { $status="Setup will verify $windowsRuntime (Windows)." }
                }

                if ($unirigPlan -is [pscustomobject]) {
                    # A UniRig-only profile can specify its own Windows driver floor.
                    $unirigDriverFloor=$driverFloor
                    if ($unirigPlan.driver_min) {
                        $unirigDriverFloor=$null
                        [void][version]::TryParse($unirigPlan.driver_min, [ref]$unirigDriverFloor)
                    }
                    if (-not $unirigDriverFloor) {
                        $unirigStatus='The Setup GPU runtime manifest has an invalid driver floor for UniRig.'
                    } elseif (-not $driverKnown -or $driver -lt $unirigDriverFloor) {
                        $unirigStatus="Update the NVIDIA Windows driver to $unirigDriverFloor or newer for UniRig."
                    } else {
                        $unirigSupported=$true
                        $unirigStatus="Setup will verify $unirigRuntime (UniRig)."
                        if ($unirigPlan.experimental -eq $true) {
                            $unirigStatus='UniRig on RTX 50 is experimental. Setup tests the CUDA operations before downloading its models.'
                        }
                        if ($supported) { $status+=" UniRig uses $unirigRuntime." }
                    }
                } elseif ($unirigRuntime) {
                    $unirigStatus='The Setup GPU runtime manifest has an invalid reference for UniRig.'
                } elseif ($profile) {
                    $unirigStatus="No tested UniRig package is available for $profile in this Setup."
                }
            }
        }
        [pscustomobject][ordered]@{
            Name=$parts[1]; MemoryMiB=$memory; Driver=$parts[3]; Compute=$parts[4]; Uuid=$parts[0]; Profile=$profile
            WindowsRuntime=$windowsRuntime; UniRigRuntime=$unirigRuntime
            Supported=$supported; UniRigSupported=$unirigSupported; Experimental=$experimental; VramNotice=$vramNotice
            UniRigStatus=if ($unirigStatus) { $unirigStatus } else { $status }
            Status=$status
        }
    })
    if ($RequestedUuid) {
        $selected=$gpus | Where-Object Uuid -EQ $RequestedUuid | Select-Object -First 1
        if ($selected) { return $selected }
        if (-not $AllowReselect) {
            $unavailable.Status='The selected NVIDIA GPU is unavailable. Run detection again.'
            return $unavailable
        }
    }
    if (-not $gpus.Count) { return $unavailable }
    return $gpus | Sort-Object -Property @{Expression='Supported';Descending=$true},@{Expression='MemoryMiB';Descending=$true},Uuid | Select-Object -First 1
}

function Get-SetupHi3d([psobject]$Gpu) {
    $result=[pscustomobject][ordered]@{Supported=$false; Status=''; VramNotice=''}
    if (-not $Gpu.Supported) {
        $result.Status=$Gpu.Status
        return $result
    }
    try { $policy=(Read-SetupGpuManifest).hi3d }
    catch { $result.Status=$_.Exception.Message; return $result }
    if (-not $policy -or -not $policy.compute_capabilities.Count -or
        [int]$policy.minimum_vram_mib -le 0 -or [int]$policy.recommended_1024_vram_mib -lt [int]$policy.minimum_vram_mib) {
        $result.Status='The Hi3D hardware policy is missing or invalid. Run a complete Setup package.'
        return $result
    }
    if ($Gpu.Compute -notin $policy.compute_capabilities) {
        $result.Status='This Hi3D package supports NVIDIA compute capabilities 8.6 and 8.9. Workshop and compatible engines remain available.'
        return $result
    }
    if ($Gpu.MemoryMiB -lt [int]$policy.minimum_vram_mib) {
        $result.Status='This Hi3D package requires at least 8 GB of detected VRAM. Other components can still be installed.'
        return $result
    }
    $result.Supported=$true
    $result.Status='Setup will test Hi3D CUDA kernels on the detected GPU before downloading model weights.'
    if ($Gpu.MemoryMiB -lt [int]$policy.recommended_1024_vram_mib) {
        $result.VramNotice='Less than 12 GB VRAM. Use Detail Refinement (512). The 1024 pass may run out of memory.'
    } else {
        $result.VramNotice='For 1024, 12 GB VRAM or more is recommended. Memory use varies with mesh detail and other GPU activity.'
    }
    return $result
}

function Test-SetupBlender([string]$Path) {
    $result=[pscustomobject][ordered]@{Path=''; Version=''; Ready=$false; Status='Blender can be configured later.'}
    if (-not $Path) { return $result }
    try {
        $candidate=Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($candidate.PSIsContainer -or $candidate.Name -ine 'blender.exe') { throw 'Choose blender.exe.' }
        $result.Path=$candidate.FullName
    } catch {
        $result.Status='Choose an existing blender.exe.'
        return $result
    }
    $probe=Invoke-SetupProcess -FilePath $result.Path -Arguments @('--version')
    if ($probe.ExitCode -ne 0 -or $probe.Output -notmatch '(?im)^Blender ([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b') {
        $result.Status='Blender could not report its version. Check the executable.'
        return $result
    }
    $result.Version=$Matches[1]
    $result.Ready=$true
    $result.Status="Blender $($result.Version) found."
    if ($result.Version -notmatch '^5\.2(?:\.|$)') { $result.Status+=" Reference version: 5.2." }
    return $result
}

function Find-SetupBlender([string]$SavedPath) {
    $paths=@($env:TRELLIS_BLENDER, $SavedPath)
    $paths+=@((Get-Command blender.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source)
    $roots=@($env:ProgramW6432, $env:ProgramFiles)
    $roots+=@(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Root -match '^[A-Za-z]:\\$' } | ForEach-Object {
        if ([IO.DriveInfo]::new($_.Root).DriveType -eq [IO.DriveType]::Fixed) {
            $_.Root
            Join-Path $_.Root 'Program Files'
            Join-Path $_.Root 'Applications'
        }
    })
    foreach ($root in $roots | Where-Object { $_ } | Select-Object -Unique) {
        $paths+=@(Join-Path $root 'Blender\blender.exe')
        $paths+=@(Get-ChildItem -LiteralPath (Join-Path $root 'Blender Foundation') -Directory -Filter 'Blender *' -ErrorAction SilentlyContinue |
            Sort-Object -Property @{Expression={ $_.Name -like 'Blender 5.2*' };Descending=$true},@{Expression='Name';Descending=$true} |
            ForEach-Object { Join-Path $_.FullName 'blender.exe' })
    }
    # Validate the preferred executable once. Setup can browse to a different copy.
    $found=$paths | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf -ErrorAction SilentlyContinue) } | Select-Object -First 1
    if ($found) { return Test-SetupBlender -Path $found }
    $result=Test-SetupBlender -Path ''
    $result.Status='Blender was not found. Browse to blender.exe or configure it later.'
    return $result
}

function Get-SetupWsl([string]$RequestedDistribution) {
    $registry='HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
    $distro=if ($RequestedDistribution) { $RequestedDistribution } else { 'Ubuntu-22.04' }
    $result=[pscustomobject][ordered]@{Distribution=$distro; Ready=$false; Status='Install and initialize Ubuntu 22.04 under WSL2 for UniRig.'}
    $registration=Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue | ForEach-Object {
        Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue
    } | Where-Object { $_.DistributionName -eq $distro } | Select-Object -First 1
    if ($registration.Version -eq 2 -and $distro -match '^[A-Za-z0-9_.-]{1,80}$') {
        $result.Ready=$true
        $result.Status="$distro is registered in WSL2. Setup will check it for UniRig."
    } elseif ($registration.Version -eq 1) {
        $result.Status="$distro uses WSL1. UniRig requires WSL2."
    }
    return $result
}

function Get-SetupMultiviewTexture([psobject]$Gpu) {
    $result=[pscustomobject][ordered]@{Supported=$false; Status=''; VramNotice=''}
    if (-not $Gpu.Supported) {
        $result.Status=$Gpu.Status
        return $result
    }
    try { $policy=(Read-SetupGpuManifest).multiview_texture }
    catch { $result.Status=$_.Exception.Message; return $result }
    if (-not $policy -or -not $policy.compute_capabilities.Count -or
        [int]$policy.minimum_vram_mib -le 0 -or [int]$policy.recommended_vram_mib -lt [int]$policy.minimum_vram_mib) {
        $result.Status='The multiview texture hardware policy is missing or invalid. Use the complete Setup package.'
        return $result
    }
    if ($Gpu.Compute -notin $policy.compute_capabilities) {
        $result.Status='This multiview texture installer supports NVIDIA compute capabilities 8.6 and 8.9.'
        return $result
    }
    if ($Gpu.MemoryMiB -lt [int]$policy.minimum_vram_mib) {
        $result.Status='Multiview texture refinement requires at least 8 GB of detected VRAM.'
        return $result
    }
    $result.Supported=$true
    $result.Status='Setup will test FP16 CUDA on the selected GPU before downloading the model files.'
    if ($Gpu.MemoryMiB -lt [int]$policy.recommended_vram_mib) {
        $result.VramNotice='12 GB VRAM or more is recommended. Close other GPU-heavy applications if this pass runs out of memory.'
    }
    return $result
}

function Get-SetupUltraTex([psobject]$Gpu) {
    $result=[pscustomobject][ordered]@{
        Supported=$false; Status=''
        Guidance='Tested on NVIDIA Ada. Allow 32 GB of system RAM and 16 GiB of disk space in addition to TRELLIS.2.'
    }
    if (-not $Gpu.Supported) {
        $result.Status=$Gpu.Status
        return $result
    }
    try { $policy=(Read-SetupGpuManifest).ultratex }
    catch { $result.Status=$_.Exception.Message; return $result }
    if (-not $policy -or -not $policy.compute_capabilities.Count -or [int]$policy.minimum_vram_mib -le 0) {
        $result.Status='The UltraTex hardware policy is missing or invalid. Use the complete Setup package.'
        return $result
    }
    if ($Gpu.Compute -notin $policy.compute_capabilities) {
        $result.Status='UltraTex preview requires a supported NVIDIA Ampere, Ada or Blackwell GPU.'
        return $result
    }
    if ($Gpu.MemoryMiB -lt [int]$policy.minimum_vram_mib) {
        $result.Status='UltraTex preview requires a detected NVIDIA GPU with at least 12 GB of VRAM.'
        return $result
    }
    $result.Supported=$true
    $result.Status='NVIDIA GPU and 12 GB VRAM requirement met. Setup tests CUDA before downloading UltraTex.'
    return $result
}

function Get-SetupDisk([string]$Root) {
    $result=[pscustomobject][ordered]@{FreeGiB=''; Status='Windows disk space could not be checked.'}
    try {
        $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Root)))
        $result.FreeGiB=[Math]::Floor($drive.AvailableFreeSpace / 1GB)
        $result.Status="$($result.FreeGiB) GiB available on $($drive.Name)"
    } catch { }
    return $result
}

function Get-SetupNativeWorkflows([psobject]$Gpu) {
    $result=[pscustomobject][ordered]@{
        Supported=$false; Status=''; MeshLibLicenseUrl=''
        Guidance='Allow 75 GiB of free disk space for a fresh installation.'
    }
    if (-not $Gpu.Supported) {
        $result.Status=$Gpu.Status
        return $result
    }
    try { $policy=(Read-SetupGpuManifest).native_workflows }
    catch { $result.Status=$_.Exception.Message; return $result }
    if (-not $policy -or -not $policy.compute_capabilities.Count -or [int]$policy.minimum_vram_mib -le 0 -or
        $policy.meshlib_license_revision -cnotmatch '^[0-9a-f]{40}$') {
        $result.Status='The native workflows hardware policy is missing or invalid. Use the complete Setup package.'
        return $result
    }
    if ($Gpu.Compute -notin $policy.compute_capabilities -or $Gpu.MemoryMiB -lt [int]$policy.minimum_vram_mib) {
        $capabilities=$policy.compute_capabilities -join ', '
        $result.Status="The native workflows require a supported NVIDIA GPU (CUDA capability $capabilities) and at least $($policy.minimum_vram_mib) MiB of VRAM."
        return $result
    }
    $result.MeshLibLicenseUrl="https://github.com/MeshInspector/MeshLib/blob/$($policy.meshlib_license_revision)/LICENSE"
    $result.Supported=$true
    $result.Status='The detected GPU meets the requirements. Setup checks the runtimes on this GPU.'
    return $result
}

function Get-SetupInstallPlan([string]$Root) {
    $state=Read-SetupJson -Root $Root -RelativePath 'config\component-install.json'
    $saved=$state.schema -eq 1 -and $state.requested -is [array]
    $existingInstallation=$Root -and (Read-SetupJson -Root $Root -RelativePath 'bundled-web.json')
    $requested=if ($saved) {
        $state.requested
    } elseif ($existingInstallation) {
        # Earlier Windows installers did not record their component choices.
        if ((Test-Path -LiteralPath (Join-Path $Root 'models/trellis2/pipeline.json') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $Root 'engine/MODELS/dinov3/model.safetensors') -PathType Leaf)) {
            'trellis'
            if (Test-Path -LiteralPath (Join-Path $Root 'models/pixal3d/verified.json') -PathType Leaf) { 'pixal3d' }
        }
        if ((Test-Path -LiteralPath (Join-Path $Root 'UniRig/models/skeleton.ckpt') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $Root 'UniRig/models/skin.ckpt') -PathType Leaf)) {
            'unirig'
        }
    }
    $hi3dAccepted=$false
    if ($state.licenses.hi3d) {
        try { $hi3dAccepted=$state.licenses.hi3d -ceq (Read-SetupGpuManifest).hi3d.license_sha256 } catch { }
    }
    $multiviewAccepted=$false
    if ($state.licenses.multiview_texture) {
        try { $multiviewAccepted=$state.licenses.multiview_texture -ceq (Read-SetupGpuManifest).multiview_texture.license_sha256 } catch { }
    }
    $ultraTexAccepted=$false
    if ($state.licenses.ultratex) {
        try { $ultraTexAccepted=$state.licenses.ultratex -ceq (Read-SetupGpuManifest).ultratex.license_sha256 } catch { }
    }
    $nativeWorkflowsAccepted=$false
    if ($state.licenses.meshlib) {
        try {
            $manifest=Read-SetupGpuManifest
            $nativeWorkflowsAccepted=($ultraTexAccepted -or
                $state.licenses.native_workflows -ceq $manifest.ultratex.license_sha256) -and
                $state.licenses.meshlib -ceq $manifest.native_workflows.meshlib_license_revision
        } catch { }
    }
    return [pscustomobject][ordered]@{
        Saved=$saved
        NativeWorkflowsDefault=(-not $saved -and -not $existingInstallation)
        NativeWorkflows='native-workflows' -in $requested
        NativeWorkflowsLicenseAccepted=$nativeWorkflowsAccepted
        Trellis='trellis' -in $requested
        Topos='topos' -in $requested
        Pixal3D='pixal3d' -in $requested
        UniRig='unirig' -in $requested
        Hi3D='hi3d' -in $requested
        Hi3DLicenseAccepted=$hi3dAccepted
        MultiviewTexture='multiview-texture' -in $requested
        MultiviewTextureLicenseAccepted=$multiviewAccepted
        UltraTex='ultratex' -in $requested
        UltraTexLicenseAccepted=$ultraTexAccepted
        Kimodo='kimodo' -in $requested
    }
}

function Get-SetupPreflight {
    param([string]$InstallRoot, [string]$BlenderPath, [string]$GpuUuid, [string]$WslDistribution)

    $config=Read-SetupJson -Root $InstallRoot -RelativePath 'config\runtime.json'
    $automaticGpu=-not $GpuUuid
    if ($automaticGpu) { $GpuUuid=[string]$config.gpu_uuid }
    if (-not $WslDistribution) { $WslDistribution=[string]$config.wsl_distribution }
    $blender=if ($PSBoundParameters.ContainsKey('BlenderPath')) { Test-SetupBlender -Path $BlenderPath } else { Find-SetupBlender -SavedPath ([string]$config.blender_path) }
    $gpu=Get-SetupGpu -RequestedUuid $GpuUuid -AllowReselect:$automaticGpu
    return [pscustomobject][ordered]@{
        GPU=$gpu
        NativeWorkflows=(Get-SetupNativeWorkflows -Gpu $gpu)
        Hi3D=(Get-SetupHi3d -Gpu $gpu)
        MultiviewTexture=(Get-SetupMultiviewTexture -Gpu $gpu)
        UltraTex=(Get-SetupUltraTex -Gpu $gpu)
        Blender=$blender
        WSL=(Get-SetupWsl -RequestedDistribution $WslDistribution)
        Disk=(Get-SetupDisk -Root $InstallRoot)
        Plan=(Get-SetupInstallPlan -Root $InstallRoot)
    }
}

function Write-SetupIni([string]$Path, [psobject]$Data) {
    $lines=foreach ($section in $Data.PSObject.Properties) {
        "[$($section.Name)]"
        foreach ($property in $section.Value.PSObject.Properties) {
            $value=if ($property.Value -is [bool]) { [int]$property.Value } else { ([string]$property.Value) -replace '[\r\n\x00]', ' ' }
            "$($property.Name)=$value"
        }
        ''
    }
    [IO.File]::WriteAllLines($Path, [string[]]$lines, [Text.Encoding]::Unicode)
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference='Stop'
    if (-not $PreflightOutputPath) { throw 'Pass -OutputPath for the Setup INI result.' }
    if ($PreflightAction -eq 'ValidateBlender') {
        Write-SetupIni -Path $PreflightOutputPath -Data ([pscustomobject]@{Blender=(Test-SetupBlender -Path $PreflightBlenderPath)})
    } else {
        $options=@{}
        foreach ($key in @('InstallRoot','BlenderPath','GpuUuid','WslDistribution')) {
            $parameter='Preflight'+$key
            if ($PSBoundParameters.ContainsKey($parameter)) { $options[$key]=$PSBoundParameters[$parameter] }
        }
        Write-SetupIni -Path $PreflightOutputPath -Data (Get-SetupPreflight @options)
    }
}
