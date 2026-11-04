param([switch]$SansNavigateur)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskUrl = 'http://127.0.0.1:7860'
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskScript = Join-Path $taskRoot 'app.py'
$taskLogs = Join-Path $taskRoot 'logs'
$taskConfigPath = Join-Path $taskRoot 'config\runtime.json'
$taskDistro = 'Ubuntu-22.04'
if (Test-Path -LiteralPath $taskConfigPath) {
    $taskConfig = Get-Content -LiteralPath $taskConfigPath -Raw | ConvertFrom-Json
    if ($taskConfig.wsl_distribution) { $taskDistro = $taskConfig.wsl_distribution }
}
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'TRELLIS Python environment is missing. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'node_modules\three\build\three.module.js'))) { throw '3D viewer dependency is missing. Run npm ci in TRELLIS.' }
# Wake WSL and probe the private environment; weights load only on demand.
$taskRuntimeError = $null
try {
    $taskCheckWindows = Join-Path $taskRoot 'UniRig\check-runtime.sh'
    $taskPathProcess = Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d', $taskDistro, '--', 'wslpath', '-a', '-u', ('"' + $taskCheckWindows + '"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'unirig-chemin.log') -RedirectStandardError (Join-Path $taskLogs 'unirig-chemin-erreurs.log')
    $taskCheckLinux = (Get-Content -LiteralPath (Join-Path $taskLogs 'unirig-chemin.log') -Raw).Trim()
    if ($taskPathProcess.ExitCode -ne 0 -or -not $taskCheckLinux.StartsWith('/')) { throw 'Unable to convert the WSL path.' }
    $taskCheckProcess = Start-Process -FilePath 'wsl.exe' -ArgumentList @('-d', $taskDistro, '--', 'bash', ('"' + $taskCheckLinux + '"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'unirig-demarrage.log') -RedirectStandardError (Join-Path $taskLogs 'unirig-demarrage-erreurs.log')
    if ($taskCheckProcess.ExitCode -ne 0) { throw 'UniRig check failed. See logs\unirig-demarrage-erreurs.log.' }
} catch {
    $taskRuntimeError = $_.Exception.Message
    @{unirig=@{ready=$false; message=$taskRuntimeError}} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskLogs 'components.json') -Encoding utf8
}
$taskConnection = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
if ($taskConnection) {
    $taskOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskConnection[0].OwningProcess)"
    if (-not ($taskOwner.CommandLine -and $taskOwner.CommandLine.Contains($taskScript))) { throw 'Port 7860 belongs to another application; it was left running.' }
    $taskExisting = Invoke-RestMethod -Uri ($taskUrl + '/api/status') -TimeoutSec 5
    $taskHasher = [System.Security.Cryptography.SHA256]::Create()
    try { $taskRevision = [BitConverter]::ToString($taskHasher.ComputeHash([IO.File]::ReadAllBytes($taskScript))).Replace('-', '').ToLowerInvariant() }
    finally { $taskHasher.Dispose() }
    if ($taskExisting.studio_version -ne '3.1.0' -or $taskExisting.source_revision -ne $taskRevision) {
        if ($taskExisting.active) {
            Write-Output 'Waiting for the current computation to finish before updating.'
            for ($taskWait=0; $taskWait -lt 600 -and $taskExisting.active; $taskWait++) {
                Start-Sleep -Milliseconds 500
                $taskExisting = Invoke-RestMethod -Uri ($taskUrl + '/api/status') -TimeoutSec 5
            }
            if ($taskExisting.active) { throw 'Computation is still running. Try again later; no process was forcibly stopped.' }
        }
        Invoke-RestMethod -Method Post -Uri ($taskUrl + '/api/shutdown') -TimeoutSec 5 | Out-Null
        for ($taskAttempt=0; $taskAttempt -lt 30; $taskAttempt++) {
            Start-Sleep -Milliseconds 500
            $taskConnection = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
            if (-not $taskConnection) { break }
        }
        if ($taskConnection) { throw 'The previous studio is still running; no process was forcibly stopped.' }
    }
}
if (-not $taskConnection) {
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', ('"' + $taskScript + '"')) -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'serveur.log') -RedirectStandardError (Join-Path $taskLogs 'serveur-erreurs.log')
    $taskProcess.Id | Set-Content -LiteralPath (Join-Path $taskLogs 'serveur.pid')
}
$taskReady = $false
for ($taskAttempt=0; $taskAttempt -lt 60; $taskAttempt++) {
    try {
        $taskResponse = Invoke-RestMethod -Uri ($taskUrl + '/api/status') -TimeoutSec 2
        if ($taskResponse.local -and $taskResponse.studio_version -eq '3.1.0') { $taskReady=$true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $taskReady) { throw 'The server did not respond. See logs\serveur-erreurs.log.' }
# The bridge uses its own Python environment and shares only the local HTTP API.
$taskMcpPython = Join-Path $taskRoot '.mcp-venv\Scripts\python.exe'
$taskMcpScript = Join-Path $taskRoot 'trellis_mcp.py'
if (-not (Test-Path -LiteralPath $taskMcpPython)) { throw 'MCP environment is missing. See docs/MCP.md.' }
$taskMcpConnection = Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue
if ($taskMcpConnection) {
    $taskMcpOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskMcpConnection[0].OwningProcess)"
    if (-not ($taskMcpOwner.CommandLine -and $taskMcpOwner.CommandLine.Contains($taskMcpScript))) { throw 'Port 7861 belongs to another application; it was left running.' }
} else {
    $taskMcpProcess = Start-Process -FilePath $taskMcpPython -ArgumentList @('-u',('"'+$taskMcpScript+'"'),'--transport','streamable-http') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs 'mcp.log') -RedirectStandardError (Join-Path $taskLogs 'mcp-errors.log')
    $taskMcpProcess.Id | Set-Content -LiteralPath (Join-Path $taskLogs 'mcp.pid')
}
$taskMcpReady=$false
for ($taskWait=0;$taskWait -lt 30;$taskWait++) {
    try { $taskMcpStatus=Invoke-RestMethod -Uri 'http://127.0.0.1:7861/health' -TimeoutSec 2; if ($taskMcpStatus.version -eq '3.1.0') { $taskMcpReady=$true;break } } catch { }
    Start-Sleep -Milliseconds 300
}
if (-not $taskMcpReady) { throw 'MCP bridge did not respond with the expected version. See logs/mcp-errors.log.' }
Write-Output 'API reference: http://127.0.0.1:7860/developers'
Write-Output 'MCP bridge ready: http://127.0.0.1:7861/mcp'
if (-not $SansNavigateur) { Start-Process $taskUrl -WindowStyle Hidden }
Write-Output ('TRELLIS Studio ready: ' + $taskUrl)
if ($taskRuntimeError) { Write-Warning ('UniRig unavailable: ' + $taskRuntimeError) }
else { Write-Output 'UniRig and Blender ready. Computation starts on demand.' }
