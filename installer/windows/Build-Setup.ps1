param(
    [string]$Compiler,
    [string]$OutputDirectory,
    [string]$Python = 'python',
    [switch]$KeepStage,
    [switch]$Preview
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutputDirectory) {
    $OutputDirectory=Join-Path $taskRoot $(if ($Preview) {'Builds\Windows\Preview'} else {'Builds\Windows'})
}
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if (-not $Compiler) {
    $taskOnPath=Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($taskOnPath) { $Compiler=$taskOnPath.Source }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf)) { throw 'Pass the path to NSIS makensis.exe with -Compiler.' }
$Compiler=(Resolve-Path -LiteralPath $Compiler).Path
& $Python -B (Join-Path $taskRoot 'installer\audit_public.py') --root $taskRoot
if ($LASTEXITCODE -ne 0) { throw 'Public source audit failed. No installer was produced.' }
$taskPackage=Get-Content -LiteralPath (Join-Path $taskRoot 'package.json') -Raw | ConvertFrom-Json
if ($taskPackage.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a numeric major.minor.patch version.' }

# Use the public source allowlist, then include only runtime/documentation trees.
$taskRoots=@('web','installer','runtime','licenses','animations','unity','UniRig','integrations','native_workflows','native_ultratex')
$taskRootFiles=@('LICENSE','README.md','INSTALLATION.md','SECURITY.md','THIRD_PARTY_NOTICES.md','package.json','package-lock.json')
$taskDocFiles=@('docs/API.md','docs/MCP.md','docs/DEPENDENCIES.json','docs/WINDOWS_SETUP.md','docs/SERVER.md',
    'docs/MOTION_GENERATION.md','docs/WTIVO.md','docs/HI3D.md','docs/ULTRATEX.md','docs/NATIVE_PROFILES.md',('docs/RELEASE_'+$taskPackage.version+'.md'))
$taskFiles=@(git -C $taskRoot -c core.quotePath=false ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Could not read the TRELLIS Studio public source allowlist.' }
$taskFiles=@($taskFiles | Sort-Object -Unique | Where-Object {
    $parts=$_ -split '/'
    ($parts.Count -eq 1 -and $_ -cne 'comfy_profiles.py' -and ($_ -match '\.(py|cmd|ps1)$' -or $taskRootFiles -contains $_ -or $_ -match '^requirements-(windows|mcp-lock|mcp)\.txt$')) -or
    ($parts.Count -gt 1 -and ($taskRoots -ccontains $parts[0] -or $_ -in $taskDocFiles -or
        $_ -cin @('comfy_workflows/game-asset-character-05.json','comfy_workflows/PIXELARTISTRY-MIT-LICENSE.txt') -or
        $_ -cmatch '^third_party/assetfurnace/image_to_3dlab/(__init__|photo_paint|view_align)\.py$'))
})
$taskRequired=@('app.py','package.json','installer/setup.py','installer/windows/check-idle.ps1','installer/windows/Launch.cmd','installer/windows/Launch-Installed.ps1','installer/windows/Manage-Shortcuts.ps1','installer/windows/trellis-studio.ico','Start-Studio.ps1','web/studio.html','animations/library.json','unity/TrellisPackageImporter.cs')
$taskRequired+=@('server_app.py','server_runtime.py','server_store.py','server_setup.py','Start-Server.ps1','Stop-Server.ps1','Open TRELLIS Server.cmd',
    'installer/windows/Configure-Server.ps1','web/server.html','web/server.css','web/server.js','web/server-shell.js','docs/SERVER.md')
$taskRequired+=@('installer/windows/StudioTray.cs','installer/windows/Build-Tray.ps1','installer/windows/Server-Control.ps1',
    'installer/windows/Tray-Control.ps1','installer/windows/Configure-Firewall.ps1','installer/install_caddy.py','installer/caddy-manifest.json','installer/audit_public.py')
$taskRequired+=@('generated_animations.py','generated_animation_api.py','generated_animation_runtime.py',
    'generated_animation_runner.py','generated_animation_worker.py','generated_motion.py',
    'web/personal-animation-library.js','web/personal-animation-previews.js','web/personal-animation.css',
    'web/generation-wait.js','web/generation-wait.css','docs/MOTION_GENERATION.md')
$taskRequired+=@('Install Kimodo.cmd','installer/install_kimodo.py','installer/kimodo-manifest.json',
    'installer/kimodo_install_common.py','installer/kimodo_install_linux.py','installer/windows/Install-Kimodo.ps1')
$taskRequired+=@('texture_projection.py','baked_game_copy.py','game_bake_blender.py','model_finishing_api.py',
    'model_finishing_worker.py','web/texture-projection.js','web/texture-projection-geometry.js',
    'web/texture-projection-auto-align.js','web/texture-projection.css',('docs/RELEASE_'+$taskPackage.version+'.md'))
$taskRequired+=@('web/game-copy-regions.js','web/reduction-region-preview.js')
$taskRequired+=@('wtivo_runtime.py','docs/WTIVO.md','master_mesh.py','master_mesh_blender.py','web/hd-master-finishing.js')
$taskRequired+=@('hi3d_runtime.py','hi3d_worker.py','hi3d_attention.py','docs/HI3D.md',
    'installer/install_hi3d.py','installer/hi3d-manifest.json','installer/requirements-hi3d.txt',
    'licenses/Hi3D-1.0-LICENSE.md')
$taskRequired+=@('multiview_texture_runtime.py','multiview_texture_views.py','multiview_texture_fusion.py',
    'installer/install_multiview_texture.py','installer/multiview-texture-manifest.json',
    'installer/requirements-multiview-texture.txt','licenses/Stable-Diffusion-2.1-OpenRAIL++-M.txt')
$taskRequired+=@('studio_release.py','web/release-notification.css','web/release-notification.js')
$taskRequired+=@('native_high_detail.py','native_high_detail_quant.py','native_high_detail_portable.py',
    'native_workflow_options.py','native_workflows/high_detail_native.json','native_workflows/game_asset_native.json',
    'native_workflows/CITY96-APACHE-2.0.txt','docs/NATIVE_PROFILES.md')
$taskRequired+=@('native_pixal3d.py','native_pixal_layers.py','native_pixal_prepare.py','native_game_asset.py',
    'native_game_conditioning.py','native_game_sampling.py','native_game_finish.py','native_game_geometry.py','native_game_profile.py')
$taskRequired+=@('native_ultratex/options.py','native_ultratex/existing_mesh.py','native_ultratex/native_ultratex_models.py',
    'native_ultratex/native_ultratex_recipe.py','native_ultratex/prepare_character05.py','native_ultratex/run_character05_texture.py',
    'web/workflow-graph.js','web/workflow-topology.js','web/standard-workflow-graph.js')
$taskRequired+=@('rig_profiles_blender.py','creature_rig.py','creature_motion.py','creature_verification.py',
    'UniRig/apply-creature.py','web/creature-rig-editor.js','web/creature-rig-viewer.js',
    'creature_motion_edit.py','web/creature-motions.js')
$taskRequired+=@('collection_preview.py','collection_preview_renderer.py','web/collection-previews.js',
    'web/collection-preview-capture.js','web/collection-preview-render.js')
$taskRequired+=@('figure_master_repair.py','ultratex_runtime.py','ultratex_worker.py','web/texture-methods.js',
    'installer/install_ultratex.py','installer/ultratex-manifest.json','installer/requirements-ultratex-comfy.txt',
    'installer/requirements-ultratex-gguf.txt','licenses/FLUX.2-klein-base-9B-LICENSE.md','docs/ULTRATEX.md')
$taskRequired+=@('generation_view_projection.py','licenses/AssetFurnace-Apache-2.0.txt',
    'third_party/assetfurnace/image_to_3dlab/__init__.py',
    'third_party/assetfurnace/image_to_3dlab/photo_paint.py',
    'third_party/assetfurnace/image_to_3dlab/view_align.py')
$taskRequired+=@('gpu_runtime.py','installer/windows/Setup-Preflight.ps1',
    'installer/windows/Install-Components.ps1','installer/gpu-runtime-profiles.json',
    'UniRig/check-runtime.py','UniRig/runtime.sh','UniRig/runtime-profile.py',
    'installer/windows/trellis-welcome.bmp','installer/requirements-unirig-blackwell.txt',
    'installer/prepare_unirig_bpy.py','UniRig/run-skin-inference.py')
$taskRequired+=@('installer/install_native_workflows.py','installer/native_workflow_install_common.py',
    'installer/native_workflow_python.py','installer/install_native_high_detail.py',
    'installer/install_native_game_geometry.py','installer/install_native_ultratex.py',
    'installer/native-high-detail-manifest.json','installer/native-game-geometry-manifest.json',
    'installer/native-ultratex-manifest.json','installer/requirements-native-high-detail.txt',
    'installer/requirements-native-game-geometry.txt','installer/requirements-native-ultratex.txt',
    'installer/requirements-native-workflow-python.txt','comfy_workflows/game-asset-character-05.json',
    'comfy_workflows/PIXELARTISTRY-MIT-LICENSE.txt')
foreach($taskItem in $taskRequired) {
    if ($taskItem -cnotin $taskFiles) { throw "Required public payload is missing: $taskItem" }
}
$taskStage=Join-Path $taskRoot ('temp\windows-setup-'+[Guid]::NewGuid().ToString('N'))
$taskPayload=Join-Path $taskStage 'payload'
New-Item -ItemType Directory -Path $taskPayload,$OutputDirectory -Force | Out-Null
try {
    foreach ($taskFile in $taskFiles) {
        if ($taskFile -match '[\"$\x00-\x1f\\]' -or ($taskFile -split '/') -contains '..') { throw "Unsafe package entry: $taskFile" }
        $taskSource=Get-Item -LiteralPath (Join-Path $taskRoot $taskFile)
        if ($taskSource.PSIsContainer -or ($taskSource.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Package entry is not a regular file: $taskFile" }
        $taskDestination=Join-Path $taskPayload $taskFile
        New-Item -ItemType Directory -Path (Split-Path -Parent $taskDestination) -Force | Out-Null
        Copy-Item -LiteralPath $taskSource.FullName -Destination $taskDestination
    }
    # Compile generic tray sources and bundle only the verified upstream proxy files.
    & (Join-Path $taskRoot 'installer\windows\Build-Tray.ps1') -OutputPath (Join-Path $taskPayload 'TRELLIS Studio Tray.exe')
    & $Python -B (Join-Path $taskRoot 'installer\install_caddy.py') --ensure --destination (Join-Path $taskPayload 'bin\caddy')
    if ($LASTEXITCODE -ne 0) { throw 'The pinned HTTPS proxy could not be verified.' }
    $taskFiles+=@('TRELLIS Studio Tray.exe','bin/caddy/caddy.exe','bin/caddy/LICENSE','bin/caddy/README.md')
    # Node is a build requirement. The installer ships only the web assets used at runtime.
    Push-Location -LiteralPath $taskPayload
    try {
        & npm.cmd ci --omit=dev --ignore-scripts --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Could not assemble the browser dependencies.' }
        & npm.cmd run check
        if ($LASTEXITCODE -ne 0) { throw 'The web application syntax check failed.' }
    } finally { Pop-Location }
    $taskFiles=@($taskFiles) + @(Get-ChildItem -LiteralPath (Join-Path $taskPayload 'node_modules\three') -File -Recurse |
        ForEach-Object { $_.FullName.Substring($taskPayload.Length+1).Replace('\','/') }) +
        @('package.json','LICENSE','NOTICE','swagger-ui.css','swagger-ui-bundle.js','swagger-ui-bundle.js.LICENSE.txt' |
        ForEach-Object { 'node_modules/swagger-ui-dist/'+$_ })
    $taskFiles=@($taskFiles | Sort-Object -Unique)
    $taskAuditPaths=Join-Path $taskStage 'public-paths.txt'
    [IO.File]::WriteAllLines($taskAuditPaths,$taskFiles,[Text.UTF8Encoding]::new($false))
    & $Python -B (Join-Path $taskRoot 'installer\audit_public.py') --root $taskPayload --paths-file $taskAuditPaths
    if ($LASTEXITCODE -ne 0) { throw 'Public payload audit failed. No installer was produced.' }
    $taskChecks=[Collections.Generic.List[string]]::new()
    foreach($taskFile in $taskFiles) {
        $taskDigest=(Get-FileHash -LiteralPath (Join-Path $taskPayload $taskFile) -Algorithm SHA256).Hash.ToLowerInvariant()
        $taskChecks.Add($taskDigest+'  '+$taskFile)
    }
    $taskRevision=[IO.Path]::Combine($taskStage,'revision.txt')
    [IO.File]::WriteAllLines($taskRevision,$taskChecks,[Text.UTF8Encoding]::new($false))
    $taskRevisionHash=(Get-FileHash -LiteralPath $taskRevision -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $taskPayload 'bundled-web.json'),(@{ source_revision=$taskRevisionHash; version=$taskPackage.version } | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    $taskFiles+=@('bundled-web.json')
    $taskInstall=[Collections.Generic.List[string]]::new()
    $taskUninstall=[Collections.Generic.List[string]]::new()
    $taskDirectories=[Collections.Generic.HashSet[string]]::new()
    $taskLastDir=$null
    foreach($taskFile in $taskFiles) {
        $taskRelative=$taskFile.Replace('/','\')
        $taskDir=[IO.Path]::GetDirectoryName($taskRelative)
        if ($taskLastDir -ne $taskDir) {
            $taskInstall.Add('SetOutPath "$INSTDIR'+ $(if($taskDir){'\'+$taskDir}) + '"')
            $taskLastDir=$taskDir
        }
        $taskInstall.Add('File "${PAYLOAD_ROOT}\'+$taskRelative+'"')
        $taskUninstall.Add('Delete "$INSTDIR\'+$taskRelative+'"')
        while($taskDir) {
            [void]$taskDirectories.Add($taskDir)
            $taskDir=[IO.Path]::GetDirectoryName($taskDir)
        }
    }
    foreach($taskDir in ($taskDirectories | Sort-Object { $_.Length } -Descending)) { $taskUninstall.Add('RMDir "$INSTDIR\'+$taskDir+'"') }
    $taskInstallPath=Join-Path $taskStage 'install-files.nsh'
    $taskUninstallPath=Join-Path $taskStage 'uninstall-files.nsh'
    [IO.File]::WriteAllLines($taskInstallPath,$taskInstall,[Text.UTF8Encoding]::new($true))
    [IO.File]::WriteAllLines($taskUninstallPath,$taskUninstall,[Text.UTF8Encoding]::new($true))
    $taskSuffix=if ($Preview) {'-Setup-Test-x64.exe'} else {'-Setup-x64.exe'}
    $taskName='TRELLIS-Studio-'+$taskPackage.version+$taskSuffix
    $taskOut=Join-Path $taskStage $taskName
    $taskVariant=@()
    if ($Preview) { $taskVariant+='/DSETUP_PREVIEW' }
    & $Compiler /V3 /INPUTCHARSET UTF8 @taskVariant "/DOUTPUT_EXE=$taskOut" "/DPAYLOAD_ROOT=$taskPayload" "/DINSTALL_FILES=$taskInstallPath" "/DUNINSTALL_FILES=$taskUninstallPath" "/DSTUDIO_VERSION=$($taskPackage.version)" (Join-Path $PSScriptRoot 'StudioSetup.nsi')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $taskOut)) { throw 'NSIS did not produce the installer.' }
    $taskFinal=Join-Path $OutputDirectory $taskName
    Copy-Item -LiteralPath $taskOut -Destination $taskFinal
    $taskHash=(Get-FileHash -LiteralPath $taskFinal -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'SHA256SUMS.txt'),"$taskHash  $taskName`n",[Text.UTF8Encoding]::new($false))
    Copy-Item -LiteralPath $taskRevision -Destination (Join-Path $OutputDirectory 'payload-sha256.txt')
    Write-Output "Installer (unsigned): $taskFinal"
    if ($KeepStage) { Write-Output "Build stage: $taskStage" }
} finally {
    # This folder was allocated by this invocation. Preserve it on request for QA.
    if (-not $KeepStage -and (Test-Path -LiteralPath $taskStage)) {
        $taskChecked=(Resolve-Path -LiteralPath $taskStage).Path
        $taskParent=(Resolve-Path -LiteralPath (Join-Path $taskRoot 'temp')).Path
        if ((Split-Path -Parent $taskChecked) -ne $taskParent -or (Split-Path -Leaf $taskChecked) -notmatch '^windows-setup-[a-f0-9]{32}$') { throw 'Refusing to clean an unexpected build path.' }
        Remove-Item -LiteralPath $taskChecked -Recurse -Force
    }
}
