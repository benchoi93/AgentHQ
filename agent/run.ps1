# AgentHQ Agent — Windows PowerShell launcher
# Usage: .\run.ps1 [start|stop|status]

param(
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start"
)

$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $AgentDir "agent.pid"
$LogFile = Join-Path $AgentDir "agent.log"

function Start-Agent {
    if (Test-Path $PidFile) {
        $agentPid = Get-Content $PidFile
        try {
            $proc = Get-Process -Id $agentPid -ErrorAction Stop
            Write-Host "Agent already running (PID $agentPid)"
            return
        } catch {
            Remove-Item $PidFile -Force
        }
    }

    Write-Host "Starting AgentHQ agent..."
    $process = Start-Process -FilePath "python" `
        -ArgumentList "-m", "agenthq_agent", "--config", "config.yaml" `
        -WorkingDirectory $AgentDir `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError "$LogFile.err" `
        -PassThru `
        -WindowStyle Hidden

    $process.Id | Out-File -FilePath $PidFile -Encoding ascii
    Write-Host "Agent started (PID $($process.Id))"
    Write-Host "Log: $LogFile"
}

function Stop-Agent {
    if (-not (Test-Path $PidFile)) {
        Write-Host "Agent not running (no PID file)"
        return
    }

    $agentPid = Get-Content $PidFile
    try {
        $proc = Get-Process -Id $agentPid -ErrorAction Stop
        Stop-Process -Id $agentPid -Force
        Write-Host "Agent stopped (PID $agentPid)"
    } catch {
        Write-Host "Agent process not found (PID $agentPid)"
    }
    Remove-Item $PidFile -Force
}

function Get-AgentStatus {
    if (-not (Test-Path $PidFile)) {
        Write-Host "Agent not running"
        return
    }

    $agentPid = Get-Content $PidFile
    try {
        $proc = Get-Process -Id $agentPid -ErrorAction Stop
        Write-Host "Agent running (PID $agentPid, CPU: $($proc.CPU)s, Memory: $([math]::Round($proc.WorkingSet64/1MB, 1))MB)"
    } catch {
        Write-Host "Agent not running (stale PID file)"
        Remove-Item $PidFile -Force
    }
}

switch ($Action) {
    "start"  { Start-Agent }
    "stop"   { Stop-Agent }
    "status" { Get-AgentStatus }
}
