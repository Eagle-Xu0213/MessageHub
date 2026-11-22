# MessageHub watchdog (ASCII only - avoid encoding traps).
# Revives the API service (port 8000) and the live collector
# (instance lock port 8765) if either is dead. Called by the
# "MessageHubWatchdog" scheduled task at logon + every 5 minutes.
#
# Snapshot refresh is handled by "MessageHubHeadless" task
# (system Python, moect_headless.py --once), completely separate.

$ErrorActionPreference = "SilentlyContinue"
# Derive project root from this script's own location so the project can be
# cloned to any directory (no hardcoded author-machine paths).
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python  = Join-Path $project ".venv\Scripts\python.exe"
$log     = Join-Path $project "runtime\watchdog.log"

function Test-Port($port) {
    $c = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

function Log($msg) {
    Add-Content -Path $log -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $msg) -Encoding UTF8
}

if (-not (Test-Port 8000)) {
    Log "API down - starting uvicorn"
    Start-Process -FilePath $python `
        -ArgumentList "-m","uvicorn","messagehub.app:app","--host","127.0.0.1","--port","8000","--log-level","warning" `
        -WorkingDirectory "$project\src" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

if (-not (Test-Port 8765)) {
    Log "Collector down - starting moect_live loop (no-refresh: snapshot comes from headless task)"
    Start-Process -FilePath $python `
        -ArgumentList "$project\scripts\moect_live.py","--loop","--interval","120","--no-refresh","--selected-groups" `
        -WorkingDirectory "$project\src" -WindowStyle Hidden
}

# Keep the log small: trim to last 200 lines beyond 200 KB.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 200KB)) {
    $tail = Get-Content $log -Tail 200
    Set-Content -Path $log -Value $tail -Encoding UTF8
}
