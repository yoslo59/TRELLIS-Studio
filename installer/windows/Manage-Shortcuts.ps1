param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][ValidateSet('Install','Uninstall','ServerStartup')][string]$Action,
    [ValidateSet(0,1)][int]$DesktopShortcut=0,
    [ValidateSet(0,1)][int]$ServerMode=0
)
$ErrorActionPreference='Stop'
$installRoot=[IO.Path]::GetFullPath($InstallRoot)
$launcher=Join-Path $installRoot 'installer\windows\Launch.cmd'
$icon=Join-Path $installRoot 'installer\windows\trellis-studio.ico'
$programs=[Environment]::GetFolderPath('Programs')
$desktop=[Environment]::GetFolderPath('DesktopDirectory')
if (-not $programs) { throw 'Windows did not resolve the Start menu folder.' }
$menu=Join-Path $programs 'TRELLIS Studio'
$desktopName='TRELLIS Studio.lnk'
$desktopDescription='TRELLIS Studio (Windows Setup)'
$links=@(
    @{ Name='TRELLIS Studio.lnk'; Arguments='' }
    @{ Name='Install TRELLIS generation.lnk'; Arguments='Generation' }
    @{ Name='Install generation and UniRig.lnk'; Arguments='Rig' }
    @{ Name='Install Pixal3D.lnk'; Arguments='Pixal3D' }
    @{ Name='Stop TRELLIS Studio.lnk'; Arguments='Stop' }
)
if ($ServerMode -or $Action -eq 'Uninstall') {
    $links+=@(
        @{ Name='TRELLIS Server.lnk'; Arguments='Server' }
        @{ Name='Configure TRELLIS Server.lnk'; Arguments='ConfigureServer' }
        @{ Name='Stop TRELLIS Server.lnk'; Arguments='StopServer' }
    )
}
$shell=New-Object -ComObject WScript.Shell

function Update-StudioShortcut([string]$Path,[string]$Arguments,[switch]$OnDesktop) {
    $exists=Test-Path -LiteralPath $Path
    if (-not $exists -and ($Action -eq 'Uninstall' -or ($OnDesktop -and $DesktopShortcut -eq 0))) { return }
    $shortcut=$null
    try {
        try { $shortcut=$shell.CreateShortcut($Path) }
        catch {
            if (-not $exists) { throw }
            Write-Output "Kept an existing shortcut at $Path."
            return
        }
        if ($exists -and ($shortcut.TargetPath -ine $launcher -or $shortcut.Arguments -cne $Arguments -or
            ($OnDesktop -and $shortcut.Description -cne $desktopDescription))) {
            Write-Output "Kept an existing shortcut at $Path."
            return
        }
        if ($Action -eq 'Uninstall') {
            Remove-Item -LiteralPath $Path
            return
        }
        $shortcut.TargetPath=$launcher
        $shortcut.Arguments=$Arguments
        $shortcut.WorkingDirectory=$installRoot
        $shortcut.IconLocation="$icon,0"
        if ($OnDesktop) { $shortcut.Description=$desktopDescription }
        $shortcut.Save()
    } finally {
        if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) }
    }
}

function Update-ServerStartup([bool]$Enabled) {
    $startup=[Environment]::GetFolderPath('Startup')
    if (-not $startup) { if ($Enabled) { throw 'Windows did not resolve the Startup folder.' }; return }
    $path=Join-Path $startup 'TRELLIS Server.lnk'
    $target=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $installRoot 'Start-Server.ps1')+'" -NoBrowser'
    $exists=Test-Path -LiteralPath $path -PathType Leaf
    if (-not $exists -and -not $Enabled) { return }
    $shortcut=$shell.CreateShortcut($path)
    try {
        if ($exists -and ($shortcut.TargetPath -ine $target -or $shortcut.Arguments -cne $arguments)) {
            if ($Enabled) { throw 'Another TRELLIS installation already owns the Startup shortcut.' }
            return
        }
        if (-not $Enabled) { Remove-Item -LiteralPath $path; return }
        $shortcut.TargetPath=$target
        $shortcut.Arguments=$arguments
        $shortcut.WorkingDirectory=$installRoot
        $shortcut.IconLocation="$icon,0"
        $shortcut.Save()
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) }
}

try {
    if ($Action -eq 'ServerStartup') {
        $config=Get-Content -LiteralPath (Join-Path $installRoot 'config\server.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Update-ServerStartup ([bool]$config.autostart)
        return
    }
    if ($Action -eq 'Install') {
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or -not (Test-Path -LiteralPath $icon -PathType Leaf)) {
            throw 'Studio launcher or application icon is missing.'
        }
        New-Item -ItemType Directory -Path $menu -Force | Out-Null
    }
    foreach ($link in $links) {
        Update-StudioShortcut (Join-Path $menu $link.Name) $link.Arguments
    }
    if ($desktop) {
        if (-not $ServerMode -or $Action -eq 'Uninstall') {
            Update-StudioShortcut (Join-Path $desktop $desktopName) '' -OnDesktop
        }
        if ($ServerMode -or $Action -eq 'Uninstall') {
            Update-StudioShortcut (Join-Path $desktop 'TRELLIS Server.lnk') 'Server' -OnDesktop
        }
    } elseif ($Action -eq 'Install' -and $DesktopShortcut -eq 1) {
        throw 'Windows did not resolve the Desktop folder.'
    }
    if ($Action -eq 'Uninstall' -and (Test-Path -LiteralPath $menu -PathType Container) -and
        -not @(Get-ChildItem -LiteralPath $menu -Force).Count) {
        # Keep a same-name folder if a user added any other shortcuts to it.
        [IO.Directory]::Delete($menu)
    }
    if ($Action -eq 'Uninstall') { Update-ServerStartup $false }
} finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
