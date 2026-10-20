[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$McpConfigPath,
    [string]$PythonPath,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath $McpConfigPath -PathType Leaf)) {
    throw 'The explicitly supplied MCP config path does not exist. Select the active path through Antigravity settings. File existence does not verify registration.'
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw 'codex must be available on PATH and signed in through its own CLI.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js/npm; npm.cmd must be on PATH.' }
$taskState = Join-Path $taskRoot '.devmesh'
$taskMarker = Join-Path $taskState 'launcher-processes.json'
$taskOwned = @()
if (Test-Path -LiteralPath $taskMarker) { $taskOwned = @(Get-Content -LiteralPath $taskMarker -Raw | ConvertFrom-Json) }
foreach ($taskPort in @(8420,5173)) {
    $taskListeners = @(Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($taskListener in $taskListeners) {
        $taskProcess = Get-Process -Id $taskListener.OwningProcess -ErrorAction SilentlyContinue
        $taskKnown = @($taskOwned | Where-Object { $_.pid -eq $taskListener.OwningProcess -and $taskProcess -and $_.started -eq $taskProcess.StartTime.ToUniversalTime().ToString('o') }).Count -gt 0
        $taskLabel = if ($taskKnown) { 'previously launched DevMesh process' } else { 'unverified process' }
        throw "Port $taskPort is occupied by $taskLabel (PID $($taskListener.OwningProcess)). No process was terminated. Inspect/stop it in its owning terminal before normal startup."
    }
}
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskVersionCheck = 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
if (Test-Path -LiteralPath $taskPython) {
    & $taskPython -c $taskVersionCheck
    if ($LASTEXITCODE -ne 0) { throw 'Existing venv is invalid or older than Python 3.11. Repair it explicitly; launcher will not delete it.' }
    & $taskPython -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Configured .venv interpreter is not a virtual environment.' }
} else {
    if (Test-Path -LiteralPath (Join-Path $taskRoot '.venv')) { throw 'An incomplete .venv exists. Inspect it; no destructive repair is performed.' }
    $taskBase = $PythonPath
    if (-not $taskBase) {
        foreach ($taskCandidate in @('python3','python')) {
            $taskFound = Get-Command $taskCandidate -ErrorAction SilentlyContinue
            if ($taskFound) {
                & $taskFound.Source -c $taskVersionCheck
                if ($LASTEXITCODE -eq 0) { $taskBase = $taskFound.Source; break }
            }
        }
    }
    if (-not $taskBase) { throw 'Supply -PythonPath with a verified Python 3.11+ executable; PATH Python may be older.' }
    & $taskBase -c $taskVersionCheck
    if ($LASTEXITCODE -ne 0) { throw 'Selected Python must be 3.11+.' }
    & $taskBase -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed.' }
}
New-Item -ItemType Directory -Path $taskState -Force | Out-Null
$taskRequirements = Join-Path $taskRoot 'requirements-m1.lock.txt'
$taskHash = (Get-FileHash -LiteralPath $taskRequirements -Algorithm SHA256).Hash
$taskDepMarker = Join-Path $taskState 'python-dependencies.sha256'
$taskInstall = -not (Test-Path -LiteralPath $taskDepMarker)
if (-not $taskInstall) { $taskInstall = (Get-Content -LiteralPath $taskDepMarker -Raw).Trim() -ne $taskHash }
& $taskPython -m pip check
if ($LASTEXITCODE -ne 0) { $taskInstall = $true }
& $taskPython -c 'import importlib.metadata as m, pathlib, sys; pins = [line.strip().split("==") for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if "==" in line]; raise SystemExit(0 if all(m.version(name) == version for name, version in pins) else 1)' $taskRequirements
if ($LASTEXITCODE -ne 0) { $taskInstall = $true }
& $taskPython -c 'import fastapi, uvicorn, aiosqlite, mcp, httpx, pytest'
if ($LASTEXITCODE -ne 0) { $taskInstall = $true }
if ($taskInstall) {
    & $taskPython -m pip install -r $taskRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    & $taskPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency validation failed.' }
    Set-Content -LiteralPath $taskDepMarker -Value $taskHash
}
$taskFrontend = Join-Path $taskRoot 'frontend'
$taskNpmHash = (Get-FileHash -LiteralPath (Join-Path $taskFrontend 'package-lock.json') -Algorithm SHA256).Hash
$taskNpmMarker = Join-Path $taskFrontend 'node_modules\.devmesh-dependencies.sha256'
if (-not (Test-Path -LiteralPath $taskNpmMarker) -or (Get-Content -LiteralPath $taskNpmMarker -Raw).Trim() -ne $taskNpmHash) {
    Push-Location $taskFrontend
    try { npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' } }
    finally { Pop-Location }
    Set-Content -LiteralPath $taskNpmMarker -Value $taskNpmHash
}
$taskApiScript = Join-Path $taskRoot 'scripts\start_normal_api.py'
$taskApiArguments = @(('"{0}"' -f $taskApiScript))
$taskApi = Start-Process -FilePath $taskPython -ArgumentList $taskApiArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskState 'api.stdout.log') -RedirectStandardError (Join-Path $taskState 'api.stderr.log')
# Start Vite directly, avoiding an ambiguous npm/cmd parent process and quoting shell commands.
$taskNode = (Get-Command node.exe).Source
$taskViteScript = Join-Path $taskFrontend 'node_modules\vite\bin\vite.js'
$taskViteArguments = @(('"{0}"' -f $taskViteScript),'--host','localhost','--port','5173','--strictPort')
$taskVite = Start-Process -FilePath $taskNode -ArgumentList $taskViteArguments -WorkingDirectory $taskFrontend -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskState 'vite.stdout.log') -RedirectStandardError (Join-Path $taskState 'vite.stderr.log')
@($taskApi,$taskVite) | ForEach-Object { @{pid=$_.Id; started=$_.StartTime.ToUniversalTime().ToString('o')} } | ConvertTo-Json | Set-Content -LiteralPath $taskMarker
$taskReady = $false
for ($taskAttempt=0; $taskAttempt -lt 60; $taskAttempt++) {
    if ($taskApi.HasExited -or $taskVite.HasExited) { throw 'An owned helper exited. Inspect .devmesh API/Vite logs; no other processes were stopped.' }
    try {
        $taskResponse = Invoke-RestMethod 'http://127.0.0.1:8420/api/projects' -TimeoutSec 1
        $taskPage = Invoke-WebRequest 'http://localhost:5173' -UseBasicParsing -TimeoutSec 1
        if ($taskPage.StatusCode -eq 200) { $taskReady = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $taskReady) { throw 'Startup did not become ready. Inspect .devmesh logs; owned process IDs are saved for inspection.' }
if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
Write-Host 'API: http://127.0.0.1:8420 | Dashboard: http://localhost:5173'
Write-Host "Owned process IDs: API $($taskApi.Id), Vite $($taskVite.Id). Stop only these after confirming identity."
Write-Host 'Antigravity activation: 1. Open MCP settings, confirm the supplied path is active, and manually verify this server entry (other entries untouched):'
@{ 'devmesh-bridge' = @{ command=$taskPython; args=@((Join-Path $taskRoot 'mcp_bridge\devmesh_mcp.py')) } } | ConvertTo-Json -Depth 4
Write-Host '2. Refresh devmesh-bridge and verify get_assignment_context, report_project_result, report_project_usage and await_next_task are listed.'
Write-Host '3. Start a project in the dashboard, copy its Antigravity instructions, and paste them into the IDE. Re-paste when polling stops or its step budget ends.'
