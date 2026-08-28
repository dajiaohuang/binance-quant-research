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

function Write-Stage([string]$Stage, [string]$Event, [object]$ExitCode) {
    if ($script:ledgerFailed) { throw 'LEDGER_ALREADY_FAILED' }
    $allowedStages = @('SELF_HASH','FREEZE_PREFLIGHT','ENV_FILE_READ','VALIDATE','COLLECTOR','FINAL_CLEANUP')
    $allowedEvents = @('START','PASS','FAIL','EXIT')
    if (($Stage -notin $allowedStages) -or ($Event -notin $allowedEvents)) { throw 'LEDGER_ENUM' }
    $script:seq += 1
    if (($null -ne $ExitCode) -and ($ExitCode -isnot [int])) { throw 'LEDGER_EXIT_TYPE' }
    $codeText = if ($null -eq $ExitCode) { 'null' } else { ([int]$ExitCode).ToString([Globalization.CultureInfo]::InvariantCulture) }
    $line = '{"event":"' + $Event + '","exit_code":' + $codeText + ',"seq":' + $script:seq.ToString([Globalization.CultureInfo]::InvariantCulture) + ',"stage":"' + $Stage + '"}' + "`n"
    try {
        $payload = (New-Object Text.UTF8Encoding($false)).GetBytes($line)
        $script:ledgerStream.Write($payload, 0, $payload.Length)
        $script:ledgerStream.Flush($true)
    }
    catch {
        $script:ledgerFailed = $true
        throw
    }
}

$ErrorActionPreference = 'Stop'
$exitCode = 0
$activeStage = 'SELF_HASH'
$envOwned = $false
$ledgerFailed = $false
$seq = 0
$ledgerStream = $null
$envStream = $null
$buffer = $null
$bytes = $null
$text = $null
$key = $null
$match = $null

