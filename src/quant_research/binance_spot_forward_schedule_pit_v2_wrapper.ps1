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
$ProgressPreference = 'SilentlyContinue'

$experimentId = 'exp_20260826_009'
$runId = 'exp_20260826_009_formal_001'
$controlParent = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot '..\..\experiments\exp_20260826_009\formal_control')
)
$reservationPath = Join-Path $controlParent ($runId + '.reservation.lock')
$ledgerPath = Join-Path $controlParent ($runId + '.stage_ledger.jsonl')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$allowedCollectorExitCodes = @(0, 10, 11, 20, 24, 30, 31, 32, 33, 34, 35)
$stageExitCodes = @{
    'CLIPBOARD_READ' = 42
    'VALIDATE' = 43
    'PRECLEAR' = 44
    'COLLECTOR' = 45
    'FINAL_CLEANUP' = 46
}
$stages = @('SELF_HASH', 'CLIPBOARD_READ', 'VALIDATE', 'PRECLEAR', 'COLLECTOR', 'FINAL_CLEANUP')
$events = @('START', 'PASS', 'FAIL', 'EXIT')

$reservationStream = $null
$ledgerStream = $null
$ledgerSeq = 0
$controlIoFailed = $false
$cleanupFailed = $false
$wrapperStageExitCode = 0
$collectorExitCode = 0
$readStarted = $false
$rawClipboard = $null
$trimmedKey = $null

function Write-StageRow {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Event,
        [AllowNull()][Nullable[int]]$ExitCode
    )

    if (($stages -notcontains $Stage) -or ($events -notcontains $Event)) {
        throw [System.InvalidOperationException]::new('invalid stage ledger enum')
    }
    if (($Event -eq 'START') -or ($Event -eq 'PASS')) {
        if ($null -ne $ExitCode) {
            throw [System.InvalidOperationException]::new('START/PASS exit must be null')
        }
        if (($Stage -eq 'SELF_HASH') -and ($Event -ne 'PASS')) {
            throw [System.InvalidOperationException]::new('SELF_HASH ledger event must be PASS')
        }
    }
    elseif ($Event -eq 'FAIL') {
        if (($Stage -eq 'SELF_HASH') -or (-not $stageExitCodes.ContainsKey($Stage))) {
            throw [System.InvalidOperationException]::new('invalid FAIL stage')
        }
        if ($ExitCode -ne $stageExitCodes[$Stage]) {
            throw [System.InvalidOperationException]::new('invalid FAIL exit')
        }
    }
    elseif ($Event -eq 'EXIT') {
        if (($Stage -ne 'COLLECTOR') -or ($null -eq $ExitCode) -or
            ($allowedCollectorExitCodes -notcontains [int]$ExitCode)) {
            throw [System.InvalidOperationException]::new('invalid collector EXIT')
        }
    }

    $script:ledgerSeq += 1
    $exitJson = if ($null -eq $ExitCode) { 'null' } else { [string][int]$ExitCode }
    $line = (
        '{"event":"' + $Event + '","exit_code":' + $exitJson +
        ',"seq":' + [string]$script:ledgerSeq + ',"stage":"' + $Stage + '"}' + "`n"
    )
    $bytes = $utf8NoBom.GetBytes($line)
    $ledgerStream.Write($bytes, 0, $bytes.Length)
    $ledgerStream.Flush($true)
}

# Self binding happens before reservation and before every clipboard operation.
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
    if (-not [string]::Equals(
        $actualWrapperSha256,
        $ExpectedWrapperSha256,
        [System.StringComparison]::Ordinal
    )) {
        exit 41
    }
}
catch {
    exit 41
}

