$ErrorActionPreference = 'Stop'
$runnerPath = Join-Path $PSScriptRoot 'local\run.py'
$pidPath = Join-Path $PSScriptRoot '.tooluniverse\runtime\workbench\server-process.json'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'No managed workbench process.'; exit 0 }
$saved = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($process) {
    if ($process.CommandLine -notlike "*$runnerPath* workbench *") { throw 'PID belongs to another process; refusing to stop it.' }
    & taskkill.exe /PID $saved.pid /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the workbench process tree.' }
}
Remove-Item -LiteralPath $pidPath
Write-Host 'ToolUniverse workbench stopped. The scientific API remains available.'
