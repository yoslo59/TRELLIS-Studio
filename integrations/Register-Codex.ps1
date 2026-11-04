param([switch]$PrintOnly)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskPython=Join-Path $taskRoot '.mcp-venv\Scripts\python.exe'
$taskServer=Join-Path $taskRoot 'trellis_mcp.py'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Install requirements-mcp-lock.txt in .mcp-venv first.' }
if ($PrintOnly) {
    @{mcpServers=@{'trellis-studio'=@{command=$taskPython;args=@($taskServer);cwd=$taskRoot}}} | ConvertTo-Json -Depth 5
} else {
    & codex mcp add trellis-studio -- $taskPython $taskServer
    if ($LASTEXITCODE -ne 0) { throw 'Codex registration failed.' }
    Write-Output 'TRELLIS Studio registered. Reload MCP or open a new chat to discover tools.'
}
