# ZIP 作成と「別フォルダへ展開して起動」テストだけを行う。
# 既存の dist / release は壊さないので、失敗しても安全に再実行できる。
$ErrorActionPreference = 'Continue'
$log = '_pack_log.txt'

function W([string]$m) { $m | Out-File -FilePath $log -Append -Encoding utf8 }

"=== 配布パッケージ作成 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $log -Encoding utf8

# ---------------------------------------------- 1) release の中身を確認
if (-not (Test-Path 'release\KiroAutoAllow.exe')) { W '!! release に exe がありません'; exit 1 }
W '--- release の中身 ---'
Get-ChildItem release | ForEach-Object { W ("  {0}  {1} bytes" -f $_.Name, $_.Length) }

$devExe = (Get-Item 'dist\KiroAutoAllow.exe' -ErrorAction SilentlyContinue)
$relExe = Get-Item 'release\KiroAutoAllow.exe'
if ($devExe) {
    $h1 = (Get-FileHash $devExe.FullName).Hash
    $h2 = (Get-FileHash $relExe.FullName).Hash
    W "dist と release の exe が同一 : $($h1 -eq $h2)"
    W "exe SHA256 : $h2"
}

# ---------------------------------------------- 2) exe に開発環境の痕跡がないか
$bytes = [System.IO.File]::ReadAllBytes($relExe.FullName)
$ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
W "exe 内に '.venv'         : $($ascii.Contains('.venv'))"
W "exe 内に 'site-packages' : $($ascii.Contains('site-packages'))"
W "exe 内に 'C:\Users'      : $($ascii.Contains('C:\Users'))"

# ---------------------------------------------- 3) ZIP 作成
Remove-Item 'Kiro-Auto-Allow.zip' -Force -ErrorAction SilentlyContinue
Compress-Archive -Path 'release\*' -DestinationPath 'Kiro-Auto-Allow.zip' -Force
if (-not (Test-Path 'Kiro-Auto-Allow.zip')) { W '!! ZIP 作成に失敗'; exit 1 }
W "ZIP サイズ : $((Get-Item 'Kiro-Auto-Allow.zip').Length) bytes"

# ---------------------------------------------- 4) 別フォルダへ展開
$test = Join-Path $env:TEMP ('KAA_disttest_' + (Get-Date -Format 'HHmmss'))
Remove-Item $test -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath (Resolve-Path 'Kiro-Auto-Allow.zip') -DestinationPath $test -Force
W "テスト展開先 : $test"
W '--- 展開されたファイル ---'
Get-ChildItem $test | ForEach-Object { W ("  {0}  {1} bytes" -f $_.Name, $_.Length) }

$testExe = Join-Path $test 'KiroAutoAllow.exe'
W "展開後の exe が同一 : $((Get-FileHash $testExe).Hash -eq (Get-FileHash $relExe.FullName).Hash)"

# ---------------------------------------------- 5) 起動前のログ行数を記録
$logDir = Join-Path $env:LOCALAPPDATA 'KiroAutoAllow\logs'
$before = 0
$lf0 = $null
if (Test-Path $logDir) {
    $lf0 = Get-ChildItem $logDir -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($lf0) { $before = (Get-Content -LiteralPath $lf0.FullName).Count }
}
W "起動前のログ行数 : $before"

# ---------------------------------------------- 6) 既存プロセスを止めてからテスト起動
Get-Process -Name KiroAutoAllow -ErrorAction SilentlyContinue | ForEach-Object {
    try { $_.Kill() } catch {}
}
Start-Sleep -Seconds 3

Start-Process -FilePath $testExe -WorkingDirectory $test | Out-Null
Start-Sleep -Seconds 15

$running = @(Get-Process -Name KiroAutoAllow -ErrorAction SilentlyContinue)
W "起動したプロセス数 : $($running.Count)"
$paths = @()
foreach ($p in $running) { try { $paths += $p.Path } catch { $paths += '(取得不可)' } }
W "実行パス : $($paths -join ' , ')"
W "テストフォルダから起動できた : $([bool]($paths -contains $testExe))"

# ---------------------------------------------- 7) ログで実際の動作を確認
$lf = $null
if (Test-Path $logDir) {
    $lf = Get-ChildItem $logDir -Filter *.log | Sort-Object LastWriteTime | Select-Object -Last 1
}
if ($lf) {
    $all = Get-Content -LiteralPath $lf.FullName
    W "起動後のログ行数 : $($all.Count)  (増加 $($all.Count - $before) 行)"
    W '--- ログ末尾 ---'
    $all | Select-Object -Last 12 | ForEach-Object { W $_ }
} else {
    W '!! ログが見つかりません'
}

# ---------------------------------------------- 8) 後片付け
foreach ($p in $running) { try { $p.Kill() } catch {} }
Start-Sleep -Seconds 2
Remove-Item $test -Recurse -Force -ErrorAction SilentlyContinue
W "テストフォルダ削除 : $(-not (Test-Path $test))"
W '=== 完了 ==='
