param([switch]$WithBridge,[switch]$IdleOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskApp = Join-Path $taskRoot 'app.py'
$taskPort = if ($env:TRELLIS_PORT) { [int]$env:TRELLIS_PORT } else { 7860 }
$taskMcpPort = if ($env:TRELLIS_MCP_PORT) { [int]$env:TRELLIS_MCP_PORT } else { 7861 }
$taskUrl = 'http://127.0.0.1:' + $taskPort
$taskConnection = Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue
if ($taskConnection) {
    $taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskConnection[0].OwningProcess)"
    if (-not ($taskOwner.CommandLine -and $taskOwner.CommandLine.IndexOf($taskApp,[StringComparison]::OrdinalIgnoreCase) -ge 0)) { throw 'This server belongs to another application; it was preserved.' }
    $taskServer = Get-Process -Id $taskOwner.ProcessId -ErrorAction SilentlyContinue
    if ($taskServer) {
        $taskShutdown=$taskUrl+'/api/shutdown'
        if ($IdleOnly) {
            $taskStatus=Invoke-RestMethod -Uri ($taskUrl+'/api/status') -TimeoutSec 5
            if (-not $taskStatus.PSObject.Properties['export_active']) { throw 'Close this older Studio from its interface when all tasks have finished.' }
            if ($taskStatus.active -or $taskStatus.export_active) { throw 'Wait for generation or export to finish before stopping or changing the installation.' }
            $taskShutdown+='?idle_only=true'
        }
        Invoke-RestMethod -Method Post -Uri $taskShutdown -TimeoutSec 5 | Out-Null
        if (-not $taskServer.WaitForExit(20000)) {
            throw 'TRELLIS Studio is still stopping. Wait for it to finish before reopening.'
        }
    }
}
if ($WithBridge) {
    $taskConnection=Get-NetTCPConnection -LocalPort $taskMcpPort -State Listen -ErrorAction SilentlyContinue
    if ($taskConnection) {
        $taskOwner=Get-CimInstance Win32_Process -Filter "ProcessId=$($taskConnection[0].OwningProcess)"
        $taskMcpScript=Join-Path $taskRoot 'trellis_mcp.py'
        if (-not ($taskOwner.CommandLine -and $taskOwner.CommandLine.IndexOf($taskMcpScript,[StringComparison]::OrdinalIgnoreCase) -ge 0)) { throw 'The MCP port belongs to another application; it was preserved.' }
        Stop-Process -Id $taskOwner.ProcessId
    }
}