try {
    $launcherItem = Get-Item -LiteralPath $PSCommandPath -Force
    if (($launcherItem.PSIsContainer) -or ($null -ne $launcherItem.LinkType) -or (($launcherItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'SELF_HASH' }
    if ((Get-StrictSha256Lower $PSCommandPath) -cne $ExpectedLauncherSha256) { throw 'SELF_HASH' }
}
catch {
    exit 41
}

$packageDirectory = [IO.DirectoryInfo]$PSScriptRoot
$repoRoot = $packageDirectory.Parent.Parent.Parent.Parent.Parent.FullName
$controlParent = Join-Path $repoRoot 'experiments\exp_20260828_004\formal_control'
$reservationPath = Join-Path $controlParent 'exp_20260828_004_formal_001.reservation.lock'
$ledgerPath = Join-Path $controlParent 'exp_20260828_004_formal_001.stage_ledger.jsonl'

try {
    $controlItem = Get-Item -LiteralPath $controlParent -Force
    if ((-not $controlItem.PSIsContainer) -or ($null -ne $controlItem.LinkType) -or (($controlItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'CONTROL_PARENT' }
    $reservation = [IO.File]::Open($reservationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $reservationBytes = (New-Object Text.UTF8Encoding($false)).GetBytes('{"experiment_id":"exp_20260828_004","run_id":"exp_20260828_004_formal_001"}' + "`n")
        $reservation.Write($reservationBytes, 0, $reservationBytes.Length)
        $reservation.Flush($true)
    }
    finally { $reservation.Dispose() }
}
catch [IO.IOException] {
    $low = $_.Exception.HResult -band 0xffff
    if ($low -in @(80,183)) { exit 47 }
    exit 40
}
catch { exit 40 }

try {
    $ledgerStream = [IO.File]::Open($ledgerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    Write-Stage 'SELF_HASH' 'PASS' $null
}
catch {
    if ($null -ne $ledgerStream) { try { $ledgerStream.Dispose() } catch {} }
    exit 40
}

$preflightCode = @'
import hashlib,json,os,stat,sys
def fail(): raise SystemExit(42)
def pairs(items):
    out={}
    for key,value in items:
        if key in out: fail()
        out[key]=value
    return out
def nonfinite(_): fail()
try:
    root=os.path.abspath(sys.argv[1]); manifest_path=os.path.abspath(sys.argv[2]); expected=sys.argv[3]
    if os.path.commonpath((root,manifest_path))!=root: fail()
    info=os.lstat(manifest_path)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info,'st_file_attributes',0)&0x400): fail()
    raw=open(manifest_path,'rb').read()
    if hashlib.sha256(raw).hexdigest()!=expected or raw.startswith(b'\xef\xbb\xbf'): fail()
    value=json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=nonfinite)
    def scalars(item):
        if type(item) is str and any(0xD800<=ord(c)<=0xDFFF for c in item): fail()
        if type(item) is list:
            for child in item: scalars(child)
        if type(item) is dict:
            for key,child in item.items(): scalars(key); scalars(child)
    scalars(value)
    canonical=(json.dumps(value,ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
    if raw!=canonical or type(value) is not dict or set(value)!={'files','schema_version'} or value['schema_version']!='JQUANTS_V2_V3_EXTERNAL_FREEZE_V1' or type(value['files']) is not list: fail()
    required={
      'experiments/exp_20260828_004/artifacts/schema.json','experiments/exp_20260828_004/artifacts/source_contract.json','experiments/exp_20260828_004/parameters.json',
      'src/quant_research/alpha_models/data/jquants_v2_v3/__init__.py','src/quant_research/alpha_models/data/jquants_v2_v3/adapters.py','src/quant_research/alpha_models/data/jquants_v2_v3/collector.py','src/quant_research/alpha_models/data/jquants_v2_v3/contracts.py','src/quant_research/alpha_models/data/jquants_v2_v3/launcher.ps1','src/quant_research/alpha_models/data/jquants_v2_v3/loader.py','tests/test_jquants_v2_v3.py'}
    seen=set()
    for row in value['files']:
        if type(row) is not dict or set(row)!={'bytes','path','sha256'} or type(row['bytes']) is not int or type(row['bytes']) is bool or row['bytes']<0 or type(row['path']) is not str or type(row['sha256']) is not str or len(row['sha256'])!=64: fail()
        rel=row['path']; normalized=rel.replace('\\','/')
        if rel!=normalized or rel.startswith('/') or '..' in normalized.split('/') or rel in seen: fail()
        seen.add(rel); target=os.path.abspath(os.path.join(root,*rel.split('/')))
        if os.path.commonpath((root,target))!=root: fail()
        item=os.lstat(target)
        if not stat.S_ISREG(item.st_mode) or stat.S_ISLNK(item.st_mode) or (getattr(item,'st_file_attributes',0)&0x400): fail()
        body=open(target,'rb').read()
        if len(body)!=row['bytes'] or hashlib.sha256(body).hexdigest()!=row['sha256']: fail()
    if seen!=required or len(seen)!=10: fail()
except SystemExit: raise
except BaseException: fail()
raise SystemExit(0)
'@

$collectorCode = @'
import importlib.util,os,sys,types
root=os.path.abspath(sys.argv[1]); base=os.path.join(root,'src','quant_research','alpha_models','data','jquants_v2_v3')
packages=(('quant_research',os.path.join(root,'src','quant_research')),('quant_research.alpha_models',os.path.join(root,'src','quant_research','alpha_models')),('quant_research.alpha_models.data',os.path.join(root,'src','quant_research','alpha_models','data')),('quant_research.alpha_models.data.jquants_v2_v3',base))
for name,path in packages:
    module=types.ModuleType(name); module.__path__=[path]; module.__package__=name; sys.modules[name]=module
for leaf in ('contracts','loader','collector'):
    name='quant_research.alpha_models.data.jquants_v2_v3.'+leaf
    spec=importlib.util.spec_from_file_location(name,os.path.join(base,leaf+'.py'))
    if spec is None or spec.loader is None: raise SystemExit(45)
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module)
raise SystemExit(sys.modules['quant_research.alpha_models.data.jquants_v2_v3.collector'].main(['--execute','--expected-freeze-manifest-sha256',sys.argv[2]]))
'@

try {
    $activeStage = 'FREEZE_PREFLIGHT'
    Write-Stage 'FREEZE_PREFLIGHT' 'START' $null
    $pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $pythonItem = Get-Item -LiteralPath $pythonPath -Force
    if (($pythonItem.PSIsContainer) -or ($null -ne $pythonItem.LinkType) -or (($pythonItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'PYTHON_PATH' }
    $freezePath = Join-Path $repoRoot 'experiments\exp_20260828_004\artifacts\expected_freeze_manifest.json'
    & $pythonPath -I -S -B -c $preflightCode $repoRoot $freezePath $ExpectedFreezeManifestSha256
    if ($LASTEXITCODE -ne 0) { $exitCode = 42; Write-Stage 'FREEZE_PREFLIGHT' 'FAIL' 42 }
    else { Write-Stage 'FREEZE_PREFLIGHT' 'PASS' $null }

    if ($exitCode -eq 0) {
        if (Test-Path Env:JQUANTS_API_KEY) {
            $activeStage = 'VALIDATE'
            $exitCode = 44
            Write-Stage 'VALIDATE' 'FAIL' 44
        }
    }

    if ($exitCode -eq 0) {
        $activeStage = 'ENV_FILE_READ'
        Write-Stage 'ENV_FILE_READ' 'START' $null
        $envPath = Join-Path $repoRoot '.env.jquants.local'
        $envItem = Get-Item -LiteralPath $envPath -Force
        if (($envItem.PSIsContainer) -or ($null -ne $envItem.LinkType) -or (($envItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) { throw 'ENV_PATH' }
        $envStream = [IO.File]::Open($envPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $buffer = New-Object byte[] 4097
        $count = $envStream.Read($buffer, 0, 4097)
        if (($count -gt 4096) -or ($envStream.ReadByte() -ne -1)) { throw 'ENV_CAP' }
        $envStream.Dispose(); $envStream = $null
        $bytes = New-Object byte[] $count; [Array]::Copy($buffer, 0, $bytes, 0, $count)
        Write-Stage 'ENV_FILE_READ' 'PASS' $null

        $activeStage = 'VALIDATE'
        $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
        $text = $strictUtf8.GetString($bytes)
        if ($text.IndexOf([char]0) -ge 0) { throw 'ENV_VALIDATE' }
        $match = [regex]::Match($text, '\AJQUANTS_API_KEY=([A-Za-z0-9_-]{1,512})(?:\r?\n)?\z', [Text.RegularExpressions.RegexOptions]::CultureInvariant)
        if (-not $match.Success) { throw 'GRAMMAR' }
        $key = $match.Groups[1].Value
        $env:JQUANTS_API_KEY = $key; $envOwned = $true
        $buffer=$null; $bytes=$null; $text=$null; $key=$null; $match=$null
        Write-Stage 'VALIDATE' 'PASS' $null

        $activeStage = 'COLLECTOR'
        Write-Stage 'COLLECTOR' 'START' $null
        & $pythonPath -I -S -B -c $collectorCode $repoRoot $ExpectedFreezeManifestSha256
        $childExit = $LASTEXITCODE
        if ($childExit -in @(0,10,11,20)) { $exitCode=$childExit; Write-Stage 'COLLECTOR' 'EXIT' $childExit }
        else { $exitCode=45; Write-Stage 'COLLECTOR' 'FAIL' 45 }
    }
}
catch {
    if ($ledgerFailed) { $exitCode = 40 }
    elseif ($activeStage -eq 'FREEZE_PREFLIGHT') { $exitCode=42; try { Write-Stage 'FREEZE_PREFLIGHT' 'FAIL' 42 } catch { $exitCode=40 } }
    elseif ($activeStage -eq 'ENV_FILE_READ') { $exitCode=43; try { Write-Stage 'ENV_FILE_READ' 'FAIL' 43 } catch { $exitCode=40 } }
    elseif ($activeStage -eq 'VALIDATE') { $exitCode=44; try { Write-Stage 'VALIDATE' 'FAIL' 44 } catch { $exitCode=40 } }
    else { $exitCode=45; try { Write-Stage 'COLLECTOR' 'FAIL' 45 } catch { $exitCode=40 } }
}
finally {
    $cleanupFailed=$false
    try { if ($null -ne $envStream) { $envStream.Dispose() } } catch { $cleanupFailed=$true }
    try { if ($envOwned) { Remove-Item Env:JQUANTS_API_KEY -ErrorAction Stop } } catch { $cleanupFailed=$true }
    try { Clear-Variable -Name buffer,bytes,text,key,match -Force -ErrorAction Stop } catch { $cleanupFailed=$true }
    if ($cleanupFailed) { $exitCode=46 }
    if (-not $ledgerFailed) {
        try {
            if ($cleanupFailed) { Write-Stage 'FINAL_CLEANUP' 'FAIL' 46 }
            else { Write-Stage 'FINAL_CLEANUP' 'PASS' $null }
        }
        catch { if (-not $cleanupFailed) { $exitCode=40 } }
    }
    if ($null -ne $ledgerStream) { try { $ledgerStream.Dispose() } catch { if ($exitCode -ne 46) { $exitCode=40 } } }
}

exit $exitCode
