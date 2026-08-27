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

$experimentId = 'exp_20260827_003'
$runId = 'exp_20260827_003_formal_001'
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$envFilePath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '.env.binance.local'))
$controlParent = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot 'experiments\exp_20260827_003\formal_control')
)
$reservationPath = Join-Path $controlParent ($runId + '.reservation.lock')
$ledgerPath = Join-Path $controlParent ($runId + '.stage_ledger.jsonl')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)
$allowedCollectorExitCodes = @(0, 10, 11, 20, 24, 30, 31, 32, 33, 34, 35)
$stageExitCodes = @{
    'ENV_FILE_READ' = 42
    'VALIDATE' = 43
    'HANDOFF' = 44
    'COLLECTOR' = 45
    'FINAL_CLEANUP' = 46
}
$stages = @('SELF_HASH', 'ENV_FILE_READ', 'VALIDATE', 'HANDOFF', 'COLLECTOR', 'FINAL_CLEANUP')
$events = @('START', 'PASS', 'FAIL', 'EXIT')

$reservationStream = $null
$ledgerStream = $null
$envStream = $null
$ledgerSeq = 0
$controlIoFailed = $false
$cleanupFailed = $false
$wrapperStageExitCode = 0
$collectorExitCode = 0
$envOwned = $false
$rawBytes = $null
$textValue = $null
$keyValue = $null
$keyMatch = $null
$lineValue = $null
$buffer = $null

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

function Test-PlainEnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    $expected = [System.IO.Path]::GetFullPath((Join-Path $repoRoot '.env.binance.local'))
    if (-not [string]::Equals($Path, $expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    try {
        $attributes = [System.IO.File]::GetAttributes($Path)
    }
    catch {
        return $false
    }
    if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
        return $false
    }
    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return $false
    }
    return $true
}

# Self binding is the only action before irrevocably reserving the run.
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

# Acquiring this CreateNew handle permanently consumes the run ID.
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
    if ($alreadyExists) { exit 47 }
    exit 40
}

try {
    $reservationBytes = $utf8NoBom.GetBytes(
        '{"experiment_id":"exp_20260827_003","run_id":"exp_20260827_003_formal_001"}' + "`n"
    )
    $reservationStream.Write($reservationBytes, 0, $reservationBytes.Length)
    $reservationStream.Flush($true)
    $reservationStream.Dispose()
    $reservationStream = $null
}
catch {
    try { if ($null -ne $reservationStream) { $reservationStream.Dispose() } } catch {}
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
    Write-StageRow -Stage 'ENV_FILE_READ' -Event 'START' -ExitCode $null
}
catch {
    try { if ($null -ne $ledgerStream) { $ledgerStream.Dispose() } } catch {}
    exit 40
}

