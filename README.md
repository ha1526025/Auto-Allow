# Kiro Auto Allow

Kiro の承認確認画面

```
Your approval is required to continue: ...
[Allow] [Always allow] [Deny] [Always deny]
```

が出たときに、**一番左の「Allow」だけ**を自動でクリックする Windows 11 用ツールです。

`Always allow` / `Deny` / `Always deny` / `Cancel` は絶対にクリックしません。

Windows UI Automation でボタン名と画面構造を直接読んでいるので、画面解像度・ウィンドウサイズ・ウィンドウ位置が変わっても動きます（座標の決め打ちなし）。

---

## 使い方

1. `KiroAutoAllow.exe` をダブルクリック
2. **`Auto Allow: OFF`** をクリックして **ON** にする
3. そのまま Kiro を使う。承認画面が出れば自動で Allow が押されます

OFF のときは一切何もしません。Python のインストールは不要です。

### 初回起動時の警告について

Microsoft の署名がないため、初回に「WindowsによってPCが保護されました」と表示されることがあります。
**［詳細情報］→［実行］** で起動できます。

---

## 画面

| 表示 | 意味 |
| --- | --- |
| `Auto Allow: ON / OFF` | 自動クリックのスイッチ。OFF のときはクリックしない |
| `完了通知: ON / OFF` | 処理が終わって次のプロンプトを送れる状態になったら知らせる |
| `現在の状態` | 停止中 / 監視中 |
| `Kiro` | Kiro の検出状況と、処理中／待機中 |
| `Allow クリック回数` | 実際にクリックした累計回数 |
| `Allow 検出回数` | クリック対象として確定した累計回数 |
| ログ欄 | 開始・検出・クリック・見送り理由・停止 |

### 完了通知

Kiro は処理中のあいだ画面下に `Working` と `Cancel` を表示します。
これが消えて承認待ちもなくなった状態が 2 秒続いたら「完了」と判断し、次のように知らせます。

- **このアプリのウィンドウを最前面に表示する**（他のアプリで作業中でもその手前に出る）
- 音を鳴らす
- タスクバーのアイコンを点滅させる
- 画面に「✓ 処理が完了しました。次のプロンプトを送信できます」と緑帯で表示（クリックで消えます）
- ログに記録

最前面表示は**通知が出たときだけ**です。常時最前面に固定はしません。
既定では 5 秒後に自動で解除され、緑帯をクリックすれば即座に解除されます。

**フォーカスは奪いません。**前面に出るだけなので、他のアプリで入力中でもキー入力が奪われません。
アクティブウィンドウごと切り替えたい場合は `config.json` の `notify.focus_window` を `true` にしてください
（ただし Windows 側の制限で成功しないことがあります）。

`完了通知` は `Auto Allow` とは独立しています。自動クリックを使わず通知だけ使うこともできます。

### 自動クリック時に画面が切り替わらないようにする

クリックは UI Automation の `Invoke` で行うのでマウスは動きません。
それでも Kiro が自分を前面に出してくる場合があるため、クリック前の前面ウィンドウを記憶し、
クリック後に元のウィンドウへフォーカスを戻します（0.05 / 0.35 / 0.9 秒後に最大 3 回試行）。

無効にする場合は `config.json` の `click.keep_foreground` を `false` にしてください。

### ボタン

| ボタン | 用途 |
| --- | --- |
| 今すぐ1回だけ実行 | 監視を使わず、いま出ている承認画面を 1 回だけ処理する |
| UI要素を確認 | Kiro から取得できる UI 要素をテキストに書き出して開く（不具合調査用） |
| ログを消去 / 保存フォルダを開く | — |
| 緊急停止 | 監視を即座に OFF |

### ログの例

```
20:06:38 Auto Allow を開始しました
20:06:38 Kiro を検出しました: 'sample - Kiro' (hwnd=0x1075E, pid=8768, kiro.exe)
20:07:25 承認画面を検出しました
20:07:25 承認メッセージ: Your approval is required to continue: ...
20:07:25 検出したボタン：Allow（ControlType=Button Rect=(1528, 826, 1599, 864)）
20:07:25 検出したボタン：Always allow（ControlType=Button Rect=(1608, 826, 1732, 864)）
20:07:25 検出したボタン：Deny（ControlType=Button Rect=(1741, 826, 1809, 864)）
20:07:25 検出したボタン：Always deny（ControlType=Button Rect=(1528, 873, 1650, 911)）
20:07:25 Allow ボタンを検出しました（(1528, 826, 1599, 864)）
20:07:25 クリック対象確認 OK
20:07:26 Allow をクリックしました（UI Automation / Invoke）
```

---

## 緊急停止

- 画面の **［緊急停止］** ボタン
- **Esc を素早く 2 回** 押す

Esc は横取りしていないので、他アプリの Esc 操作を邪魔しません。
1 分間に 12 回を超えてクリックした場合も、暴走とみなして自動で OFF になります。

---

## 誤クリックしないための仕組み

クリックするのは以下を **すべて** 満たしたときだけです。

1. ウィンドウを所有するプロセスの実行ファイルが **`kiro.exe`**（主判定）
2. ウィンドウクラスが `Chrome_WidgetWin_1`
3. 要素の `ControlType` が **Button**
4. ボタン名が **`Allow` と完全一致**（前後の空白のみ除去。`Always allow` や `allow` は不一致）
5. 同じ承認ブロック内に **`Always allow` と `Deny` が存在する**
6. その Allow が **同じ行の一番左**
7. ボタンが有効・画面内で、矩形サイズが妥当、Kiro ウィンドウの内側
8. 要素の所有プロセス ID が Kiro と一致

