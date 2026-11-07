# Start every service of this project as DETACHED processes.
#
# Why detached: a background bash task is capped at 1 hour, and when the
# runtime kills the task the service dies with it (chatgpt-pool-ng already
# died that way once). Start-Process spawns a process that outlives the task.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 mis-parses UTF-8
# files without a BOM when they contain non-ASCII characters.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File deploy\start_services.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\start_services.ps1 -Stop
param([switch]$Stop)

$ErrorActionPreference = 'Continue'

# Resolve paths relative to the repository root so the script works from any
# checkout location.
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

$services = @(
    @{ Name = 'chatgpt-pool-ng';  Exe = "$Repo\.venv\Scripts\python.exe"
       Args = @('main.py'); WorkDir = $Repo
       Log = "$Repo\_tmp\server"; Port = 8000 }
    # The two Go upstream pools live in upstreams/ and run locally: the console
    # talks to 127.0.0.1 directly, no cross-host SSH tunnel needed.
    @{ Name = 'minimax2api';     Exe = "$Repo\upstreams\minimax2api\backend\bin\minimax2api.exe"
       Args = @('-addr', '127.0.0.1:8096', '-data', 'data'); WorkDir = "$Repo\upstreams\minimax2api"
       Log = "$Repo\upstreams\minimax2api\run"; Port = 8096 }
    @{ Name = 'grok2api';        Exe = "$Repo\upstreams\grok2api\backend\bin\grok2api.exe"
       Args = @('--config', 'config.yaml'); WorkDir = "$Repo\upstreams\grok2api"
       Log = "$Repo\upstreams\grok2api\run"; Port = 8002 }
    @{ Name = 'grok-register';    Exe = 'D:\xi\grok-register\.venv\Scripts\python.exe'
       Args = @('-m', 'web.server'); WorkDir = 'D:\xi\grok-register'
       Log = 'D:\xi\grok-register\webui'; Port = 8092 }
    @{ Name = 'reg-exits-mihomo'; Exe = 'D:\xi\Ai\smart-proxy\mihomo-split.exe'
       Args = @('-f', 'config.yaml', '-d', '.'); WorkDir = 'D:\xi\Ai\smart-proxy\reg-exits'
       Log = 'D:\xi\Ai\smart-proxy\reg-exits\svc'; Port = 7940 }
)

function Get-Listener([int]$Port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

foreach ($s in $services) {
    $pidNow = Get-Listener $s.Port
    if ($Stop) {
        if ($pidNow) {
            Stop-Process -Id $pidNow -Force -ErrorAction SilentlyContinue
            Write-Output ("  STOP {0} (pid {1})" -f $s.Name, $pidNow)
        } else {
            Write-Output ("  {0} not running" -f $s.Name)
        }
        continue
    }
    if ($pidNow) {
        Write-Output ("  SKIP {0} already up (pid {1}, :{2})" -f $s.Name, $pidNow, $s.Port)
        continue
    }
    if (-not (Test-Path $s.Exe)) {
        Write-Output ("  MISS {0} executable not found: {1}" -f $s.Name, $s.Exe)
        continue
    }
    $logDir = Split-Path $s.Log -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $out = "{0}.out.log" -f $s.Log
    $err = "{0}.err.log" -f $s.Log
    $p = Start-Process -FilePath $s.Exe -ArgumentList $s.Args -WorkingDirectory $s.WorkDir `
        -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
    Write-Output ("  START {0}  pid={1}  :{2}  log={3}" -f $s.Name, $p.Id, $s.Port, $out)
}

if (-not $Stop) {
    Start-Sleep -Seconds 12
    Write-Output ""
    Write-Output "=== status ==="
    foreach ($s in $services) {
        $pidNow = Get-Listener $s.Port
        if ($pidNow) {
            Write-Output ("  {0,-18} :{1}  ALIVE  pid={2}" -f $s.Name, $s.Port, $pidNow)
        } else {
            Write-Output ("  {0,-18} :{1}  FAILED  see {2}.err.log" -f $s.Name, $s.Port, $s.Log)
        }
    }
}
