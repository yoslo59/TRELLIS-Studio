$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskApp = Join-Path $taskRoot 'app.py'
$taskConnection = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
if ($taskConnection) {
    $taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskConnection[0].OwningProcess)"
    if (-not ($taskOwner.CommandLine -and $taskOwner.CommandLine.Contains($taskApp))) { throw 'This server belongs to another application; it was preserved.' }
    $taskServer = Get-Process -Id $taskOwner.ProcessId -ErrorAction SilentlyContinue
    if ($taskServer) {
        Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:7860/api/shutdown' -TimeoutSec 5 | Out-Null
        if (-not $taskServer.WaitForExit(20000)) {
            throw 'TRELLIS Studio is still stopping. Wait for it to finish before reopening.'
        }
    }
}
