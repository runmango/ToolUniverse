$ErrorActionPreference = 'Stop'
$runnerPath = Join-Path $PSScriptRoot 'local\run.py'
$pidPath = Join-Path $PSScriptRoot '.tooluniverse\runtime\api-process.json'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'No managed API process.'; exit 0 }
$saved = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($process) {
    if ($process.CommandLine -notlike "*$runnerPath* api *") { throw 'PID belongs to another process; refusing to stop it.' }
    # The Windows venv launcher can own a separate Python worker process.
    # Stop the validated process tree so that the listening worker exits too.
    & taskkill.exe /PID $saved.pid /T /F
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the API process tree.' }
}
Remove-Item -LiteralPath $pidPath
Write-Host 'ToolUniverse API stopped.'
