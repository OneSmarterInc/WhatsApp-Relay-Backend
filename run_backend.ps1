# Run from PowerShell. Stops only this workspace's Django development servers.
$ErrorActionPreference = 'Stop'
$relayBackend = $PSScriptRoot
$relayPython = Join-Path $relayBackend '.venv\Scripts\python.exe'
& $relayPython (Join-Path $relayBackend 'ensure_database.py')
if ($LASTEXITCODE -ne 0) { throw 'Database check failed. Backend was not started.' }
$relayProcesses = @(Get-CimInstance Win32_Process)
$relayRoots = @($relayProcesses | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -like "*$relayPython*" -and
    $_.CommandLine -match 'manage\.py\s+runserver\s+127\.0\.0\.1:8000'
})
$relayIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($relayRoot in $relayRoots) { [void]$relayIds.Add([int]$relayRoot.ProcessId) }
do {
    $relayAdded = $false
    foreach ($relayProcess in $relayProcesses) {
        if ($relayIds.Contains([int]$relayProcess.ParentProcessId) -and $relayIds.Add([int]$relayProcess.ProcessId)) {
            $relayAdded = $true
        }
    }
} while ($relayAdded)
foreach ($relayId in $relayIds) { Stop-Process -Id $relayId -ErrorAction SilentlyContinue }
Set-Location -LiteralPath $relayBackend
Write-Host 'Starting one Relay backend with current code. Keep this terminal open; Ctrl+C stops it.'
& $relayPython manage.py runserver 127.0.0.1:8000
