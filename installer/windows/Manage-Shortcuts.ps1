param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][ValidateSet('Install','Uninstall')][string]$Action,
    [ValidateSet(0,1)][int]$DesktopShortcut=0
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

try {
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
        Update-StudioShortcut (Join-Path $desktop $desktopName) '' -OnDesktop
    } elseif ($Action -eq 'Install' -and $DesktopShortcut -eq 1) {
        throw 'Windows did not resolve the Desktop folder.'
    }
    if ($Action -eq 'Uninstall' -and (Test-Path -LiteralPath $menu -PathType Container) -and
        -not @(Get-ChildItem -LiteralPath $menu -Force).Count) {
        # Keep a same-name folder if a user added any other shortcuts to it.
        [IO.Directory]::Delete($menu)
    }
} finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
