param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedLauncherSha256,
    [Parameter(Mandatory = $true, Position = 1)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedFreezeManifestSha256
)

$ErrorActionPreference = 'Stop'
$runId = 'exp_20260828_007_bootstrap_formal_001'
$experimentId = 'exp_20260828_007'
$apiKey = $null
$envOwned = $false
$ledger = $null

function Get-StrictFile([string]$LiteralPath) {
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if ($item.PSIsContainer -or $null -ne $item.LinkType -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw 'UNTRUSTED_FILE'
    }
    return $item
}

function Get-Sha256([string]$LiteralPath) {
    $stream = [IO.File]::Open($LiteralPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
    }
    finally { $stream.Dispose() }
}

function Add-Ledger([string]$Event) {
    $line = '{"event":"' + $Event + '","run_id":"' + $runId + '"}' + "`n"
    $body = (New-Object Text.UTF8Encoding($false)).GetBytes($line)
    $script:ledger.Write($body, 0, $body.Length)
    $script:ledger.Flush($true)
}

try {
    Get-StrictFile $PSCommandPath | Out-Null
    if ((Get-Sha256 $PSCommandPath) -cne $ExpectedLauncherSha256) { throw 'LAUNCHER_FREEZE_MISMATCH' }

    $repoRoot = [IO.DirectoryInfo]$PSScriptRoot
    1..5 | ForEach-Object { $repoRoot = $repoRoot.Parent }
    $repoRoot = $repoRoot.FullName
    $freezePath = Join-Path $repoRoot 'experiments\exp_20260828_007\artifacts\candidate_freeze_manifest.json'
    Get-StrictFile $freezePath | Out-Null
    if ((Get-Sha256 $freezePath) -cne $ExpectedFreezeManifestSha256) { throw 'FREEZE_MANIFEST_MISMATCH' }
    $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
    Get-StrictFile $pythonPath | Out-Null
    $freezePreflight = @'
import hashlib,json,os,stat,sys
def fail(code): raise SystemExit(code)
def pairs(items):
    out={}
    for key,value in items:
        if key in out: fail(42)
        out[key]=value
    return out
try:
    root=os.path.abspath(sys.argv[1]); manifest_path=os.path.abspath(sys.argv[2]); expected=sys.argv[3]
    if os.path.commonpath((root,manifest_path))!=root: fail(42)
    raw=open(manifest_path,'rb').read()
    if hashlib.sha256(raw).hexdigest()!=expected or raw.startswith(b'\xef\xbb\xbf'): fail(42)
    value=json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=lambda _:fail(42))
    canonical=(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2)+'\n').encode()
    if raw!=canonical or type(value) is not dict or set(value)!={'files','schema_version'} or value['schema_version']!='JQUANTS_V2_BARS_MONTHLY_V1_CANDIDATE_FREEZE_V1' or type(value['files']) is not list: fail(42)
    seen=set()
    for row in value['files']:
        if type(row) is not dict or set(row)!={'bytes','path','sha256'} or type(row['bytes']) is not int or type(row['bytes']) is bool or row['bytes']<0 or type(row['path']) is not str or type(row['sha256']) is not str or len(row['sha256'])!=64: fail(42)
        rel=row['path']; parts=rel.split('/')
        if '\\' in rel or rel.startswith('/') or '..' in parts or rel in seen: fail(42)
        seen.add(rel); target=os.path.abspath(os.path.join(root,*parts))
        if os.path.commonpath((root,target))!=root: fail(42)
        info=os.lstat(target)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info,'st_file_attributes',0)&0x400): fail(42)
        body=open(target,'rb').read()
        if len(body)!=row['bytes'] or hashlib.sha256(body).hexdigest()!=row['sha256']: fail(42)
    if not seen: fail(42)
except SystemExit: raise
except BaseException: fail(42)
'@
    & $pythonPath -I -S -B -c $freezePreflight $repoRoot $freezePath $ExpectedFreezeManifestSha256
    if ($LASTEXITCODE -ne 0) { throw 'FREEZE_CONTENT_MISMATCH' }

    $controlRoot = Join-Path $repoRoot 'experiments\exp_20260828_007\formal_control'
    [IO.Directory]::CreateDirectory($controlRoot) | Out-Null
    $reservationPath = Join-Path $controlRoot "$runId.reservation.lock"
    $reservation = [IO.File]::Open($reservationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $reservationBody = (New-Object Text.UTF8Encoding($false)).GetBytes('{"experiment_id":"' + $experimentId + '","run_id":"' + $runId + '"}' + "`n")
        $reservation.Write($reservationBody, 0, $reservationBody.Length)
        $reservation.Flush($true)
    }
    finally { $reservation.Dispose() }
    $ledgerPath = Join-Path $controlRoot "$runId.stage_ledger.jsonl"
    $ledger = [IO.File]::Open($ledgerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    Add-Ledger 'FROZEN_AUTHORITY_AND_O_EXCL_RESERVATION_PASS'

    if (Test-Path Env:JQUANTS_API_KEY) { throw 'PREEXISTING_KEY_ENV_REJECTED' }
    $envPath = Join-Path $repoRoot '.env.jquants.local'
    Get-StrictFile $envPath | Out-Null
    $envBytes = [IO.File]::ReadAllBytes($envPath)
    if ($envBytes.Length -gt 4096) { throw 'ENV_CAP' }
    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
    $envText = $strictUtf8.GetString($envBytes)
    $match = [regex]::Match($envText, '\AJQUANTS_API_KEY=([A-Za-z0-9_-]{1,512})(?:\r?\n)?\z')
    if (-not $match.Success) { throw 'ENV_GRAMMAR' }
    $apiKey = $match.Groups[1].Value
    $env:JQUANTS_API_KEY = $apiKey
    $envOwned = $true
    $envBytes = $null; $envText = $null; $match = $null; $apiKey = $null
    Add-Ledger 'ENV_ONLY_KEY_READY'

    Set-Location -LiteralPath $repoRoot
    & $pythonPath -I -B -m quant_research.alpha_models.data.jquants_v2_bars_monthly_v1 --repo-root $repoRoot --formal-bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'FORMAL_BOOTSTRAP_FAILED' }
    Add-Ledger 'FORMAL_BOOTSTRAP_EXIT_0'
    exit 0
}
catch {
    if ($null -ne $ledger) { try { Add-Ledger 'STOPPED_FIRST_FAILURE' } catch {} }
    exit 40
}
finally {
    if ($envOwned) { Remove-Item Env:JQUANTS_API_KEY -ErrorAction SilentlyContinue }
    $apiKey = $null
    if ($null -ne $ledger) { $ledger.Dispose() }
}
