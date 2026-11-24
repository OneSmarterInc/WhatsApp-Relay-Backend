$ErrorActionPreference = 'Stop'
$relayPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $PSScriptRoot
& $relayPython (Join-Path $PSScriptRoot 'ensure_database.py')
if ($LASTEXITCODE -ne 0) { throw 'Database check failed. Worker was not started.' }
& $relayPython manage.py relay_worker
exit $LASTEXITCODE
