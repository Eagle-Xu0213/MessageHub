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
    throw 'An unrelated scheduled task has the same name.'
}
switch ($Action) {
    'Install' {
        if ($task.State -eq 'Running') { throw 'Stop the existing supervisor before reinstalling.' }
        if (-not $NapCatRoot) { throw 'Supply the installed NapCat directory with -NapCatRoot.' }
        $root = (Resolve-Path -LiteralPath $NapCatRoot).Path
        $node = Join-Path $root 'node.exe'
        if (-not (Test-Path -LiteralPath $node)) { throw 'NapCat Node package not found.' }
        if (Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object ExecutablePath -eq $node) {
            throw 'Exit the manually started NapCat instance before installing.'
        }
        $python = Join-Path $project '.venv\Scripts\pythonw.exe'
        if (-not (Test-Path -LiteralPath $python)) { throw 'MessageHub pythonw.exe not found.' }
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
        throw 'NapCat supervisor has not stopped yet.'
    }
}
