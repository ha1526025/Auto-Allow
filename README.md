# Kiro Auto Allow

Kiro の承認確認 UI

```
Your approval is required to continue: ...
[Allow] [Always allow] [Deny] [Always deny]
```

に出てくる **一番左の「Allow」だけ** を自動でクリックする Windows 11 用の常駐ツールです。

`Always allow` / `Deny` / `Always deny` / `Cancel` は絶対にクリックしません。

---

## 使い方（プログラミング不要）

1. デスクトップの **`KiroAutoAllow.exe`** をダブルクリック
2. 最初は **テストモード: ON** で起動します（検出はするがクリックしない安全な状態）
3. `Auto Allow: OFF` を押して **ON** にする
4. Kiro で承認画面が出たときにログを見て、次のように出れば検出は成功です

```
承認画面を検出しました
承認メッセージ: 「Your approval is required to continue: ...」
検出したボタン：Allow（ControlType=Button Rect=(1528, 826, 1599, 864)）
検出したボタン：Always allow（ControlType=Button Rect=(1608, 826, 1732, 864)）
検出したボタン：Deny（ControlType=Button Rect=(1741, 826, 1809, 864)）
検出したボタン：Always deny（ControlType=Button Rect=(1528, 873, 1650, 911)）
Allow ボタンを検出しました（(1528, 826, 1599, 864)）
クリック対象確認 OK
テストモードのためクリックしませんでした
```

5. 確認できたら **`テストモード: ON` を押して OFF にする**

> **実際にクリックさせるには、テストモードを OFF にする必要があります。**
> テストモードが ON の間は、設計どおり絶対にクリックしません。

---

## 画面

| 表示 | 意味 |
| --- | --- |
| `Auto Allow: ON / OFF` | 監視の親スイッチ。OFF のときは何もしない |
| `テストモード: ON / OFF` | ON = 検出のみ、OFF = 実際にクリック |
| `現在の状態` | 停止中 / 監視中（テストモード）/ 監視中（自動クリック） |
| `Kiro` | Kiro ウィンドウの検出状況 |
| `Allow クリック回数` | 実際にクリックした累計回数 |
| `Allow 検出回数` | クリック対象として確定した累計回数 |
| ログ欄 | 開始・検出・クリック・見送り理由・停止 |

### ボタン

- **今すぐ1回だけ実行** … 監視を使わず、いま出ている承認画面を 1 回だけ処理する（動作確認向け）
- **UI要素を確認** … Kiro から取得できる UI 要素をテキストに書き出して開く
- **ログを消去** / **保存フォルダを開く**

---

## 緊急停止

- 画面の **[緊急停止]** ボタン
- **Esc を素早く 2 回** 押す

Esc は横取りしていないので、他のアプリの Esc 操作を邪魔しません。
1 分間に 12 回を超えてクリックした場合も、暴走とみなして自動で OFF になります。

---

## 誤クリックしないための仕組み

クリックするのは、以下を **すべて** 満たしたときだけです。

1. ウィンドウを所有するプロセスの実行ファイルが **`kiro.exe`**（これが主判定）
2. ウィンドウクラスが `Chrome_WidgetWin_1`
3. 要素の `ControlType` が **Button**
4. ボタン名が **`Allow` と完全一致**（前後の空白のみ除去。`Always allow` や `allow` は不一致）
5. 同じ承認ブロック内に **`Always allow` と `Deny` が存在する**
6. その Allow が **同じ行の一番左**
7. ボタンが有効・画面内で、矩形サイズが妥当、Kiro ウィンドウの内側
8. 要素の所有プロセス ID が Kiro と一致

さらに **クリック直前にもう一度 1〜8 を再確認** します。条件を満たさなくなっていれば中止してログに残します。
候補が複数見つかって特定できない場合もクリックしません。

### クリック方法

1. **UI Automation の Invoke**（マウスを動かさない。最優先）
2. `LegacyIAccessible` の既定アクション
3. 物理クリック（1・2 が効かず、Kiro が最前面のときだけ）

各方法の実行後に「承認 UI が消えたか」を確認し、消えていなければ次の方法に進みます。
座標を決め打ちしていないので、解像度・ウィンドウサイズ・位置が変わっても動きます。

---

## 保存先

```
%LOCALAPPDATA%\KiroAutoAllow\
  config.json      設定
  logs\            日付ごとのログ
  ui_dumps\        UI要素ダンプ
```

### よく使う設定（config.json）

| キー | 既定 | 説明 |
| --- | --- | --- |
| `poll_interval_sec` | `0.8` | 監視間隔（秒） |
| `max_clicks_per_minute` | `12` | 超えたら自動停止 |
| `safety.require_approval_text` | `false` | `true` にすると承認メッセージの文字列も必須にする |
| `safety.on_multiple_candidates` | `"skip"` | Allow が複数見つかったとき。`"bottommost"` にすると一番下（最新）を選ぶ |
| `click.allow_mouse_fallback` | `true` | 物理クリックを許可するか |

---

## ソースから動かす / ビルドする

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe KiroAutoAllow.py
```

exe を作る場合は `build.bat` をダブルクリック（`dist\KiroAutoAllow.exe` ができます）。

### 構成

| ファイル | 役割 |
| --- | --- |
| `KiroAutoAllow.py` | アプリ本体（1 ファイル完結） |
| `KiroAutoAllow.spec` | PyInstaller 設定 |
| `build.bat` | exe ビルド |
| `requirements.txt` | 実行用（`comtypes` のみ） |
| `requirements-dev.txt` | ビルド用（`pyinstaller`） |

画像認識は使っていません。Windows UI Automation で要素名と構造を直接読んでいます。

---

## うまく動かないとき

| 症状 | 対処 |
| --- | --- |
| Allow を押してくれない | **テストモードが OFF になっているか確認** |
| `Kiro のウィンドウが見つかりません` | Kiro を起動し、最小化を解除する |
| `承認 UI と確認できませんでした` | `UI要素を確認` を押し、Button の Name を確認。名前が違えば `config.json` の `target_button_name` / `require_sibling_buttons` を合わせる |
| `Invoke は実行できましたが承認 UI が残っています` | Kiro を最前面にしておくと物理クリックのフォールバックが働きます |
