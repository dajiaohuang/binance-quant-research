param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedLauncherSha256,
    [Parameter(Mandatory = $true, Position = 1)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedFreezeManifestSha256
)

$ErrorActionPreference = 'Stop'
$batchId = 'exp_20260828_010_monthly_formal_002'
$experimentId = 'exp_20260828_011'
$apiKey = $null
$envOwned = $false
$ledger = $null
$preflightPassed = $false

function Get-StrictFile([string]$LiteralPath) {
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if ($item.PSIsContainer -or $null -ne $item.LinkType -or (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'UNTRUSTED_FILE' }
    return $item
}

function Get-Sha256([string]$LiteralPath) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $LiteralPath).Hash.ToLowerInvariant()
}

try {
    Get-StrictFile $PSCommandPath | Out-Null
    if ((Get-Sha256 $PSCommandPath) -cne $ExpectedLauncherSha256) { throw 'LAUNCHER_FREEZE_MISMATCH' }
    $repoRoot = [IO.DirectoryInfo]$PSScriptRoot
    1..5 | ForEach-Object { $repoRoot = $repoRoot.Parent }
    $repoRoot = $repoRoot.FullName
    $freezePath = Join-Path $repoRoot 'experiments\exp_20260828_011\artifacts\candidate_freeze_manifest.json'
    Get-StrictFile $freezePath | Out-Null
    if ((Get-Sha256 $freezePath) -cne $ExpectedFreezeManifestSha256) { throw 'FREEZE_MANIFEST_MISMATCH' }
    $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
    Get-StrictFile $pythonPath | Out-Null
    $freezePreflight = @'
import hashlib,json,os,stat,sys
def fail(): raise SystemExit(42)
def pairs(items):
    out={}
    for key,value in items:
        if key in out: fail()
        out[key]=value
    return out
try:
    root=os.path.abspath(sys.argv[1]); path=os.path.abspath(sys.argv[2]); expected=sys.argv[3]
    raw=open(path,'rb').read()
    if os.path.commonpath((root,path))!=root or hashlib.sha256(raw).hexdigest()!=expected or raw.startswith(b'\xef\xbb\xbf'): fail()
    value=json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=lambda _:fail())
    if raw!=(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2)+'\n').encode() or type(value) is not dict or set(value)!={'files','schema_version'} or value['schema_version']!='JQUANTS_V2_BARS_MONTHLY_V5_CANDIDATE_FREEZE_V1': fail()
    seen=set()
    for row in value['files']:
        if type(row) is not dict or set(row)!={'bytes','path','sha256'} or type(row['bytes']) is not int or type(row['path']) is not str or type(row['sha256']) is not str: fail()
        rel=row['path']; target=os.path.abspath(os.path.join(root,*rel.split('/')))
        if '\\' in rel or rel.startswith('/') or '..' in rel.split('/') or rel in seen or os.path.commonpath((root,target))!=root: fail()
        seen.add(rel); info=os.lstat(target)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info,'st_file_attributes',0)&0x400): fail()
        body=open(target,'rb').read()
        if len(body)!=row['bytes'] or hashlib.sha256(body).hexdigest()!=row['sha256']: fail()
    if not seen: fail()
except SystemExit: raise
except BaseException: fail()
'@
    & $pythonPath -I -S -B -c $freezePreflight $repoRoot $freezePath $ExpectedFreezeManifestSha256
    if ($LASTEXITCODE -ne 0) { throw 'FREEZE_CONTENT_MISMATCH' }

    & $pythonPath -I -B -m quant_research.alpha_models.data.jquants_v2_bars_monthly_v5 --repo-root $repoRoot --recovery-preflight-check | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_PREFLIGHT_FAILED' }
    $preflightPassed = $true

    function Add-Ledger([string]$Event) {
        if (-not $script:preflightPassed) { throw 'WRITE_BEFORE_RECOVERY_PREFLIGHT_PASS' }
        $line = '{"batch_id":"' + $batchId + '","event":"' + $Event + '"}' + "`n"
        $body = (New-Object Text.UTF8Encoding($false)).GetBytes($line)
        $script:ledger.Write($body, 0, $body.Length); $script:ledger.Flush($true)
    }

    $controlRoot = Join-Path $repoRoot 'experiments\exp_20260828_011\formal_control'
    [IO.Directory]::CreateDirectory($controlRoot) | Out-Null
    $reservationPath = Join-Path $controlRoot "$batchId.reservation.lock"
    $reservation = [IO.File]::Open($reservationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $body = (New-Object Text.UTF8Encoding($false)).GetBytes('{"batch_id":"' + $batchId + '","experiment_id":"' + $experimentId + '"}' + "`n")
        $reservation.Write($body, 0, $body.Length); $reservation.Flush($true)
    }
    finally { $reservation.Dispose() }
    $ledgerPath = Join-Path $controlRoot "$batchId.stage_ledger.jsonl"
    $ledger = [IO.File]::Open($ledgerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    Add-Ledger 'READ_ONLY_RECOVERY_PREFLIGHT_AND_O_EXCL_CONTROL_PASS'

    & $pythonPath -I -B -m quant_research.alpha_models.data.jquants_v2_bars_monthly_v5 --repo-root $repoRoot --reserve-recovery-batch | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_BATCH_RESERVATION_FAILED' }
    Add-Ledger 'POINTER_REGISTRY_EMITTED_BEFORE_ENV'

    if (Test-Path Env:JQUANTS_API_KEY) { throw 'PREEXISTING_KEY_ENV_REJECTED' }
    $envPath = Join-Path $repoRoot '.env.jquants.local'
    Get-StrictFile $envPath | Out-Null
    $envBytes = [IO.File]::ReadAllBytes($envPath)
    if ($envBytes.Length -gt 4096) { throw 'ENV_CAP' }
    $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
    $envText = $strictUtf8.GetString($envBytes)
    $match = [regex]::Match($envText, '\AJQUANTS_API_KEY=([A-Za-z0-9_-]{1,512})(?:\r?\n)?\z')
    if (-not $match.Success) { throw 'ENV_GRAMMAR' }
    $apiKey = $match.Groups[1].Value; $env:JQUANTS_API_KEY = $apiKey; $envOwned = $true
    $envBytes = $null; $envText = $null; $match = $null; $apiKey = $null
    Add-Ledger 'ENV_ONLY_KEY_READY_AFTER_POINTER_REGISTRY'

    Set-Location -LiteralPath $repoRoot
    & $pythonPath -I -B -m quant_research.alpha_models.data.jquants_v2_bars_monthly_v5 --repo-root $repoRoot --formal-recovery-pre-reserved
    if ($LASTEXITCODE -ne 0) { throw 'FORMAL_RECOVERY_FAILED' }
    Add-Ledger 'FORMAL_RECOVERY_EXIT_0'; exit 0
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
