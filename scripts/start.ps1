$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $projectPython -c 'import fastapi, uvicorn, httpx, dotenv, mcp, keyring, playwright, jsonschema, yaml' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $projectPython -m ensurepip --upgrade
    & $projectPython -m pip install --require-hashes -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Host 'Agent Workbench: http://127.0.0.1:8765' -ForegroundColor Green
& $projectPython -m agent_workbench serve
exit $LASTEXITCODE
