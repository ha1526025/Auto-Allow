# Create the distribution ZIP and verify it runs from a separate folder.
# ASCII only on purpose: PowerShell 5.1 reads .ps1 as the system code page.
$ErrorActionPreference = 'Continue'
$log = 'package_result.txt'

function W([string]$m) { $m | Out-File -FilePath $log -Append -Encoding utf8 }

"=== package $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $log -Encoding utf8

# 0) refresh release from dist
if (-not (Test-Path 'dist\KiroAutoAllow.exe')) { W 'FAIL: dist exe missing'; exit 1 }
Get-Process -Name KiroAutoAllow -ErrorAction SilentlyContinue |
    ForEach-Object { try { $_.Kill() } catch {} }
Start-Sleep -Seconds 3
Copy-Item 'dist\KiroAutoAllow.exe' 'release\KiroAutoAllow.exe' -Force
W 'release exe refreshed from dist'

if (-not (Test-Path 'release\README.txt')) { W 'FAIL: README.txt missing'; exit 1 }

# 1) list release
W '--- release contents ---'
Get-ChildItem release | ForEach-Object { W ("  {0}  {1} bytes" -f $_.Name, $_.Length) }
$relExe = Get-Item 'release\KiroAutoAllow.exe'
$hRel = (Get-FileHash $relExe.FullName).Hash
W "release exe sha256 : $hRel"
W "same as dist       : $($hRel -eq (Get-FileHash 'dist\KiroAutoAllow.exe').Hash)"

# 2) look for dev-machine traces
$bytes = [System.IO.File]::ReadAllBytes($relExe.FullName)
$ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
W "contains '.venv'         : $($ascii.Contains('.venv'))"
W "contains 'site-packages' : $($ascii.Contains('site-packages'))"
W "contains 'C:\Users'      : $($ascii.Contains('C:\Users'))"

# 3) build zip
Remove-Item 'Kiro-Auto-Allow.zip' -Force -ErrorAction SilentlyContinue
Compress-Archive -Path 'release\*' -DestinationPath 'Kiro-Auto-Allow.zip' -Force
if (-not (Test-Path 'Kiro-Auto-Allow.zip')) { W 'FAIL: zip not created'; exit 1 }
W "zip bytes : $((Get-Item 'Kiro-Auto-Allow.zip').Length)"

# 4) extract to an independent folder
$test = Join-Path $env:TEMP ('KAA_disttest_' + (Get-Date -Format 'HHmmss'))
Remove-Item $test -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath (Resolve-Path 'Kiro-Auto-Allow.zip') -DestinationPath $test -Force
W "test folder : $test"
W '--- extracted ---'
Get-ChildItem $test | ForEach-Object { W ("  {0}  {1} bytes" -f $_.Name, $_.Length) }
$testExe = Join-Path $test 'KiroAutoAllow.exe'
W "extracted exe identical : $((Get-FileHash $testExe).Hash -eq $hRel)"

# 5) note log size before launch
$logDir = Join-Path $env:LOCALAPPDATA 'KiroAutoAllow\logs'
$before = 0
if (Test-Path $logDir) {
    $lf0 = Get-ChildItem $logDir -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($lf0) { $before = (Get-Content -LiteralPath $lf0.FullName).Count }
}
W "log lines before : $before"

# 6) launch from the test folder
Start-Process -FilePath $testExe -WorkingDirectory $test | Out-Null
Start-Sleep -Seconds 16
$running = @(Get-Process -Name KiroAutoAllow -ErrorAction SilentlyContinue)
W "process count : $($running.Count)"
$paths = @()
foreach ($p in $running) { try { $paths += $p.Path } catch { $paths += '(unavailable)' } }
W "process paths : $($paths -join ' , ')"
W "launched from test folder : $([bool]($paths -contains $testExe))"

# 7) confirm the app actually initialised (log growth proves UIA worked)
$lf = $null
if (Test-Path $logDir) {
    $lf = Get-ChildItem $logDir -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
}
if ($lf) {
    $all = Get-Content -LiteralPath $lf.FullName
    W "log lines after  : $($all.Count)  (added $($all.Count - $before))"
    W '--- log tail ---'
    $all | Select-Object -Last 12 | ForEach-Object { W $_ }
} else {
    W 'FAIL: no app log found'
}

# 8) cleanup
foreach ($p in $running) { try { $p.Kill() } catch {} }
Start-Sleep -Seconds 2
Remove-Item $test -Recurse -Force -ErrorAction SilentlyContinue
W "test folder removed : $(-not (Test-Path $test))"
W '=== done ==='
