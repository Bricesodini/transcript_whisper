param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = 'Stop'

function Resolve-SingleValue {
    param($Value)
    if ($Value -is [System.Array]) {
        return $Value[0]
    }
    return $Value
}

$scriptPath = Resolve-SingleValue $MyInvocation.MyCommand.Path
[string]$scriptDir = Resolve-SingleValue (Split-Path -Parent $scriptPath)
[string]$rootDir = Resolve-SingleValue (Split-Path -Parent $scriptDir)
$configPath = [System.IO.Path]::Combine($rootDir, 'config', 'config.yaml')

$python = if ($env:PYTHON -and $env:PYTHON.Trim()) { $env:PYTHON } else { 'python' }

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $idx = $trimmed.IndexOf('=')
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($key) {
            Set-Item -Path ("Env:{0}" -f $key) -Value $value
        }
    }
}

Import-DotEnv -Path ([System.IO.Path]::Combine($rootDir, '.env.local'))

$asrEnv = [System.IO.Path]::Combine($scriptDir, 'asr_env.ps1')
if (Test-Path $asrEnv) {
    . $asrEnv
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$rootDir\src;$($env:PYTHONPATH)"
} else {
    $env:PYTHONPATH = "$rootDir\src"
}

$resolvedArgs = @("$rootDir\src\pipeline.py", "--config", $configPath)
if ($Args) { $resolvedArgs += $Args }

& $python @resolvedArgs
exit $LASTEXITCODE
