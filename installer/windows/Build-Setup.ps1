param(
    [string]$Compiler,
    [string]$OutputDirectory,
    [switch]$KeepStage
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutputDirectory) { $OutputDirectory=Join-Path $taskRoot 'Builds\Windows' }
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if (-not $Compiler) {
    $taskOnPath=Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($taskOnPath) { $Compiler=$taskOnPath.Source }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf)) { throw 'Pass the path to NSIS makensis.exe with -Compiler.' }
$Compiler=(Resolve-Path -LiteralPath $Compiler).Path
$taskPackage=Get-Content -LiteralPath (Join-Path $taskRoot 'package.json') -Raw | ConvertFrom-Json
if ($taskPackage.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Expected a numeric major.minor.patch version.' }

# Use the public source allowlist, then include only runtime/documentation trees.
$taskRoots=@('web','installer','runtime','licenses','animations','unity','UniRig','integrations')
$taskRootFiles=@('LICENSE','README.md','INSTALLATION.md','SECURITY.md','THIRD_PARTY_NOTICES.md','package.json','package-lock.json')
$taskFiles=@(git -C $taskRoot -c core.quotePath=false ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw 'Could not read the TRELLIS Studio public source allowlist.' }
$taskFiles=@($taskFiles | Sort-Object -Unique | Where-Object {
    $parts=$_ -split '/'
    ($parts.Count -eq 1 -and ($_ -match '\.(py|cmd|ps1)$' -or $taskRootFiles -contains $_ -or $_ -match '^requirements-(windows|mcp-lock|mcp)\.txt$')) -or
    ($parts.Count -gt 1 -and ($taskRoots -ccontains $parts[0] -or $_ -in @('docs/API.md','docs/MCP.md','docs/DEPENDENCIES.json','docs/WINDOWS_SETUP.md','docs/SERVER.md')))
})
$taskRequired=@('app.py','package.json','installer/setup.py','installer/windows/check-idle.ps1','installer/windows/Launch.cmd','installer/windows/Launch-Installed.ps1','installer/windows/Manage-Shortcuts.ps1','installer/windows/trellis-studio.ico','Start-Studio.ps1','web/studio.html','animations/library.json','unity/TrellisPackageImporter.cs')
$taskRequired+=@('server_app.py','server_runtime.py','server_store.py','server_setup.py','Start-Server.ps1','Stop-Server.ps1','Open TRELLIS Server.cmd',
    'installer/windows/Configure-Server.ps1','web/server.html','web/server.css','web/server.js','web/server-shell.js','docs/SERVER.md')
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
    $taskName='TRELLIS-Studio-'+$taskPackage.version+'-Setup-x64.exe'
    $taskOut=Join-Path $taskStage $taskName
    & $Compiler /V3 /INPUTCHARSET UTF8 "/DOUTPUT_EXE=$taskOut" "/DPAYLOAD_ROOT=$taskPayload" "/DINSTALL_FILES=$taskInstallPath" "/DUNINSTALL_FILES=$taskUninstallPath" "/DSTUDIO_VERSION=$($taskPackage.version)" (Join-Path $PSScriptRoot 'StudioSetup.nsi')
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
