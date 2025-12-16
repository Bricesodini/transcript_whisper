function Resolve-SingleValue {
    param($Value)
    if ($Value -is [System.Array]) {
        return $Value[0]
    }
    return $Value
}

$scriptPath = Resolve-SingleValue $MyInvocation.MyCommand.Path
[string]$scriptDir = Resolve-SingleValue (Split-Path -Parent $scriptPath)
[string]$repoRoot = Resolve-SingleValue (Split-Path -Parent $scriptDir)
[string]$workspaceRoot = Resolve-SingleValue (Split-Path -Parent $repoRoot)

$threads = $env:ASR_THREADS
if (-not $threads -or -not $threads.Trim()) {
    $cpuCount = [Environment]::ProcessorCount
    if ($cpuCount -lt 1) { $cpuCount = 1 }
    $threads = [Math]::Max(8, $cpuCount - 2)
    $env:ASR_THREADS = $threads
}

function Set-ThreadVar {
    param(
        [string]$Name,
        [string]$Value
    )
    if (-not (Get-Item ("Env:{0}" -f $Name) -ErrorAction SilentlyContinue)) {
        Set-Item -Path ("Env:{0}" -f $Name) -Value $Value
    }
}

Set-ThreadVar -Name "OMP_NUM_THREADS" -Value $threads
Set-ThreadVar -Name "OPENBLAS_NUM_THREADS" -Value $threads
Set-ThreadVar -Name "VECLIB_MAXIMUM_THREADS" -Value $threads
Set-ThreadVar -Name "NUMEXPR_NUM_THREADS" -Value $threads
Set-ThreadVar -Name "CTRANSLATE2_NUM_THREADS" -Value $threads

$additionalBins = @(
    "$workspaceRoot\.venv\Lib\site-packages\nvidia\cudnn\bin",
    "$workspaceRoot\.venv\Lib\site-packages\torch\lib"
)

foreach ($binPath in $additionalBins) {
    if (Test-Path $binPath) {
        $expanded = [System.IO.Path]::GetFullPath($binPath)
        $pathParts = ($env:PATH -split ";") | Where-Object { $_ }
        if ($pathParts -notcontains $expanded) {
            $env:PATH = "$expanded;$($env:PATH)"
        }
    }
}
