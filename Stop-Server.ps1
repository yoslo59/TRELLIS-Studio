$ErrorActionPreference = 'Stop'
$taskConfig = Join-Path $PSScriptRoot 'config\server.json'
$taskScript = Join-Path $PSScriptRoot 'server_app.py'
if (-not (Test-Path -LiteralPath $taskConfig -PathType Leaf)) { return }
$taskSettings = Get-Content -LiteralPath $taskConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPort = [int]$taskSettings.port
$taskListeners = @(Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue)
if (-not $taskListeners.Count) { return }
$taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListeners[0].OwningProcess)"
$taskExpected = [regex]::Escape('"' + $taskScript + '" --config "' + $taskConfig + '"')
if ($taskListeners.Count -ne 1 -or $taskListeners[0].LocalAddress -ne $taskSettings.host -or
    $taskOwner.CommandLine -notmatch $taskExpected) {
    throw "Port $taskPort belongs to another process. It was left running."
}
$taskNonce = [Guid]::NewGuid().ToString('N')
$taskRequest = Join-Path $taskSettings.data_dir 'stop.request'
$taskResponse = Join-Path $taskSettings.data_dir 'stop.response'
$taskPending = Join-Path $taskSettings.data_dir ('.stop-' + $taskNonce)
try {
    [IO.File]::WriteAllText($taskPending, $taskNonce)
    [IO.File]::Move($taskPending, $taskRequest)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (-not (Test-Path -LiteralPath $taskResponse -PathType Leaf)) { continue }
        $taskResult = Get-Content -LiteralPath $taskResponse -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($taskResult.nonce -cne $taskNonce) { continue }
        if ($taskResult.busy) { throw 'TRELLIS Server is processing a job. Wait for it to finish, then retry.' }
        if ($taskResult.stopped) {
            Wait-Process -Id $taskOwner.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
            if (Get-Process -Id $taskOwner.ProcessId -ErrorAction SilentlyContinue) { throw 'TRELLIS Server is still stopping.' }
            Write-Host 'TRELLIS Server has stopped.'
            return
        }
        throw 'TRELLIS Server rejected the shutdown request.'
    }
    throw 'TRELLIS Server did not acknowledge the shutdown request.'
} finally {
    if (Test-Path -LiteralPath $taskPending -PathType Leaf) { Remove-Item -LiteralPath $taskPending }
    if ((Test-Path -LiteralPath $taskRequest -PathType Leaf) -and
        (Get-Content -LiteralPath $taskRequest -Raw -ErrorAction SilentlyContinue) -ceq $taskNonce) {
        Remove-Item -LiteralPath $taskRequest -ErrorAction SilentlyContinue
    }
}
