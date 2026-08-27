param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedLauncherSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

try {
    $launcherBytes = [System.IO.File]::ReadAllBytes($PSCommandPath)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $launcherSha256 = ([System.BitConverter]::ToString(
            $sha256.ComputeHash($launcherBytes)
        )).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
        $launcherBytes = $null
    }
    if ($launcherSha256 -cne $ExpectedLauncherSha256) {
        exit 48
    }
}
catch {
    exit 48
}

try {
    $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $wrapperPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'src\quant_research\binance_spot_forward_schedule_pit_v6_wrapper.ps1'))
    $nativePowerShell = [System.IO.Path]::GetFullPath((Join-Path $PSHOME 'powershell.exe'))

    foreach ($fixedFile in @($nativePowerShell, $wrapperPath)) {
        if (-not [System.IO.File]::Exists($fixedFile)) {
            exit 49
        }
        $attributes = [System.IO.File]::GetAttributes($fixedFile)
        if ((($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) -or
            (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
            exit 49
        }
    }
}
catch {
    exit 49
}

try {
    $LASTEXITCODE = $null
    & $nativePowerShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $wrapperPath `
        -ExpectedWrapperSha256 6e7ed42a32c7b7a2765f1c711d19ad4fb6190d17bb6130496bf6651cba0f8c55 `
        -ExpectedCollectorSha256 ff76fd9e7cd3164e98c883ded78dd0e3bf4c89165e7b168b1471fec96fb1eb8d `
        -ExpectedLoaderSha256 42a8cb233e535af3e5bf665b5da16d77176469bd3fb58816eb21aa1a51a49a1e `
        -ExpectedSourceContractSha256 b7776b2ce3eed23468a8eb3d146e481c1e570b0220e247710696f999bbc0b2d2 `
        -ExpectedSchemaSha256 129ff866fd2e4c06e43fee2aac0c869b3d908d9da4fc7a8eea70beec4c83d43e `
        -ExpectedParametersSha256 919a4db96af6ed4cf8cdc22c6ee023ea5c20f839ffe7412fa7676c77727368c1 `
        -ExpectedTestsSha256 7ec9259e35bad895b1956efa1ebab946c69c5ad3b7e42468b3a387a54c480db9
    $childExitCode = $LASTEXITCODE
}
catch {
    exit 49
}

if ($null -eq $childExitCode) {
    exit 50
}

$allowedChildExitCodes = @(0, 10, 11, 20, 24, 30, 31, 32, 33, 34, 35, 40, 41, 42, 43, 44, 45, 46, 47)
if ($allowedChildExitCodes -notcontains [int]$childExitCode) {
    exit 50
}
exit [int]$childExitCode
