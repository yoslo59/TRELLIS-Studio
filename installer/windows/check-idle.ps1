param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [switch]$IgnoreCurrentProcess,
    [ValidateSet('Check','ProbeUninstall','StopForUninstall')][string]$Action='Check'
)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($InstallRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$taskScripts=@{
    app='app.py'; bridge='trellis_mcp.py'; setup='Install-Studio.ps1'
    pixal='pixal3d_setup.py'; launcher='installer\windows\Launch-Installed.ps1'
}
function Test-StudioArgument([string]$CommandLine,[string]$Role) {
    if (-not $CommandLine) { return $false }
    $path=[regex]::Escape((Join-Path $taskRoot $taskScripts[$Role]))
    return [regex]::IsMatch($CommandLine,('(?:^|\s)(?:"'+$path+'"|'+$path+')(?=\s|$)'),[Text.RegularExpressions.RegexOptions]::IgnoreCase)
}
function Get-StudioProcesses {
    foreach ($taskProcess in (Get-CimInstance -ClassName Win32_Process -Filter "Name LIKE 'python%.exe' OR Name = 'powershell.exe' OR Name = 'pwsh.exe'")) {
        if ($IgnoreCurrentProcess -and $taskProcess.ProcessId -eq $PID) { continue }
        foreach ($role in $taskScripts.Keys) {
            if (Test-StudioArgument $taskProcess.CommandLine $role) {
                [pscustomobject]@{ Role=$role; Id=$taskProcess.ProcessId; Parent=$taskProcess.ParentProcessId }
                break
            }
        }
    }
}
function Assert-NoComponentLock {
    $lockPath=Join-Path $taskRoot '.component-install.lock'
    if (Test-Path -LiteralPath $lockPath) {
        try { $handle=[IO.File]::Open($lockPath,'Open','ReadWrite','None'); $handle.Dispose() }
        catch [IO.IOException] { throw 'The TRELLIS component installer is running.' }
    }
}
function Get-LocalStudioListener([string]$Role,$Processes,$Connections) {
    $owners=@($Processes | Where-Object Role -eq $Role)
    if (-not $owners.Count) { return }
    $listeners=@($Connections | Where-Object { $_.OwningProcess -in $owners.Id })
    if ($listeners.Count -ne 1) { throw "This copy has a $Role process without a unique local listener. Close Studio and any MCP clients, then retry." }
    $listener=$listeners[0]
    $memberIds=@($listener.OwningProcess)
    # On Windows a Python venv can have a forwarding parent with the same script.
    while ($true) {
        $parent=($owners | Where-Object Id -eq $memberIds[-1]).Parent
        if ($parent -notin $owners.Id -or $parent -in $memberIds) { break }
        $memberIds+=@($parent)
    }
    if (@($owners | Where-Object { $_.Id -notin $memberIds }).Count) {
        throw "Another $Role session uses this installation. Close it before uninstalling."
    }
    return $listener
}
function Assert-ListenerOwner($Listener,[string]$Role) {
    $owner=Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)"
    $current=@(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $Listener.LocalPort -ErrorAction SilentlyContinue)
    if (-not (Test-StudioArgument $owner.CommandLine $Role) -or $current.Count -ne 1 -or $current[0].OwningProcess -ne $Listener.OwningProcess) {
        throw "The $Role listener changed; no unrelated process was stopped."
    }
}

if ($Action -eq 'Check') {
    $running=@(Get-StudioProcesses)
    if ($running.Count) { throw "TRELLIS Studio is running from this folder (process $($running[0].Id))." }
    Assert-NoComponentLock
    return
}

try {
    Assert-NoComponentLock
    $running=@(Get-StudioProcesses)
    if (-not $running.Count) { exit 0 }
    if (@($running | Where-Object { $_.Role -notin @('app','bridge') }).Count) { throw 'The launcher or component installer is still running.' }
    $connections=@(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -ErrorAction SilentlyContinue)
    $app=Get-LocalStudioListener 'app' $running $connections
    $bridge=Get-LocalStudioListener 'bridge' $running $connections
    if ($app) {
        Assert-ListenerOwner $app 'app'
        $url='http://127.0.0.1:'+$app.LocalPort
        $state=Invoke-RestMethod -Uri ($url+'/api/status') -TimeoutSec 4
        if (-not $state.local -or -not $state.PSObject.Properties['export_active']) { throw 'This version cannot confirm an idle shutdown.' }
        if ($state.active -or $state.export_active) { exit 11 }
    }
    if ($Action -eq 'ProbeUninstall') { exit 10 }
    if ($app) {
        $server=Get-Process -Id $app.OwningProcess
        Assert-ListenerOwner $app 'app'
        try { Invoke-RestMethod -Method Post -Uri ($url+'/api/shutdown?idle_only=true') -TimeoutSec 5 | Out-Null }
        catch {
            if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 409) { exit 11 }
            throw
        }
        if (-not $server.WaitForExit(20000)) { throw 'Studio did not stop in time.' }
    }
    if ($bridge) {
        $bridgeProcess=Get-Process -Id $bridge.OwningProcess -ErrorAction SilentlyContinue
        if ($bridgeProcess) {
            Assert-ListenerOwner $bridge 'bridge'
            Stop-Process -InputObject $bridgeProcess
            if (-not $bridgeProcess.WaitForExit(5000)) { throw 'The MCP bridge did not stop in time.' }
        }
    }
    for ($attempt=0; $attempt -lt 10; $attempt++) {
        if (-not @(Get-StudioProcesses).Count) { Assert-NoComponentLock; exit 0 }
        Start-Sleep -Milliseconds 350
    }
    throw 'A process for this installation is still running.'
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 12
}
