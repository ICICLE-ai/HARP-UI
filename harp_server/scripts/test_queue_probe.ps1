[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$OscUser,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SshKeyPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$KnownHostsPath,

    [ValidateRange(3, 120)]
    [int]$TimeoutSeconds = 15,

    [switch]$StartServer,

    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$serverRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $serverRoot '.venv\Scripts\python.exe'
$reporter = Join-Path $PSScriptRoot 'probe_osc_queues.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "HARP virtual environment not found at $python. Install harp_server/requirements.txt first."
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw 'OpenSSH client not found. Install the Windows OpenSSH Client capability first.'
}
if (-not (Test-Path -LiteralPath $SshKeyPath -PathType Leaf)) {
    throw 'The SSH private-key path does not name a file.'
}
if (-not (Test-Path -LiteralPath $KnownHostsPath -PathType Leaf)) {
    throw 'The known_hosts path does not name a file.'
}

$variables = @(
    'HARP_OSC_QUEUE_PROBE',
    'HARP_OSC_SSH_USER',
    'HARP_OSC_SSH_KEY',
    'HARP_OSC_SSH_KNOWN_HOSTS',
    'HARP_QUEUE_REFRESH_SECONDS',
    'HARP_QUEUE_MAX_AGE_SECONDS',
    'HARP_QUEUE_PROBE_TIMEOUT_SECONDS',
    'HARP_BUILD_PYTHON'
)
$previous = @{}
foreach ($name in $variables) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    $env:HARP_OSC_QUEUE_PROBE = '1'
    $env:HARP_OSC_SSH_USER = $OscUser
    $env:HARP_OSC_SSH_KEY = (Resolve-Path -LiteralPath $SshKeyPath).Path
    $env:HARP_OSC_SSH_KNOWN_HOSTS = (Resolve-Path -LiteralPath $KnownHostsPath).Path
    $env:HARP_QUEUE_REFRESH_SECONDS = '86400'
    $env:HARP_QUEUE_MAX_AGE_SECONDS = '172800'
    $env:HARP_QUEUE_PROBE_TIMEOUT_SECONDS = [string]$TimeoutSeconds
    # The estimator runner needs the same ML-enabled environment as local builds.
    $env:HARP_BUILD_PYTHON = $python

    Write-Host 'Running one HARP OSC queue probe. Key and known_hosts contents will not be printed.'
    Push-Location $serverRoot
    try {
        & $python $reporter
        if ($LASTEXITCODE -ne 0) {
            throw 'One or more OSC queue probes failed. Review the safe JSON errors above.'
        }

        Write-Host 'Queue probe succeeded for Ascend, Pitzer, and Cardinal.'
        if ($StartServer) {
            Write-Host "Starting HARP at http://127.0.0.1:$Port with the daily queue probe enabled."
            & $python -m uvicorn harp_server.app:app --host 127.0.0.1 --port $Port
            if ($LASTEXITCODE -ne 0) {
                throw "HARP exited with code $LASTEXITCODE."
            }
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $variables) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}

