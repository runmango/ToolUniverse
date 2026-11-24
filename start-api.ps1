param(
    [ValidateRange(1, 65535)][int]$Port = 8088,
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $projectRoot 'local\run.py'
$runtimePath = Join-Path $projectRoot '.tooluniverse\runtime'
$pidPath = Join-Path $runtimePath 'api-process.json'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run local\setup.ps1 first.' }
New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null

if ($Foreground) {
    & $pythonPath $runnerPath api --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
}

if (Test-Path -LiteralPath $pidPath) {
    $saved = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
    if ($existing -and $existing.CommandLine -like "*$runnerPath* api *") {
        Write-Host "ToolUniverse is already running: http://127.0.0.1:$($saved.port)/docs"
        exit 0
    }
}

$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $probe.Start() } catch { throw "Port $Port is occupied. Pass -Port with a free port." }
finally { $probe.Stop() }

$launchArguments = '"{0}" api --host 127.0.0.1 --port {1}' -f $runnerPath, $Port
$process = Start-Process -FilePath $pythonPath -ArgumentList $launchArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimePath 'api.stdout.log') -RedirectStandardError (Join-Path $runtimePath 'api.stderr.log')
@{ pid = $process.Id; port = $Port; runner = $runnerPath } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $pidPath
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $process.Refresh()
    if ($process.HasExited) { throw 'API exited. Check .tooluniverse\runtime\api.stderr.log.' }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
        if ($health.status -eq 'healthy') {
            Write-Host "ToolUniverse API: http://127.0.0.1:$Port/docs (PID $($process.Id))"
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw 'API did not become ready. Check .tooluniverse\runtime\api.stderr.log; stop-api.ps1 stops this process.'
