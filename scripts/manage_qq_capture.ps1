param(
    [ValidateSet('Install','Start','Stop')]
    [string]$Action = 'Install',
    [string]$NapCatRoot
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskName = 'MessageHubNapCat'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$script = Join-Path $PSScriptRoot 'qq_supervisor.py'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($task -and ($task.Actions.Arguments -notlike "*$script*" -or
                $task.Principal.UserId -notin @($owner, ($owner -split '\\')[-1]))) {
    throw '存在同名计划任务，已停止，避免覆盖其他安装。'
}
switch ($Action) {
    'Install' {
        if ($task.State -eq 'Running') { throw '请先停止现有 QQ 采集任务，再重新安装。' }
        if (-not $NapCatRoot) { throw '请通过 -NapCatRoot 指定已安装的 NapCat 目录。' }
        $root = (Resolve-Path -LiteralPath $NapCatRoot).Path
        $node = Join-Path $root 'node.exe'
        if (-not (Test-Path -LiteralPath $node)) { throw '指定目录中未找到 NapCat Windows Node 版。' }
        if (Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object ExecutablePath -eq $node) {
            throw '请先退出手动启动的 NapCat，再安装后台任务。'
        }
        $python = Join-Path $project '.venv\Scripts\pythonw.exe'
        if (-not (Test-Path -LiteralPath $python)) { throw '未找到 MessageHub 虚拟环境里的 pythonw.exe。' }
        $command = New-ScheduledTaskAction -Execute $python -Argument ('"{0}" "{1}"' -f $script,$root) -WorkingDirectory $project
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $owner
        $identity = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -Action $command -Trigger $trigger -Principal $identity -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName
    }
    'Start' { Start-ScheduledTask -TaskName $taskName }
    'Stop' {
        if ($task.State -ne 'Running') { return }
        New-Item -Path (Join-Path $project 'runtime\qq_supervisor.stop') -ItemType File -Force | Out-Null
        for ($i = 0; $i -lt 20; $i++) {
            if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { return }
            Start-Sleep -Seconds 1
        }
        throw 'NapCat 后台任务尚未停止。'
    }
}
