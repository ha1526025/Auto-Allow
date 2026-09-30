"""コマンド→日本語変換の検証と、構文チェック。結果は _verify_result.txt へ。"""
import importlib.util
import sys
from pathlib import Path

here = Path(__file__).parent
out = open(here / "verify_result.txt", "w", encoding="utf-8")


def p(*a):
    print(*a, file=out, flush=True)


spec = importlib.util.spec_from_file_location("kaa", here / "KiroAutoAllow.py")
kaa = importlib.util.module_from_spec(spec)
sys.modules["kaa"] = kaa
spec.loader.exec_module(kaa)
p("import OK")

PREFIX = "Your approval is required to continue: "

# (入力コマンド, 期待に含まれるべき語, 注意フラグの期待)
CASES = [
    ("Remove-Item -Recurse -Force build", "削除", True),
    ("git push origin main", "リモートに送信", True),
    ("git commit -m \"fix\"", "変更を記録する", False),
    ("git status --short", "状態を確認", False),
    ("pip install comtypes", "ライブラリを追加", False),
    ("pip uninstall comtypes", "ライブラリを削除", True),
    ("npm install", "パッケージを追加", False),
    (".venv\\Scripts\\python.exe -m PyInstaller KiroAutoAllow.spec", "ビルド", False),
    (".venv\\Scripts\\python.exe -m py_compile KiroAutoAllow.py", "文法", False),
    ("python -m pytest -q", "テスト", False),
    ("python _detail.py", "Python", False),
    ("ping -n 1 127.0.0.1", "ネットワーク", False),
    ("curl -s https://example.com", "インターネットに通信", True),
    ("taskkill /f /im KiroAutoAllow.exe", "強制終了", True),
    ("shutdown /r /t 0", "再起動", True),
    ("Compress-Archive -Path release\\* -DestinationPath a.zip", "ZIP", False),
    ("Expand-Archive a.zip -DestinationPath t", "展開", False),
    ("Copy-Item a.txt b.txt", "コピー", False),
    ("Get-Content README.txt", "中身を読む", False),
    ("Get-ChildItem", "一覧", False),
    ("Get-FileHash a.exe", "同一か確認", False),
    ("echo hello", "表示", False),
    ("reg add HKCU\\Software\\Test /v A /d 1", "レジストリ", False),
    ("mkdir newdir", "新しく作る", False),
]

p("")
p("=== コマンド変換テスト ===")
ok = 0
ng = 0
for cmd, expect, want_caution in CASES:
    got = kaa.explain_command(PREFIX + cmd)
    has_word = expect in got
    has_caution = got.startswith("【注意】")
    judge = "OK " if has_word else "NG "
    if has_word:
        ok += 1
    else:
        ng += 1
    p(f"{judge} '{cmd[:52]}'")
    p(f"      -> {got}")
    if want_caution and not has_caution:
        p("      !! 注意マークが付くべきなのに付いていない")
    if (not want_caution) and has_caution:
        p(f"      (参考) 注意マークが付いた: {got}")

p("")
p(f"一致 {ok} 件 / 不一致 {ng} 件")

p("")
p("=== 冗長ラベル・誤検出のチェック ===")
STRICT = [
    # (コマンド, 出てはいけない文字列)
    ("Expand-Archive a.zip -DestinationPath t", "ZIP にまとめる"),
    (".venv\\Scripts\\python.exe -m PyInstaller a.spec", "Python のプログラムを実行する"),
    ("git status --short", "Git を操作する"),
    ("git push origin main", "Git を操作する"),
    ("git commit -m x", "Git を操作する"),
    ("python -m pytest", "Python のプログラムを実行する"),
]
bad = 0
for cmd, forbidden in STRICT:
    got = kaa.explain_command(PREFIX + cmd)
    hit = forbidden in got
    if hit:
        bad += 1
    p(f"{'NG ' if hit else 'OK '} '{cmd[:50]}'")
    p(f"      -> {got}")
    if hit:
        p(f"      !! '{forbidden}' が余計に付いている")
p(f"冗長・誤検出 {bad} 件")

p("")
p("=== 承認ブロックの整理テスト ===")
s = kaa.load_config()["safety"]
# 実測で取得された並び（作業フォルダ・コマンド・承認メッセージ・断片が混在）
SAMPLE = [
    "c:\\Users\\user\\proj",
    "Start-Sleep -Seconds 30; Set-Content _w2.txt done",
    PREFIX + "Start-Sleep -Seconds 30; Set-Content _w2.txt done",
    "Start-Sleep -Seconds 30",
]
got = kaa.summarize_approval(SAMPLE, s)
p(f"  入力 {len(SAMPLE)} 行")
p(f"  結果 -> {got}")
p(f"  承認メッセージ1行だけになった = {got == SAMPLE[2]}")
p(f"  説明 -> {kaa.explain_command(got)}")

NOPREFIX = ["フォルダ名", "git status --short", "git status"]
got2 = kaa.summarize_approval(NOPREFIX, s)
p(f"  前置きなしの場合 -> {got2}")
p(f"  重複した断片が消えた = {'git status --short' in got2 and got2.count('git status') == 1}")

p("")
p("=== 前置き除去テスト ===")
for raw in [
    PREFIX + "git status",
    "Your approval is required to continue: git status",
    "何かの説明 / Your approval is required to continue: git status",
    "git status",
    "",
]:
    p(f"  入力={raw!r}")
    p(f"    コマンド部分={kaa.extract_command(raw)!r}")
    p(f"    説明={kaa.explain_command(raw)!r}")

p("")
p("=== 設定キー ===")
cfg = kaa.load_config()
p(f"ui.show_raw_command  = {cfg['ui'].get('show_raw_command')}")
p(f"ui.max_allowed_items = {cfg['ui'].get('max_allowed_items')}")

p("")
p("=== LogBus.allowed の動作 ===")
bus = kaa.LogBus()
bus.allowed("テスト要約", "git status")
rows = bus.drain_allowed()
p(f"drain_allowed の戻り = {rows}")
p(f"要素数が3つ = {len(rows[0]) == 3 if rows else False}")

p("")
p("VERIFY DONE")
out.close()
