$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$projectPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $projectPython -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython -m ruff check agent_workbench tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython -m agent_workbench eval
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $projectPython -m agent_workbench app-eval
exit $LASTEXITCODE
