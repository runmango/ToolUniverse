param([ValidateRange(1, 65535)][int]$Port = 8090)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $projectRoot 'local\run.py'
$runtimePath = Join-Path $projectRoot '.tooluniverse\runtime\workbench'
$pidPath = Join-Path $runtimePath 'server-process.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run local\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\dist\index.html'))) { throw 'Build the frontend first: cd frontend; npm.cmd install; npm.cmd run build' }
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null

# Ensure the existing scientific API is available; keep its lifecycle independent.
$apiReady = $false
try { $apiHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8088/health' -TimeoutSec 2; $apiReady = $apiHealth.status -eq 'healthy' } catch { }
if (-not $apiReady) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'start-api.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'ToolUniverse API startup failed.' }
}
if (Test-Path -LiteralPath $pidPath) {
    $saved = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
    if ($existing -and $existing.CommandLine -like "*$runnerPath* workbench *") {
        Write-Host "ToolUniverse workbench is already running: http://127.0.0.1:$($saved.port)"
        exit 0
    }
}
$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $probe.Start() } catch { throw "Port $Port is occupied. Pass -Port with a free port." }
finally { $probe.Stop() }
$launchArguments = '"{0}" workbench --port {1}' -f $runnerPath, $Port
$process = Start-Process -FilePath $pythonPath -ArgumentList $launchArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'server.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'server.stderr.log')
$record = @{ pid = $process.Id; port = $Port; runner = $runnerPath } | ConvertTo-Json
[System.IO.File]::WriteAllText($pidPath, $record, [System.Text.UTF8Encoding]::new($false))
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $process.Refresh()
    if ($process.HasExited) { throw 'Workbench exited. Check .tooluniverse\runtime\workbench\server.stderr.log.' }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
        if ($health.service -eq 'tooluniverse-workbench') { Write-Host "ToolUniverse workbench: http://127.0.0.1:$Port (PID $($process.Id))"; exit 0 }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw 'Workbench did not become ready. Check .tooluniverse\runtime\workbench\server.stderr.log.'