さらに **クリック直前にもう一度 1〜8 を再確認**します。条件を満たさなくなっていれば中止してログに残します。
Allow 候補が複数見つかって特定できない場合もクリックしません。

### クリック方法

1. **UI Automation の Invoke**（マウスを動かさない。最優先）
2. `LegacyIAccessible` の既定アクション
3. 物理クリック（1・2 が効かず、Kiro が最前面のときだけ）

各方法の実行後に「承認画面が実際に消えたか」を確認し、消えていなければ次の方法に進みます。

---

## 保存先

```
%LOCALAPPDATA%\KiroAutoAllow\
  config.json      設定（初回起動時に自動生成）
  logs\            日付ごとのログ
  ui_dumps\        UI要素ダンプ
```

アンインストールは exe を削除し、上のフォルダを消すだけです。レジストリは触りません。

### よく使う設定（config.json）

| キー | 既定 | 説明 |
| --- | --- | --- |
| `poll_interval_sec` | `0.8` | 監視間隔（秒） |
| `max_clicks_per_minute` | `12` | 超えたら自動停止 |
| `target_button_name` | `"Allow"` | クリック対象のボタン名（完全一致） |
| `safety.require_approval_text` | `false` | `true` にすると承認メッセージの文字列も必須にする |
| `safety.on_multiple_candidates` | `"skip"` | Allow が複数あるとき。`"bottommost"` で一番下（最新）を選ぶ |
| `click.allow_mouse_fallback` | `true` | 物理クリックを許可するか |
| `click.keep_foreground` | `true` | クリック後に元のウィンドウへフォーカスを戻す |
| `notify.enabled` | `true` | 完了通知の初期状態 |
| `notify.busy_text_names` | `["Working"]` | 処理中と判定する Text 名（完全一致） |
| `notify.busy_button_names` | `["Cancel"]` | 処理中と判定する Button 名（完全一致） |
| `notify.ready_idle_sec` | `2.0` | 処理中表示が消えてから完了とみなすまでの秒数 |
| `notify.sound` / `notify.flash_taskbar` | `true` | 音 / タスクバー点滅 |
| `notify.bring_to_front` | `true` | 完了時だけウィンドウを最前面に出す |
| `notify.front_seconds` | `5.0` | 最前面を維持する秒数。経過後に自動解除 |
| `notify.focus_window` | `false` | フォーカスも移すか（入力を奪うので既定は無効） |
| `ui.always_on_top` | `false` | ウィンドウを常に最前面にする |

---

## うまく動かないとき

| 症状 | 対処 |
| --- | --- |
| `Kiro のウィンドウが見つかりません` | Kiro を起動し、最小化を解除する |
| `承認 UI と確認できませんでした` | ［UI要素を確認］を押し、Button の Name を確認。名前が違えば `config.json` の `target_button_name` と `safety.require_sibling_buttons` を合わせる |
| `Invoke は実行できましたが承認 UI が残っています` | Kiro を最前面にしておくと物理クリックのフォールバックが働きます |
| 何も反応しない | `Auto Allow` が ON になっているか確認 |

---

## ソースから動かす / ビルドする

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe KiroAutoAllow.py
```

exe を作るだけなら `build.bat`、配布用 ZIP まで作るなら `make_release.bat` を実行します。

| ファイル | 役割 |
| --- | --- |
| `KiroAutoAllow.py` | アプリ本体（1 ファイル完結） |
| `KiroAutoAllow.spec` | PyInstaller 設定 |
| `build.bat` | exe をビルドするだけ |
| `make_release.bat` | 検証 → ビルド → ZIP 作成 → 展開起動テストまで一括 |
| `verify_explain.py` | コマンド→日本語変換の自動テスト |
| `package.ps1` | ZIP 作成と「別フォルダへ展開して起動」テスト |
| `requirements.txt` | 実行用（`comtypes` のみ） |
| `requirements-dev.txt` | ビルド用（`pyinstaller`） |
| `release/` | 配布物（`KiroAutoAllow.exe` と `README.txt` のみ） |
| `Kiro-Auto-Allow.zip` | 配布用 ZIP |

### 配布物の作り方

`make_release.bat` を実行すると次の順で処理し、結果を
`verify_result.txt` と `package_result.txt` に書き出します。

1. 変換ロジックの自動テスト（24 パターン ＋ 冗長ラベル検査）
2. `--clean` 付きで exe をビルド
3. `release` を exe + README.txt で再構成
4. `Kiro-Auto-Allow.zip` を作成
5. `%TEMP%` の別フォルダへ展開し、そこから起動して独立性を確認

5 では、起動したプロセスの実行パスがテストフォルダであること、
アプリのログが増えること（UI Automation の初期化成功）を確認します。
exe に `.venv` / `site-packages` / `C:\Users` の文字列が含まれないことも
検査しています。

> `package.ps1` は ASCII のみで書いています。PowerShell 5.1 は `.ps1` を
> システムのコードページ（日本語環境では cp932）として読むため、
> UTF-8 の日本語を入れると構文エラーになります。編集時は注意してください。

動作環境: Windows 11 / 64bit。画像認識は使っていません。

### コード署名について

署名は付けていません。そのため配布先で SmartScreen の警告が出ることが
あります。回避処理は意図的に実装していません。対処方法は
`release/README.txt` に記載しています。