# Acquiring the CreateNew handle irreversibly consumes this run ID.
try {
    $reservationStream = [System.IO.FileStream]::new(
        $reservationPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
}
catch {
    $exceptionCursor = $_.Exception
    $alreadyExists = $false
    while ($null -ne $exceptionCursor) {
        $lowWord = ([int]$exceptionCursor.HResult) -band 0xFFFF
        if (($lowWord -eq 80) -or ($lowWord -eq 183)) {
            $alreadyExists = $true
        }
        $exceptionCursor = $exceptionCursor.InnerException
    }
    if ($alreadyExists) {
        exit 47
    }
    exit 40
}

try {
    $reservationBytes = $utf8NoBom.GetBytes(
        '{"experiment_id":"exp_20260826_009","run_id":"exp_20260826_009_formal_001"}' + "`n"
    )
    $reservationStream.Write($reservationBytes, 0, $reservationBytes.Length)
    $reservationStream.Flush($true)
    $reservationStream.Dispose()
    $reservationStream = $null
}
catch {
    try {
        if ($null -ne $reservationStream) {
            $reservationStream.Dispose()
        }
    }
    catch {}
    exit 40
}

try {
    $ledgerStream = [System.IO.FileStream]::new(
        $ledgerPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    Write-StageRow -Stage 'SELF_HASH' -Event 'PASS' -ExitCode $null
    Write-StageRow -Stage 'CLIPBOARD_READ' -Event 'START' -ExitCode $null
    $readStarted = $true
}
catch {
    try {
        if ($null -ne $ledgerStream) {
            $ledgerStream.Dispose()
        }
    }
    catch {}
    exit 40
}

try {
    $readSucceeded = $false
    try {
        $rawClipboard = Get-Clipboard -Raw
        $readSucceeded = $true
    }
    catch {
        $wrapperStageExitCode = 42
        try {
            Write-StageRow -Stage 'CLIPBOARD_READ' -Event 'FAIL' -ExitCode 42
        }
        catch {
            $controlIoFailed = $true
        }
    }
    if ($readSucceeded -and (-not $controlIoFailed)) {
        try {
            Write-StageRow -Stage 'CLIPBOARD_READ' -Event 'PASS' -ExitCode $null
        }
        catch {
            $controlIoFailed = $true
        }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $validationFailed = $false
        try {
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
            if ($invalidKey) {
                throw [System.InvalidOperationException]::new('invalid clipboard')
            }
        }
        catch {
            $validationFailed = $true
            $wrapperStageExitCode = 43
        }
        try {
            if ($validationFailed) {
                Write-StageRow -Stage 'VALIDATE' -Event 'FAIL' -ExitCode 43
            }
            else {
                Write-StageRow -Stage 'VALIDATE' -Event 'PASS' -ExitCode $null
            }
        }
        catch {
            $controlIoFailed = $true
        }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $preclearFailed = $false
        try {
            Set-Clipboard -Value ''
        }
        catch {
            $preclearFailed = $true
            $wrapperStageExitCode = 44
        }
        try {
            if ($preclearFailed) {
                Write-StageRow -Stage 'PRECLEAR' -Event 'FAIL' -ExitCode 44
            }
            else {
                Write-StageRow -Stage 'PRECLEAR' -Event 'PASS' -ExitCode $null
            }
        }
        catch {
            $controlIoFailed = $true
        }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        try {
            Write-StageRow -Stage 'COLLECTOR' -Event 'START' -ExitCode $null
        }
        catch {
            $controlIoFailed = $true
        }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $collectorLaunchFailed = $false
        try {
            $env:BINANCE_READ_ONLY_API_KEY = $trimmedKey
            & .venv\Scripts\python.exe -B `
                src\quant_research\binance_spot_forward_schedule_pit_v2.py `
                --expected-wrapper-sha256 $ExpectedWrapperSha256 `
                --expected-collector-sha256 $ExpectedCollectorSha256 `
                --expected-loader-sha256 $ExpectedLoaderSha256 `
                --expected-source-contract-sha256 $ExpectedSourceContractSha256 `
                --expected-schema-sha256 $ExpectedSchemaSha256 `
                --expected-parameters-sha256 $ExpectedParametersSha256 `
                --expected-tests-sha256 $ExpectedTestsSha256
            $collectorExitCode = $LASTEXITCODE
        }
        catch {
            $collectorLaunchFailed = $true
            try {
                Write-StageRow -Stage 'COLLECTOR' -Event 'FAIL' -ExitCode 45
            }
            catch {
                $controlIoFailed = $true
            }
            $wrapperStageExitCode = 45
        }
        if ((-not $collectorLaunchFailed) -and (-not $controlIoFailed)) {
            if ($allowedCollectorExitCodes -contains $collectorExitCode) {
                try {
                    Write-StageRow -Stage 'COLLECTOR' -Event 'EXIT' -ExitCode $collectorExitCode
                }
                catch {
                    $controlIoFailed = $true
                }
            }
            else {
                $wrapperStageExitCode = 45
                try {
                    Write-StageRow -Stage 'COLLECTOR' -Event 'FAIL' -ExitCode 45
                }
                catch {
                    $controlIoFailed = $true
                }
            }
        }
    }
}
finally {
    try {
        if (Test-Path Env:BINANCE_READ_ONLY_API_KEY) {
            Remove-Item Env:BINANCE_READ_ONLY_API_KEY -ErrorAction Stop
        }
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

    if (-not $controlIoFailed) {
        try {
            if ($cleanupFailed) {
                Write-StageRow -Stage 'FINAL_CLEANUP' -Event 'FAIL' -ExitCode 46
            }
            else {
                Write-StageRow -Stage 'FINAL_CLEANUP' -Event 'PASS' -ExitCode $null
            }
        }
        catch {
            $controlIoFailed = $true
        }
    }
    try {
        if ($null -ne $ledgerStream) {
            $ledgerStream.Dispose()
        }
    }
    catch {
        $controlIoFailed = $true
    }
}

if ($cleanupFailed) {
    exit 46
}
if ($controlIoFailed) {
    exit 40
}
if ($wrapperStageExitCode -ne 0) {
    exit $wrapperStageExitCode
}
if ($allowedCollectorExitCodes -contains $collectorExitCode) {
    exit $collectorExitCode
}
exit 45
