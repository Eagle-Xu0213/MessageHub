# MessageHub: one-shot installer for the two Windows scheduled tasks.
# ASCII only (avoid Windows encoding traps). No admin rights required.
#
# What it does:
#   1. Locate project root from THIS script's location (works after clone to any folder)
#   2. Ensure the Moect GPL source exists at <project>\moect_src (clone if missing)
#   3. Ensure a system Python (outside .venv) has the small headless dependency set
#   4. Register "MessageHubHeadless" (pythonw, --once, logon + every 5 min)
#      and "MessageHubWatchdog" (venv python + watchdog.ps1, logon + every 5 min)
#
# Usage (from any shell):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduled_tasks.ps1"
#   ... -DryRun    # resolve paths and print actions, do NOT modify the system

param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $project ".venv\Scripts\python.exe"
$moectSrc = Join-Path $project "moect_src"
$logDir = Join-Path $project "runtime"

function Find-MoectSource {
    # Same search order as moect_headless.py: MOECT_SRC, project, home, legacy.
    $candidates = @()
    if ($env:MOECT_SRC) { $candidates += $env:MOECT_SRC }
    $candidates += @(
        (Join-Path $project "moect_src"),
        (Join-Path $HOME "moect_src"),
        "D:\Projects\moect_src"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path (Join-Path $candidate "core\service.py"))) {
            return (Resolve-Path $candidate).Path
        }
    }
    return $null
}

function Find-SystemPythonExe {
    # Prefer an explicit override, then common install dirs (newest first),
    # then the py launcher, then PATH (excluding the project venv).
    $override = $env:MOECT_PYTHON
    if ($override -and (Test-Path $override)) {
        return (Resolve-Path $override).Path
    }

    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path ${env:ProgramFiles} "Python")
    )
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $dirs = Get-ChildItem -Path $root -Directory |
                Where-Object { $_.Name -match '^Python3(1[1-9]|[2-9][0-9])$' } |
                Sort-Object { [int](($_.Name -replace '\D', '')) } -Descending
            foreach ($dir in $dirs) {
                $candidate = Join-Path $dir.FullName "python.exe"
                if (Test-Path $candidate) { return $candidate }
            }
        }
    }

    try {
        $pyExe = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $pyExe) {
            $first = ($pyExe | Select-Object -First 1).ToString().Trim()
            if ($first -and (Test-Path $first)) { return $first }
        }
    } catch { }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*\.venv\*" -and $cmd.Source -notlike "*\venv\*") {
        return $cmd.Source
    }
    return $null
}

Write-Host "项目目录：$project"
if (-not (Test-Path $venvPython)) {
    throw "未找到虚拟环境 $venvPython。请先运行 setup.bat 完成安装。"
}
Write-Host "虚拟环境 Python：$venvPython"

# --- 1. Ensure Moect GPL source -------------------------------------------
$foundSrc = Find-MoectSource
if ($foundSrc) {
    Write-Host "复用 Moect 源码：$foundSrc"
} else {
    if ($DryRun) {
        Write-Host "[DryRun] 将下载 Moect 源码到：$moectSrc"
    } else {
        Write-Host "正在下载 Moect 源码到：$moectSrc"
        & git clone --depth 1 https://github.com/mofan6/Moect.git $moectSrc
        if ($LASTEXITCODE -ne 0) { throw "下载 Moect 源码失败，请检查 Git 和网络。" }
    }
}

# --- 2. Ensure system Python + headless dependencies -----------------------
$sysPython = Find-SystemPythonExe
if (-not $sysPython) {
    throw "未找到 Python 3.11 或更高版本。请先安装 Python，再重新运行本脚本。"
}
$sysDir = Split-Path $sysPython -Parent
$sysPythonw = Join-Path $sysDir "pythonw.exe"
if (-not (Test-Path $sysPythonw)) {
    throw "未找到与 $sysPython 同目录的 pythonw.exe，后台任务需要它。"
}
Write-Host "系统 Python：$sysPython"
Write-Host "后台任务程序：$sysPythonw"

if (-not $DryRun) {
    $probe = & $sysPython -c "import psutil, Cryptodome, zstandard, PIL, dotenv" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "正在安装微信采集依赖…"
        & $sysPython -m pip install --disable-pip-version-check psutil pycryptodomex zstandard pillow python-dotenv
        if ($LASTEXITCODE -ne 0) { throw "微信采集依赖安装失败：$sysPython" }
    }
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
}

# --- 3. Register scheduled tasks ------------------------------------------
$repetition = New-TimeSpan -Minutes 5
$headlessAction = New-ScheduledTaskAction `
    -Execute $sysPythonw `
    -Argument "`"$project\scripts\moect_headless.py`" --once" `
    -WorkingDirectory $project
$watchdogAction = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\wscript.exe" `
    -Argument "`"$project\scripts\watchdog_silent.vbs`"" `
    -WorkingDirectory $project

$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval $repetition)
)

$headlessSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -MultipleInstances IgnoreNew
$watchdogSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew

if ($DryRun) {
    Write-Host "[DryRun] 将为当前用户注册 MessageHub 桌面通知快捷方式。"
    Write-Host "[DryRun] 快照任务：$($headlessAction.Execute) $($headlessAction.Arguments)"
    Write-Host "[DryRun] 看门狗任务：$($watchdogAction.Execute) $($watchdogAction.Arguments)"
    Write-Host "[DryRun] 未修改计划任务。"
} else {
    try {
        & (Join-Path $PSScriptRoot 'install_toast_shortcut.ps1')
    } catch {
        Write-Warning "无法安装桌面通知快捷方式：$_"
    }
    Register-ScheduledTask -TaskName "MessageHubHeadless" -Action $headlessAction `
        -Trigger $triggers -Settings $headlessSettings -Force | Out-Null
    Register-ScheduledTask -TaskName "MessageHubWatchdog" -Action $watchdogAction `
        -Trigger $triggers -Settings $watchdogSettings -Force | Out-Null
    Write-Host ""
    Write-Host "已注册计划任务"
    foreach ($name in @("MessageHubHeadless", "MessageHubWatchdog")) {
        $task = Get-ScheduledTask -TaskName $name
        Write-Host ("  {0}: {1} | {2}" -f $name, $task.State, $task.Actions[0].Execute)
    }
    Write-Host ""
    Write-Host "安装完成。请登录微信桌面版并保持运行。可在工作台查看同步状态。"
}
