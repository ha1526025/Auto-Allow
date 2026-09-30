"""承認ブロックからの内容抽出を実機検証する。

承認画面が出ていなくても、抽出関数が「範囲外のテキストを拾わない」ことは
擬似的なボタン矩形を与えて確認できる。
"""
import importlib.util
import sys
from pathlib import Path

here = Path(__file__).parent
out = open(here / "_detail_result.txt", "w", encoding="utf-8")


def p(*a):
    print(*a, file=out, flush=True)


spec = importlib.util.spec_from_file_location("kaa", here / "KiroAutoAllow.py")
kaa = importlib.util.module_from_spec(spec)
sys.modules["kaa"] = kaa
spec.loader.exec_module(kaa)
p("import OK")

cfg = kaa.load_config()
s = cfg["safety"]
p(f"approval_context_height_px  = {s['approval_context_height_px']}")
p(f"approval_context_x_margin_px = {s['approval_context_x_margin_px']}")
p(f"max_allowed_items = {cfg['ui']['max_allowed_items']}")

wins = kaa.find_kiro_windows(cfg)
if not wins:
    p("Kiro が見つかりません")
    out.close()
    sys.exit(0)

uia = kaa.UiaSession()
win = wins[0]
root = uia.element_from_handle(win.hwnd)

res = kaa.scan(uia, cfg, check_busy=True)
p("")
p(f"busy   = {res.busy} ({res.busy_reason})")
p(f"承認関連ボタン = {len(res.approval_buttons)}")
p(f"target = {None if res.target is None else res.target[1].norm_name}")
p(f"approval_detail = {res.approval_detail!r}")
p(f"approval_lines  = {len(res.approval_lines)} 件")
for line in res.approval_lines:
    p(f"    - {line[:120]}")

# --- 承認画面が出ていない場合の代替検証 ---
# 実際の承認ブロックと同じ位置（過去ログの座標）に擬似ボタンを置いて、
# 抽出範囲が限定されているかを確かめる。
if res.target is None:
    p("")
    p("=== 承認画面が無いので擬似ボタンで範囲限定を検証 ===")
    texts = uia.find_texts(root)
    p(f"ウィンドウ内の Text 総数 = {len(texts)}")
    onscreen = [t for t in texts if t.norm_name and not t.offscreen]
    p(f"うち画面内で名前があるもの = {len(onscreen)}")
    if onscreen:
        # 画面下のほうにある要素を基準にする
        base = max(onscreen, key=lambda t: t.rect[1])
        fake = kaa.ElemInfo(
            name="Allow", control_type=kaa.UIA_ButtonControlTypeId,
            rect=(base.rect[0], base.rect[3] + 10,
                  base.rect[0] + 71, base.rect[3] + 48),
            enabled=True, offscreen=False,
        )
        p(f"擬似ボタン rect = {fake.rect}")
        picked = kaa.approval_block_texts(uia, root, [fake], s)
        p(f"抽出された行数 = {len(picked)} （Text 総数 {len(texts)} より大幅に少ないこと）")
        for line in picked[:10]:
            p(f"    - {line[:100]}")
        detail = kaa.summarize_approval(picked, s)
        p(f"まとめ結果の長さ = {len(detail)} (上限 {s['approval_detail_max_chars']})")
        p(f"上限以内 = {len(detail) <= s['approval_detail_max_chars'] + 1}")
        p(f"範囲が限定されている = {len(picked) < len(onscreen)}")

p("")
p("DETAIL TEST DONE")
out.close()
