$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        uv venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    $installArgs = @('pip', 'install', '--python', '.venv\Scripts\python.exe', '--editable', '.')
    if (Test-Path 'local\constraints-win-py312.txt') { $installArgs += @('--constraint', 'local\constraints-win-py312.txt') }
    & uv @installArgs
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
    & '.venv\Scripts\python.exe' 'local\run.py' cli status
    if ($LASTEXITCODE -ne 0) { throw 'ToolUniverse startup check failed.' }
} finally { Pop-Location }
