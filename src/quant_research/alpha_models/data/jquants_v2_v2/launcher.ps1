param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedLauncherSha256,
    [Parameter(Mandatory = $true, Position = 1)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedFreezeManifestSha256
)

function Get-StrictSha256Lower([string]$LiteralPath) {
    $hashStream = [IO.File]::Open($LiteralPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha256.ComputeHash($hashStream))).Replace('-', '').ToLowerInvariant() }
        finally { $sha256.Dispose() }
    }
    finally { $hashStream.Dispose() }
}

$ErrorActionPreference = 'Stop'
$exitCode = 0
$stage = 'SELF_HASH'
$envOwned = $false
$stream = $null
$buffer = $null
$bytes = $null
$text = $null
$key = $null
$match = $null

try {
    $launcherItem = Get-Item -LiteralPath $PSCommandPath -Force
    if (($launcherItem.PSIsContainer) -or ($null -ne $launcherItem.LinkType) -or (($launcherItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'SELF_HASH' }
    $actualLauncherSha256 = Get-StrictSha256Lower $PSCommandPath
    if ($actualLauncherSha256 -cne $ExpectedLauncherSha256) { throw 'SELF_HASH' }

    $packageDirectory = [IO.DirectoryInfo]$PSScriptRoot
    $repoRoot = $packageDirectory.Parent.Parent.Parent.Parent.Parent.FullName
    $freezePath = Join-Path $repoRoot 'experiments\exp_20260828_003\artifacts\expected_freeze_manifest.json'
    $freezeItem = Get-Item -LiteralPath $freezePath -Force
    if (($freezeItem.PSIsContainer) -or ($null -ne $freezeItem.LinkType) -or (($freezeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'FREEZE_PATH' }
    $actualFreezeSha256 = Get-StrictSha256Lower $freezePath
    if ($actualFreezeSha256 -cne $ExpectedFreezeManifestSha256) { throw 'FREEZE_HASH' }

    $stage = 'HANDOFF'
    if (Test-Path Env:JQUANTS_API_KEY) { throw 'PREEXISTING_ENV' }

    $stage = 'ENV_READ'
    $envPath = Join-Path $repoRoot '.env.jquants.local'
    $envItem = Get-Item -LiteralPath $envPath -Force
    if (($envItem.PSIsContainer) -or ($null -ne $envItem.LinkType) -or (($envItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'ENV_PATH' }
    $stream = [IO.File]::Open($envPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $buffer = New-Object byte[] 4097
    $count = $stream.Read($buffer, 0, 4097)
    if (($count -gt 4096) -or ($stream.ReadByte() -ne -1)) { throw 'ENV_CAP' }
    $stream.Dispose()
    $stream = $null
    $bytes = New-Object byte[] $count
    [Array]::Copy($buffer, 0, $bytes, 0, $count)

    $stage = 'VALIDATE'
    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
    $text = $strictUtf8.GetString($bytes)
    if ($text.IndexOf([char]0) -ge 0) { throw 'NUL' }
    $match = [regex]::Match($text, '\AJQUANTS_API_KEY=([A-Za-z0-9_-]{1,512})(?:\r?\n)?\z', [Text.RegularExpressions.RegexOptions]::CultureInvariant)
    if (-not $match.Success) { throw 'GRAMMAR' }
    $key = $match.Groups[1].Value

    $stage = 'HANDOFF'
    $env:JQUANTS_API_KEY = $key
    $envOwned = $true
    $buffer = $null
    $bytes = $null
    $text = $null
    $key = $null
    $match = $null

    $stage = 'COLLECTOR'
    $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $pythonItem = Get-Item -LiteralPath $pythonPath -Force
    if (($pythonItem.PSIsContainer) -or ($null -ne $pythonItem.LinkType) -or (($pythonItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'PYTHON_PATH' }
    & $pythonPath -B -m quant_research.alpha_models.data.jquants_v2_v2.collector --execute --expected-freeze-manifest-sha256 $ExpectedFreezeManifestSha256
    $childExit = $LASTEXITCODE
    if ($childExit -in @(0, 10, 11, 20)) { $exitCode = $childExit }
    else { throw 'CHILD_EXIT' }
}
catch {
    if ($stage -eq 'SELF_HASH') { $exitCode = 41 }
    elseif ($stage -eq 'ENV_READ') { $exitCode = 42 }
    elseif ($stage -eq 'VALIDATE') { $exitCode = 43 }
    elseif ($stage -eq 'HANDOFF') { $exitCode = 44 }
    else { $exitCode = 45 }
}
finally {
    $cleanupFailed = $false
    try { if ($null -ne $stream) { $stream.Dispose() } } catch { $cleanupFailed = $true }
    try { if ($envOwned) { Remove-Item Env:JQUANTS_API_KEY -ErrorAction Stop } } catch { $cleanupFailed = $true }
    try { Clear-Variable -Name buffer,bytes,text,key,match -Force -ErrorAction Stop } catch { $cleanupFailed = $true }
    if ($cleanupFailed) { $exitCode = 46 }
}

exit $exitCode
