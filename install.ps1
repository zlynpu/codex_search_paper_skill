[CmdletBinding()]
param(
    [ValidateSet("codex", "claude-code", "qoder", "qoderwork", "all", "custom")]
    [string[]]$Harness = @("codex"),
    [ValidateSet("codex", "claude-code", "qoder", "none", "custom")]
    [string]$AgentHarness,
    [string]$Target,
    [string]$Config,
    [string]$ArchiveRoot,
    [string]$Time,
    [string]$Timezone,
    [int]$Total,
    [string[]]$Quota,
    [string[]]$Terms,
    [switch]$NoSchedule
)

$ErrorActionPreference = "Stop"
$python = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @()
if ($python) {
    $pythonArgs += "-3"
} else {
    $python = Get-Command python -ErrorAction Stop
}
$installer = Join-Path $PSScriptRoot "scripts\install.py"
$pythonArgs += $installer
foreach ($item in $Harness) { $pythonArgs += @("--harness", $item) }
if ($AgentHarness) { $pythonArgs += @("--agent-harness", $AgentHarness) }
if ($Target) { $pythonArgs += @("--target", $Target) }
if ($Config) { $pythonArgs += @("--config", $Config) }
if ($ArchiveRoot) { $pythonArgs += @("--archive-root", $ArchiveRoot) }
if ($Time) { $pythonArgs += @("--time", $Time) }
if ($Timezone) { $pythonArgs += @("--timezone", $Timezone) }
if ($PSBoundParameters.ContainsKey("Total")) { $pythonArgs += @("--total", [string]$Total) }
foreach ($item in $Quota) { $pythonArgs += @("--quota", $item) }
foreach ($item in $Terms) { $pythonArgs += @("--terms", $item) }
if ($NoSchedule) { $pythonArgs += "--no-schedule" }

& $python.Source @pythonArgs
exit $LASTEXITCODE