try {
    $readFailed = $false
    try {
        # Recheck after reservation, then perform exactly one read-only open.
        if (-not (Test-PlainEnvFile -Path $envFilePath)) {
            throw [System.IO.IOException]::new('env file unavailable')
        }
        $envStream = [System.IO.FileStream]::new(
            $envFilePath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $buffer = New-Object byte[] 4097
        $totalRead = 0
        while ($totalRead -lt 4097) {
            $readNow = $envStream.Read($buffer, $totalRead, 4097 - $totalRead)
            if ($readNow -eq 0) { break }
            $totalRead += $readNow
        }
        $envStream.Dispose()
        $envStream = $null
        if ($totalRead -gt 4096) {
            throw [System.IO.InvalidDataException]::new('env file exceeds cap')
        }
        $rawBytes = New-Object byte[] $totalRead
        if ($totalRead -gt 0) {
            [System.Array]::Copy($buffer, 0, $rawBytes, 0, $totalRead)
        }
        $buffer = $null
        $totalRead = 0
        $readNow = 0
    }
    catch {
        try { if ($null -ne $envStream) { $envStream.Dispose() } } catch {}
        $envStream = $null
        $readFailed = $true
        $wrapperStageExitCode = 42
    }
    try {
        if ($readFailed) {
            Write-StageRow -Stage 'ENV_FILE_READ' -Event 'FAIL' -ExitCode 42
        }
        else {
            Write-StageRow -Stage 'ENV_FILE_READ' -Event 'PASS' -ExitCode $null
        }
    }
    catch { $controlIoFailed = $true }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $validationFailed = $false
        try {
            $textValue = $utf8Strict.GetString($rawBytes)
            $textStart = 0
            if (($rawBytes.Length -ge 3) -and
                ($rawBytes[0] -eq 0xEF) -and ($rawBytes[1] -eq 0xBB) -and
                ($rawBytes[2] -eq 0xBF)) {
                if (($textValue.Length -eq 0) -or ([int]$textValue[0] -ne 0xFEFF)) {
                    throw [System.IO.InvalidDataException]::new('invalid leading BOM')
                }
                $textStart = 1
            }

            for ($asciiIndex = $textStart; $asciiIndex -lt $textValue.Length; $asciiIndex++) {
                $codePoint = [int]$textValue[$asciiIndex]
                if (($codePoint -eq 0) -or ($codePoint -gt 0x7F)) {
                    throw [System.IO.InvalidDataException]::new('non-ASCII or NUL')
                }
            }

            $assignmentCount = 0
            $cursor = $textStart
            while ($cursor -le $textValue.Length) {
                $lineStart = $cursor
                while (($cursor -lt $textValue.Length) -and
                    ($textValue[$cursor] -ne "`r") -and
                    ($textValue[$cursor] -ne "`n")) {
                    $cursor += 1
                }
                $lineValue = $textValue.Substring($lineStart, $cursor - $lineStart)

                if ([System.Text.RegularExpressions.Regex]::IsMatch(
                    $lineValue,
                    '\A[ \t]*\z',
                    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
                )) {
                    # Blank line.
                }
                elseif ([System.Text.RegularExpressions.Regex]::IsMatch(
                    $lineValue,
                    '\A[ \t]*#[\t\x20-\x7E]*\z',
                    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
                )) {
                    # Comment line; inline comments are not accepted here.
                }
                else {
                    $keyMatch = [System.Text.RegularExpressions.Regex]::Match(
                        $lineValue,
                        '\ABINANCE_READ_ONLY_API_KEY[ \t]*=[ \t]*([A-Za-z0-9_-]+)[ \t]*\z',
                        [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
                    )
                    if (-not $keyMatch.Success) {
                        throw [System.IO.InvalidDataException]::new('invalid env line')
                    }
                    $assignmentCount += 1
                    if ($assignmentCount -ne 1) {
                        throw [System.IO.InvalidDataException]::new('duplicate assignment')
                    }
                    $keyValue = $keyMatch.Groups[1].Value
                }

                if ($cursor -eq $textValue.Length) { break }
                if ($textValue[$cursor] -eq "`r") {
                    if ((($cursor + 1) -ge $textValue.Length) -or
                        ($textValue[$cursor + 1] -ne "`n")) {
                        throw [System.IO.InvalidDataException]::new('bare CR')
                    }
                    $cursor += 2
                }
                else {
                    $cursor += 1
                }
                if ($cursor -eq $textValue.Length) { break }
            }

            if (($assignmentCount -ne 1) -or [string]::IsNullOrEmpty($keyValue)) {
                throw [System.IO.InvalidDataException]::new('invalid env grammar')
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
        catch { $controlIoFailed = $true }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $handoffFailed = $false
        try {
            if (Test-Path Env:BINANCE_READ_ONLY_API_KEY) {
                throw [System.InvalidOperationException]::new('preexisting child env')
            }
            $env:BINANCE_READ_ONLY_API_KEY = $keyValue
            $envOwned = $true
            Clear-Variable -Name rawBytes -ErrorAction Stop
            Clear-Variable -Name textValue -ErrorAction Stop
            Clear-Variable -Name keyValue -ErrorAction Stop
            Clear-Variable -Name keyMatch -ErrorAction Stop
            Clear-Variable -Name lineValue -ErrorAction Stop
        }
        catch {
            $handoffFailed = $true
            $wrapperStageExitCode = 44
        }
        try {
            if ($handoffFailed) {
                Write-StageRow -Stage 'HANDOFF' -Event 'FAIL' -ExitCode 44
            }
            else {
                Write-StageRow -Stage 'HANDOFF' -Event 'PASS' -ExitCode $null
            }
        }
        catch { $controlIoFailed = $true }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        try { Write-StageRow -Stage 'COLLECTOR' -Event 'START' -ExitCode $null }
        catch { $controlIoFailed = $true }
    }

    if (($wrapperStageExitCode -eq 0) -and (-not $controlIoFailed)) {
        $collectorLaunchFailed = $false
        try {
            $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
            $collectorPath = Join-Path $repoRoot 'src\quant_research\binance_spot_forward_schedule_pit_v6.py'
            & $pythonPath -B $collectorPath `
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
            $wrapperStageExitCode = 45
        }
        if ((-not $collectorLaunchFailed) -and
            ($allowedCollectorExitCodes -notcontains $collectorExitCode)) {
            $wrapperStageExitCode = 45
        }
        try {
            if ($wrapperStageExitCode -eq 45) {
                Write-StageRow -Stage 'COLLECTOR' -Event 'FAIL' -ExitCode 45
            }
            else {
                Write-StageRow -Stage 'COLLECTOR' -Event 'EXIT' -ExitCode $collectorExitCode
            }
        }
        catch { $controlIoFailed = $true }
    }
}
finally {
    try {
        if ($envOwned) {
            Remove-Item Env:BINANCE_READ_ONLY_API_KEY -ErrorAction Stop
            $envOwned = $false
        }
    }
    catch { $cleanupFailed = $true }
    foreach ($sensitiveName in @('rawBytes', 'textValue', 'keyValue', 'keyMatch', 'lineValue', 'buffer')) {
        try { Clear-Variable -Name $sensitiveName -ErrorAction Stop }
        catch { $cleanupFailed = $true }
    }

    if (-not $controlIoFailed) {
        try {
            if ($cleanupFailed) {
                Write-StageRow -Stage 'FINAL_CLEANUP' -Event 'FAIL' -ExitCode 46
            }
            else {
                Write-StageRow -Stage 'FINAL_CLEANUP' -Event 'PASS' -ExitCode $null
            }
        }
        catch { $controlIoFailed = $true }
    }
    try { if ($null -ne $ledgerStream) { $ledgerStream.Dispose() } }
    catch { $controlIoFailed = $true }
}

if ($cleanupFailed) { exit 46 }
if ($controlIoFailed) { exit 40 }
if ($wrapperStageExitCode -ne 0) { exit $wrapperStageExitCode }
if ($allowedCollectorExitCodes -contains $collectorExitCode) { exit $collectorExitCode }
exit 45
