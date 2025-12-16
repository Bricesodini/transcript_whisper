param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$PythonPath,

    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,

    [string]$ExtraArgs
)

$ErrorActionPreference = 'Stop'

$asrEnv = Join-Path $RepoRoot 'bin\asr_env.ps1'
if (Test-Path $asrEnv) {
    . $asrEnv
}

function Parse-ExtraArgs {
    param([string]$ArgsString)
    if ([string]::IsNullOrWhiteSpace($ArgsString)) {
        return @()
    }
    $errors = $null
    $tokens = [System.Management.Automation.PSParser]::Tokenize($ArgsString, [ref]$errors)
    $result = @()
    foreach ($token in $tokens) {
        if ($token.Type -in 'CommandArgument','String') {
            $result += $token.Content
        }
    }
    return $result
}

try {
    $mediaDir = Split-Path -Path $InputPath
    if ($mediaDir) {
        Set-Location -LiteralPath $mediaDir
    }

    Write-Host '--- Transcribe Suite ---'
    Write-Host ("Media : " + $InputPath)
    if ($ExtraArgs) {
        Write-Host ("Args  : " + $ExtraArgs)
    }

    $baseArgs = @("--config", $ConfigPath, "--input", $InputPath)
    $parsedArgs = Parse-ExtraArgs -ArgsString $ExtraArgs

    & $PythonPath "$RepoRoot\src\pipeline.py" @baseArgs @parsedArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Error "Pipeline terminee avec le code $code"
    }
}
catch {
    Write-Error $_
    if (-not $code) { $code = 1 }
}
finally {
    Write-Host ""
    Read-Host 'Traitement termine. Appuyez sur Entree pour fermer cette fenetre'
    exit $code
}
