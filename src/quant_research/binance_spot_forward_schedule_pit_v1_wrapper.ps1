param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedWrapperSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedCollectorSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedLoaderSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedSourceContractSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedSchemaSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedParametersSha256,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedTestsSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$savedExitCode = 20
$cleanupFailed = $false
$rawClipboard = $null
$trimmedKey = $null

try {
    $wrapperBytes = [System.IO.File]::ReadAllBytes($PSCommandPath)
    $wrapperHasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $wrapperDigest = $wrapperHasher.ComputeHash($wrapperBytes)
        $actualWrapperSha256 = (
            [System.BitConverter]::ToString($wrapperDigest)
        ).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $wrapperHasher.Dispose()
    }
}
catch {
    exit $savedExitCode
}
if (-not [string]::Equals(
    $actualWrapperSha256,
    $ExpectedWrapperSha256,
    [System.StringComparison]::Ordinal
)) {
    exit $savedExitCode
}

$savedExitCode = 11

try {
    $rawClipboard = Get-Clipboard -Raw
    if ($null -ne $rawClipboard) {
        $rawClipboard = [string]$rawClipboard
        $trimmedKey = $rawClipboard.Trim()
    }
    $invalidKey = (
        [string]::IsNullOrWhiteSpace($trimmedKey) -or
        $rawClipboard.Contains("`r") -or
        $rawClipboard.Contains("`n") -or
        $rawClipboard.Contains([char]0)
    )
    if (-not $invalidKey) {
        Set-Clipboard -Value ''
        $env:BINANCE_READ_ONLY_API_KEY = $trimmedKey
        & .venv\Scripts\python.exe -B `
            src\quant_research\binance_spot_forward_schedule_pit_v1.py `
            --expected-wrapper-sha256 $ExpectedWrapperSha256 `
            --expected-collector-sha256 $ExpectedCollectorSha256 `
            --expected-loader-sha256 $ExpectedLoaderSha256 `
            --expected-source-contract-sha256 $ExpectedSourceContractSha256 `
            --expected-schema-sha256 $ExpectedSchemaSha256 `
            --expected-parameters-sha256 $ExpectedParametersSha256 `
            --expected-tests-sha256 $ExpectedTestsSha256
        $savedExitCode = $LASTEXITCODE
    }
}
catch {
    if ($savedExitCode -eq 0) {
        $savedExitCode = 11
    }
}
finally {
    try {
        Remove-Item Env:BINANCE_READ_ONLY_API_KEY -ErrorAction Stop
    }
    catch {
        $cleanupFailed = $true
    }
    try {
        Set-Clipboard -Value ''
    }
    catch {
        $cleanupFailed = $true
    }
    try {
        Clear-Variable -Name rawClipboard -ErrorAction Stop
    }
    catch {
        $cleanupFailed = $true
    }
    try {
        Clear-Variable -Name trimmedKey -ErrorAction Stop
    }
    catch {
        $cleanupFailed = $true
    }
    Clear-Variable -Name invalidKey -ErrorAction SilentlyContinue
}

if ($cleanupFailed -and $savedExitCode -eq 0) {
    $savedExitCode = 12
}
exit $savedExitCode
