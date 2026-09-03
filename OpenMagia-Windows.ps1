$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Runtime = Join-Path $Root "data\runtime"
$PidFile = Join-Path $Runtime "server.pid"
$LogFile = Join-Path $Runtime "server-windows.log"
$ErrorLog = Join-Path $Runtime "server-windows-error.log"
$Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
$Port = 8730
try { $Port = (Get-Content (Join-Path $Root "config.json") -Raw | ConvertFrom-Json).port } catch {}
$Url = "http://127.0.0.1:$Port"

function Get-RunningVersion {
    try { return (Invoke-RestMethod "$Url/api/state" -TimeoutSec 1).engine.app_version } catch { return "" }
}

if ((Get-RunningVersion) -ne $Version -and (Test-Path $PidFile)) {
    $RecordedPid = (Get-Content $PidFile -Raw) -replace '[^0-9]', ''
    if ($RecordedPid) {
        $Managed = Get-CimInstance Win32_Process -Filter "ProcessId=$RecordedPid" -ErrorAction SilentlyContinue
        if ($Managed -and $Managed.CommandLine -like "*$Root*server.py*") {
            Stop-Process -Id ([int]$RecordedPid) -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 300
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

if ((Get-RunningVersion) -ne $Version) {
    if (Get-RunningVersion) { throw "Port $Port is serving a different OpenMagia version. Stop that copy and try again." }
    $Python = (Get-Command python.exe -ErrorAction SilentlyContinue)
    if (-not $Python) { $Python = (Get-Command python3.exe -ErrorAction SilentlyContinue) }
    if (-not $Python) { throw "Python 3 is required. Install Python, then run this launcher again." }
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    $Process = Start-Process -FilePath $Python.Source -ArgumentList "-u", "server.py" -WorkingDirectory $Root -RedirectStandardOutput $LogFile -RedirectStandardError $ErrorLog -WindowStyle Hidden -PassThru
    Set-Content -Path $PidFile -Value $Process.Id
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        if ((Get-RunningVersion) -eq $Version) { break }
        if ($Process.HasExited) { break }
    }
}

if ((Get-RunningVersion) -ne $Version) { throw "OpenMagia failed to start. See $ErrorLog" }
Start-Process $Url
Write-Host "OpenMagia $Version is ready at $Url"
