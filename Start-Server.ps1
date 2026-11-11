param([switch]$Configure, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskScript = Join-Path $taskRoot 'server_app.py'
$taskConfig = Join-Path $taskRoot 'config\server.json'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw 'TRELLIS Python is missing. Run the Windows Setup in Server mode or install the engine first.'
}
if ($Configure) {
    & (Join-Path $taskRoot 'installer\windows\Configure-Server.ps1') -ConfigPath $taskConfig -Python $taskPython
}
if (-not (Test-Path -LiteralPath $taskConfig -PathType Leaf)) {
    throw 'Run Start-Server.ps1 -Configure to set up this server and its first administrator.'
}
$taskSettings = Get-Content -LiteralPath $taskConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPort = [int]$taskSettings.port
$taskListeners = @(Get-NetTCPConnection -State Listen -LocalPort $taskPort -ErrorAction SilentlyContinue)
if ($taskListeners.Count) {
    $taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListeners[0].OwningProcess)"
    $taskExpected = [regex]::Escape('"' + $taskScript + '" --config "' + $taskConfig + '"')
    if ($taskListeners.Count -ne 1 -or $taskListeners[0].LocalAddress -ne $taskSettings.host -or
        $taskOwner.CommandLine -notmatch $taskExpected) {
        throw "Port $taskPort is already occupied. Its process was left running."
    }
} else {
    & $taskPython -B (Join-Path $taskRoot 'server_setup.py') --config $taskConfig --check
    if ($LASTEXITCODE -ne 0) { throw 'Server configuration check failed.' }
    $taskLogs = Join-Path $taskSettings.data_dir 'logs'
    New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
    $taskArguments = @('-B', '-u', ('"' + $taskScript + '"'), '--config', ('"' + $taskConfig + '"'))
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'server.log') `
        -RedirectStandardError (Join-Path $taskLogs 'server-errors.log')
    $taskReady = $false
    for ($attempt = 0; $attempt -lt 60 -and -not $taskProcess.HasExited; $attempt++) {
        $taskListener = Get-NetTCPConnection -State Listen -LocalAddress $taskSettings.host -LocalPort $taskPort -ErrorAction SilentlyContinue
        if ($taskListener) {
            $taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListener.OwningProcess)"
            # A venv on Windows can use a forwarding parent.
            if ($taskOwner.ProcessId -eq $taskProcess.Id -or $taskOwner.ParentProcessId -eq $taskProcess.Id) {
                $taskReady = $true
                break
            }
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $taskReady) { throw "TRELLIS Server did not start. See $taskLogs\server-errors.log." }
}
Write-Host ('TRELLIS Server: ' + $taskSettings.public_url)
if (-not $NoBrowser) { Start-Process $taskSettings.public_url }
