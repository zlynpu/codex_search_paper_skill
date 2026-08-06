[CmdletBinding()]
param(
    [ValidateSet("install", "uninstall", "status", "run-now")]
    [string]$Action = "install",
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$RunnerPath,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [string]$TaskName = "DailyPaperDigest"
)

$ErrorActionPreference = "Stop"
if ($Action -eq "uninstall") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed=$TaskName"
    exit 0
}
if ($Action -eq "status") {
    Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
    exit 0
}
if ($Action -eq "run-now") {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "started=$TaskName"
    exit 0
}

$arguments = '"{0}" --config "{1}" --if-due' -f $RunnerPath, $ConfigPath
$taskAction = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments
$start = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $start
$trigger.Repetition.Interval = "PT1M"
$trigger.Repetition.Duration = "P3650D"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Description "Checks the configured daily paper digest time and runs it once per configured day." -Force | Out-Null
Write-Output "installed=$TaskName"
