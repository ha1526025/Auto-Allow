"""Kiro Auto Allow  (単一ファイル版)

Kiro の承認確認 UI（"Your approval is required to continue ..."）に出る
一番左の「Allow」ボタンだけを Windows UI Automation で特定して自動クリックする。

絶対にクリックしないもの:
  Always allow / Deny / Always deny / Cancel / Reject
  Kiro 以外のウィンドウにある Allow

起動:
    python KiroAutoAllow.py

必要ライブラリ:
    pip install comtypes
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

APP_NAME = "Kiro Auto Allow"
APP_VERSION = "1.0.0"

# =============================================================================
#  保存先
# =============================================================================


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / "KiroAutoAllow"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_data_dir() / "config.json"


def logs_dir() -> Path:
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dumps_dir() -> Path:
    d = app_data_dir() / "ui_dumps"
    d.mkdir(parents=True, exist_ok=True)
    return d


# =============================================================================
#  設定
# =============================================================================

DEFAULTS: dict[str, Any] = {
    "poll_interval_sec": 0.8,
    "post_click_cooldown_sec": 1.5,
    "max_clicks_per_minute": 12,
    # 完全一致でこの名前のときだけクリックする（"Always allow" は一致しない）
    "target_button_name": "Allow",
    "never_click_names": [
        "always allow", "deny", "always deny", "cancel", "reject", "always",
    ],
    "kiro": {
        "process_names": ["kiro.exe"],
        "window_classes": ["Chrome_WidgetWin_1"],
        "title_contains": "",
    },
    "safety": {
        "require_sibling_buttons": ["always allow", "deny"],
        "group_vertical_tolerance_px": 160,
        "require_allow_is_leftmost": True,
        "same_row_tolerance_px": 24,
        "require_approval_text": False,
        "approval_text_patterns": [
            "approval is required",
            "your approval is required to continue",
            "承認が必要",
        ],
        "min_button_width": 20,
        "max_button_width": 400,
        "min_button_height": 12,
        "max_button_height": 90,
        # 候補が複数: "skip"（何もしない） / "bottommost"（一番下＝最新）
        "on_multiple_candidates": "skip",
        # 「何を許可するのか」を読み取る範囲。ボタンの上方向 px と左右のはみ出し許容 px
        "approval_context_height_px": 320,
        "approval_context_x_margin_px": 60,
        "approval_detail_max_chars": 300,
    },
    "click": {
        "use_invoke": True,
        "use_legacy_default_action": True,
        "allow_mouse_fallback": True,
        "mouse_requires_foreground": True,
        "restore_cursor": True,
        # クリックで Kiro が前面に出てきてしまう場合、元のウィンドウに戻す
        "keep_foreground": True,
    },

    # --- 「次のプロンプトを送れる状態」の通知 ---
    "notify": {
        "enabled": True,
        # Kiro が処理中のあいだ表示される要素（完全一致で判定）
        "busy_text_names": ["Working"],
        "busy_button_names": ["Cancel"],
        # 処理中の表示が消えてからこの秒数だけ変化がなければ「完了」とみなす
        "ready_idle_sec": 2.0,
        "sound": True,
        "flash_taskbar": True,
        # 完了したときだけウィンドウを最前面に出す（常時固定はしない）
        "bring_to_front": True,
        # 最前面を維持する秒数。経過後に自動で解除する
        "front_seconds": 5.0,
        # フォーカスも奪うか。Windows の制限で失敗することが多く、
        # 作業中のキー入力を取られると困るので既定は False。
        # False でも最前面には出るので通知には気付ける。
        "focus_window": False,
    },
    "emergency": {"double_esc": True, "double_esc_window_ms": 600},
    "ui": {
        "auto_allow_default": False,
        "max_log_lines": 800,
        "max_allowed_items": 30,
        # Allow した内容の欄に、実際のコマンドも併記するか
        "show_raw_command": True,
        "always_on_top": False,
    },
    "dump": {"max_depth": 16, "max_nodes": 6000, "open_after_dump": True},
}


def _deep_merge(base: dict, over: dict) -> dict:
    import copy

    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    p = config_path()
    user: dict[str, Any] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                user = loaded
        except Exception:
            user = {}
    cfg = _deep_merge(DEFAULTS, user)
    if not p.exists():
        try:
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return cfg


# =============================================================================
#  ログ
# =============================================================================

INFO, OK, WARN, ERROR, DETECT = "info", "ok", "warn", "error", "detect"


class LogBus:
    def __init__(self) -> None:
        self._q: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        self._allowed_q: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        self._lock = threading.Lock()
        self._file: Path | None = None
        self._day = None

    def log(self, message: str, level: str = INFO) -> None:
        now = datetime.now()
        self._q.put((now.strftime("%H:%M:%S"), message, level))
        try:
            with self._lock:
                if self._file is None or self._day != now.date():
                    self._day = now.date()
                    self._file = logs_dir() / f"kiro_auto_allow_{now:%Y%m%d}.log"
                with self._file.open("a", encoding="utf-8") as fp:
                    fp.write(f"{now:%Y-%m-%d %H:%M:%S} [{level}] {message}\n")
        except Exception:
            pass

    def info(self, m: str) -> None:
        self.log(m, INFO)

    def ok(self, m: str) -> None:
        self.log(m, OK)

    def warn(self, m: str) -> None:
        self.log(m, WARN)

    def error(self, m: str) -> None:
        self.log(m, ERROR)

    def detect(self, m: str) -> None:
        self.log(m, DETECT)

    def allowed(self, summary: str, detail: str) -> None:
        """Allow した内容を専用欄とログの両方へ流す。

        summary は初心者向けの言い換え、detail は実際のコマンド。
        """
        self._allowed_q.put((datetime.now().strftime("%H:%M:%S"), summary, detail))
        self.log(f"許可した内容: {summary}", OK)
        if detail:
            self.log(f"　実行されたコマンド: {detail}", INFO)

    def drain_allowed(self, limit: int = 50) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for _ in range(limit):
            try:
                out.append(self._allowed_q.get_nowait())
            except queue.Empty:
                break
        return out

    def drain(self, limit: int = 300) -> list[tuple[str, str, str]]:
        out = []
        for _ in range(limit):
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out


# =============================================================================
#  Win32
# =============================================================================

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
VK_ESCAPE = 0x1B


def enum_top_level_windows() -> list[int]:
    found: list[int] = []

    def _cb(hwnd, _l):
        found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


def window_title(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, b, 512)
    return b.value


def window_class(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, b, 256)
    return b.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right, r.bottom)


def process_image_path(pid: int) -> str:
    if pid <= 0:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004


def cursor_pos() -> tuple[int, int]:
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def physical_left_click(x: int, y: int, restore: bool = True) -> bool:
    old = cursor_pos()
    if not user32.SetCursorPos(int(x), int(y)):
        return False
    time.sleep(0.03)
    ev = (_INPUT * 2)()
    for i, flag in enumerate((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)):
        ev[i].type = 0
        ev[i].mi = _MOUSEINPUT(0, 0, 0, flag, 0, 0)
    sent = user32.SendInput(2, ev, ctypes.sizeof(_INPUT))
    if restore:
        time.sleep(0.03)
        user32.SetCursorPos(old[0], old[1])
    return sent == 2


user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

GA_ROOT = 2
SW_RESTORE = 9


def root_hwnd(hwnd: int) -> int:
    """tkinter の winfo_id() は子ウィンドウを返すことがあるので、実体の
    トップレベルウィンドウのハンドルに変換する。"""
    try:
        top = user32.GetAncestor(hwnd, GA_ROOT)
        return int(top) if top else hwnd
    except Exception:
        return hwnd


def restore_if_minimized(hwnd: int) -> None:
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        pass


user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.MessageBeep.argtypes = [wintypes.UINT]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MB_ICONASTERISK = 0x00000040


def foreground_window() -> int:
    """前面ウィンドウ。無いときは 0。

    ウィンドウ切り替えの瞬間などに NULL が返るため、必ず None を潰す。
    """
    h = user32.GetForegroundWindow()
    return int(h) if h else 0


def restore_foreground(hwnd: int) -> bool:
    """指定ウィンドウを前面に戻す。

    Windows は勝手な前面化を制限しているため、いまの前面ウィンドウの
    入力スレッドに一時的に相乗り（AttachThreadInput）してから要求する。
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    cur = foreground_window()
    if cur == hwnd:
        return True
    our = kernel32.GetCurrentThreadId()
    target_thread = wintypes.DWORD(0)
    if cur:
        user32.GetWindowThreadProcessId(cur, ctypes.byref(target_thread))
    attached = False
    try:
        if target_thread.value and target_thread.value != our:
            attached = bool(user32.AttachThreadInput(our, target_thread.value, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(our, target_thread.value, False)
            except Exception:
                pass
    return foreground_window() == hwnd


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


user32.FlashWindowEx.argtypes = [ctypes.POINTER(_FLASHWINFO)]
user32.FlashWindowEx.restype = wintypes.BOOL

FLASHW_ALL = 0x00000003
FLASHW_TIMERNOFG = 0x0000000C


def flash_window(hwnd: int, count: int = 6) -> None:
    """タスクバーを点滅させて知らせる（フォーカスは奪わない）。"""
    try:
        fi = _FLASHWINFO(
            ctypes.sizeof(_FLASHWINFO), wintypes.HWND(hwnd),
            FLASHW_ALL | FLASHW_TIMERNOFG, count, 0,
        )
        user32.FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass


def notify_beep() -> None:
    try:
        user32.MessageBeep(MB_ICONASTERISK)
    except Exception:
        pass


class DoubleEscWatcher:
    """Esc を横取りせずに監視し、素早い 2 回押しを検出する。"""

    def __init__(self, window_ms: int = 600) -> None:
        self.window_sec = window_ms / 1000.0
        self._was_down = False
        self._last = 0.0

    def poll(self) -> bool:
        down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
        hit = False
        if down and not self._was_down:
            now = time.monotonic()
            if now - self._last <= self.window_sec:
                hit = True
                self._last = 0.0
            else:
                self._last = now
        self._was_down = down
        return hit


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# =============================================================================
#  UI Automation
# =============================================================================

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402

UIA_RuntimeIdPropertyId = 30000
UIA_BoundingRectanglePropertyId = 30001
UIA_ProcessIdPropertyId = 30002
UIA_ControlTypePropertyId = 30003
UIA_LocalizedControlTypePropertyId = 30004
UIA_NamePropertyId = 30005
UIA_IsEnabledPropertyId = 30010
UIA_AutomationIdPropertyId = 30011
UIA_ClassNamePropertyId = 30012
UIA_HelpTextPropertyId = 30013
UIA_IsOffscreenPropertyId = 30022
UIA_IsInvokePatternAvailablePropertyId = 30031

UIA_ButtonControlTypeId = 50000
UIA_TextControlTypeId = 50020

CONTROL_TYPE_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}

TreeScope_Element = 1
TreeScope_Descendants = 4
AutomationElementMode_Full = 1
UIA_InvokePatternId = 10000
UIA_LegacyIAccessiblePatternId = 10018

_mod_lock = threading.Lock()
_mod: Any = None


def uia_module():
    global _mod
    with _mod_lock:
        if _mod is None:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as m
            _mod = m
    return _mod


def control_type_name(ct: int) -> str:
    return CONTROL_TYPE_NAMES.get(int(ct or 0), f"Unknown({ct})")


def normalize_name(raw: str | None) -> str:
    """比較用に整える。前後空白・NBSP・全角空白を除去し連続空白を 1 個に潰す。

    大文字小文字は変えない（完全一致判定のため）。
    """
    if not raw:
        return ""
    s = str(raw).replace("\u00a0", " ").replace("\u3000", " ")
    return " ".join(s.split())


@dataclass
class ElemInfo:
    name: str = ""
    control_type: int = 0
    automation_id: str = ""
    class_name: str = ""
    localized_type: str = ""
    help_text: str = ""
    enabled: bool = False
    offscreen: bool = True
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    pid: int = 0
    runtime_id: tuple[int, ...] = ()
    invokable: bool = False
    element: Any = field(default=None, repr=False)

    @property
    def control_type_name(self) -> str:
        return control_type_name(self.control_type)

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def center(self) -> tuple[int, int]:
        return ((self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2)

    @property
    def center_y(self) -> int:
        return (self.rect[1] + self.rect[3]) // 2

    @property
    def left(self) -> int:
        return self.rect[0]

    @property
    def norm_name(self) -> str:
        return normalize_name(self.name)

    def key(self) -> str:
        return ",".join(str(i) for i in self.runtime_id) or f"rect{self.rect}"


class UiaSession:
    """1 スレッド分の UIA セッション。使うスレッド内で生成すること。"""

    def __init__(self) -> None:
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
        m = uia_module()
        self.m = m
        self.iuia = comtypes.client.CreateObject(m.CUIAutomation, interface=m.IUIAutomation)
        self._button_cond = self.iuia.CreatePropertyCondition(
            UIA_ControlTypePropertyId, UIA_ButtonControlTypeId
        )
        self._text_cond = self.iuia.CreatePropertyCondition(
            UIA_ControlTypePropertyId, UIA_TextControlTypeId
        )
        self._cache = self._make_cache()

    def _make_cache(self):
        cr = self.iuia.CreateCacheRequest()
        for pid in (
            UIA_NamePropertyId, UIA_ControlTypePropertyId, UIA_AutomationIdPropertyId,
            UIA_ClassNamePropertyId, UIA_LocalizedControlTypePropertyId,
            UIA_HelpTextPropertyId, UIA_IsEnabledPropertyId, UIA_IsOffscreenPropertyId,
            UIA_BoundingRectanglePropertyId, UIA_ProcessIdPropertyId,
            UIA_RuntimeIdPropertyId, UIA_IsInvokePatternAvailablePropertyId,
        ):
            try:
                cr.AddProperty(pid)
            except Exception:
                pass
        cr.TreeScope = TreeScope_Element
        cr.AutomationElementMode = AutomationElementMode_Full
        return cr

    def element_from_handle(self, hwnd: int):
        return self.iuia.ElementFromHandle(hwnd)

    # ---- 取得 -------------------------------------------------------------
    def _cached_info(self, el) -> ElemInfo:
        i = ElemInfo(element=el)
        for attr, setter in (
            ("CachedName", lambda v: setattr(i, "name", v or "")),
            ("CachedControlType", lambda v: setattr(i, "control_type", int(v or 0))),
            ("CachedAutomationId", lambda v: setattr(i, "automation_id", v or "")),
            ("CachedClassName", lambda v: setattr(i, "class_name", v or "")),
            ("CachedLocalizedControlType", lambda v: setattr(i, "localized_type", v or "")),
            ("CachedHelpText", lambda v: setattr(i, "help_text", v or "")),
            ("CachedIsEnabled", lambda v: setattr(i, "enabled", bool(v))),
            ("CachedIsOffscreen", lambda v: setattr(i, "offscreen", bool(v))),
            ("CachedProcessId", lambda v: setattr(i, "pid", int(v or 0))),
        ):
            try:
                setter(getattr(el, attr))
            except Exception:
                pass
        try:
            r = el.CachedBoundingRectangle
            i.rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        try:
            rid = el.GetCachedPropertyValue(UIA_RuntimeIdPropertyId)
            i.runtime_id = tuple(int(x) for x in rid) if rid else ()
        except Exception:
            pass
        try:
            i.invokable = bool(
                el.GetCachedPropertyValue(UIA_IsInvokePatternAvailablePropertyId)
            )
        except Exception:
            pass
        return i

    def current_info(self, el) -> ElemInfo:
        """キャッシュを使わず今の状態を読み直す（クリック直前の再確認用）。"""
        i = ElemInfo(element=el)
        for attr, setter in (
            ("CurrentName", lambda v: setattr(i, "name", v or "")),
            ("CurrentControlType", lambda v: setattr(i, "control_type", int(v or 0))),
            ("CurrentAutomationId", lambda v: setattr(i, "automation_id", v or "")),
            ("CurrentClassName", lambda v: setattr(i, "class_name", v or "")),
            ("CurrentLocalizedControlType", lambda v: setattr(i, "localized_type", v or "")),
            ("CurrentIsEnabled", lambda v: setattr(i, "enabled", bool(v))),
            ("CurrentIsOffscreen", lambda v: setattr(i, "offscreen", bool(v))),
            ("CurrentProcessId", lambda v: setattr(i, "pid", int(v or 0))),
        ):
            try:
                setter(getattr(el, attr))
            except Exception:
                pass
        try:
            r = el.CurrentBoundingRectangle
            i.rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        try:
            rid = el.GetRuntimeId()
            i.runtime_id = tuple(int(x) for x in rid) if rid else ()
        except Exception:
            pass
        try:
            i.invokable = bool(
                el.GetCurrentPropertyValue(UIA_IsInvokePatternAvailablePropertyId)
            )
        except Exception:
            pass
        return i

    def _find_all(self, root, cond) -> list[ElemInfo]:
        try:
            arr = root.FindAllBuildCache(TreeScope_Descendants, cond, self._cache)
        except Exception:
            return []
        if arr is None:
            return []
        try:
            n = int(arr.Length)
        except Exception:
            return []
        out: list[ElemInfo] = []
        for k in range(n):
            try:
                out.append(self._cached_info(arr.GetElement(k)))
            except Exception:
                continue
        return out

    def find_buttons(self, root) -> list[ElemInfo]:
        return self._find_all(root, self._button_cond)

    def find_texts(self, root) -> list[ElemInfo]:
        return self._find_all(root, self._text_cond)

    # ---- 操作 -------------------------------------------------------------
    def invoke(self, el) -> bool:
        try:
            pat = el.GetCurrentPattern(UIA_InvokePatternId)
        except Exception:
            return False
        if not pat:
            return False
        try:
            pat.QueryInterface(self.m.IUIAutomationInvokePattern).Invoke()
            return True
        except Exception:
            return False

    def legacy_default_action(self, el) -> bool:
        try:
            pat = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId)
        except Exception:
            return False
        if not pat:
            return False
        try:
            pat.QueryInterface(
                self.m.IUIAutomationLegacyIAccessiblePattern
            ).DoDefaultAction()
            return True
        except Exception:
            return False

    def parent_of(self, el):
        try:
            return self.iuia.ControlViewWalker.GetParentElement(el)
        except Exception:
            return None

    def first_child(self, el):
        try:
            return self.iuia.ControlViewWalker.GetFirstChildElement(el)
        except Exception:
            return None

    def next_sibling(self, el):
        try:
            return self.iuia.ControlViewWalker.GetNextSiblingElement(el)
        except Exception:
            return None


# =============================================================================
#  Kiro ウィンドウ特定
# =============================================================================


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    exe_path: str
    rect: tuple[int, int, int, int]

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.exe_path).lower()

    def contains(self, r: tuple[int, int, int, int], margin: int = 6) -> bool:
        l, t, rr, b = self.rect
        return (
            r[0] >= l - margin and r[1] >= t - margin
            and r[2] <= rr + margin and r[3] <= b + margin
        )

    def describe(self) -> str:
        return f"'{self.title}' (hwnd=0x{self.hwnd:X}, pid={self.pid}, {self.exe_name})"


def find_kiro_windows(cfg: dict) -> list[WindowInfo]:
    """表示中の Kiro のトップレベルウィンドウだけを列挙する。

    判定の主役はウィンドウ所有プロセスの実行ファイル名（kiro.exe）。
    """
    kcfg = cfg.get("kiro", {})
    want_exes = {str(x).lower() for x in kcfg.get("process_names", ["kiro.exe"])}
    want_classes = {str(x) for x in kcfg.get("window_classes", []) if x}
    title_contains = str(kcfg.get("title_contains", "") or "")

    exe_cache: dict[int, str] = {}
    out: list[WindowInfo] = []
    for hwnd in enum_top_level_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        title = window_title(hwnd)
        if not title:
            continue
        rect = window_rect(hwnd)
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            continue
        pid = window_pid(hwnd)
        if pid <= 0:
            continue
        exe = exe_cache.get(pid)
        if exe is None:
            exe = process_image_path(pid)
            exe_cache[pid] = exe
        if os.path.basename(exe).lower() not in want_exes:
            continue
        cls = window_class(hwnd)
        if want_classes and cls not in want_classes:
            continue
        if title_contains and title_contains.lower() not in title.lower():
            continue
        out.append(WindowInfo(hwnd, pid, title, cls, exe, rect))
    return out


# =============================================================================
#  承認 UI / Allow ボタンの検出
# =============================================================================

APPROVAL_NAMES_LOWER = {"allow", "always allow", "deny", "always deny"}


@dataclass
class ScanResult:
    windows: list[WindowInfo] = field(default_factory=list)
    # (ウィンドウ, ボタン) 承認 UI 関連として見つかったボタン全部（ログ用）
    approval_buttons: list[tuple[WindowInfo, ElemInfo]] = field(default_factory=list)
    approval_ui_found: bool = False
    approval_text: str = ""
    # 承認ブロックから読み取った「何を許可するのか」
    approval_lines: list[str] = field(default_factory=list)
    approval_detail: str = ""
    target: tuple[WindowInfo, ElemInfo] | None = None
    checks: list[str] = field(default_factory=list)
    skip_reasons: list[str] = field(default_factory=list)
    # Kiro が処理中かどうか（"Working" 表示などで判定）
    busy: bool = False
    busy_reason: str = ""


def _size_ok(b: ElemInfo, s: dict) -> bool:
    return (
        s["min_button_width"] <= b.width <= s["max_button_width"]
        and s["min_button_height"] <= b.height <= s["max_button_height"]
    )


def _strip_icon_chars(name: str) -> str:
    """アイコン用の私用領域文字を落とす。

    VS Code / Kiro は codicon をテキストとして持っているため、
    そのまま表示すると空白のように見える行が混ざる。
    """
    out = []
    for ch in name:
        o = ord(ch)
        if 0xE000 <= o <= 0xF8FF:          # 基本多言語面の私用領域
            continue
        if 0xF0000 <= o <= 0x10FFFD:       # 補助私用領域
            continue
        out.append(ch)
    return "".join(out).strip()


def _is_meaningful_text(name: str) -> bool:
    """人が読める内容が 2 文字以上あるか。"""
    return len(_strip_icon_chars(name)) >= 2


def approval_block_texts(uia: UiaSession, root, buttons: list[ElemInfo],
                         s: dict) -> list[str]:
    """承認ボタン群の「すぐ上」にあるテキストだけを拾う。

    ウィンドウ全体から探すとチャットの過去ログまで拾ってしまうので、
    ボタンの座標を基準に範囲を限定する。
    """
    if not buttons:
        return []
    left = min(b.rect[0] for b in buttons)
    right = max(b.rect[2] for b in buttons)
    top = min(b.rect[1] for b in buttons)
    reach = int(s.get("approval_context_height_px", 320))
    xmargin = int(s.get("approval_context_x_margin_px", 60))

    picked: list[ElemInfo] = []
    for t in uia.find_texts(root):
        name = t.norm_name
        if not name or t.offscreen or not _is_meaningful_text(name):
            continue
        r = t.rect
        if r[3] > top + 4:            # ボタンより下にあるものは無関係
            continue
        if r[1] < top - reach:        # 離れすぎているものは別の投稿
            continue
        if r[2] < left - xmargin or r[0] > right + xmargin:
            continue                  # 横方向にずれているものも除外
        picked.append(t)

    picked.sort(key=lambda t: (t.rect[1], t.rect[0]))
    out: list[str] = []
    seen: set[str] = set()
    for t in picked:
        n = _strip_icon_chars(t.norm_name)
        if not _is_meaningful_text(t.norm_name):
            continue                  # アイコンだけの要素は捨てる
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


#: 承認メッセージの定型文（この後ろが実際のコマンド）
_APPROVAL_PREFIX_RE = re.compile(
    r"^.*?(?:approval is required to continue|承認が必要)\s*[:：]?\s*",
    re.IGNORECASE | re.DOTALL,
)

#: コマンド文字列 → 初心者向けの日本語。
#: 上から順に判定するので、危険な操作と具体的な操作を先に置いている。
#: (正規表現, 日本語の説明, 注意が必要か, 系統)
#: 系統が同じルールは最初に一致した 1 つだけを採用する。
#: これで「アプリを .exe にビルドする ＋ Python のプログラムを実行する」
#: のような冗長な並びを防ぐ。
_COMMAND_RULES: list[tuple[str, str, bool, str]] = [
    # ---------------- 取り返しがつきにくい操作 ----------------
    (r"\brm\s+-[a-z]*[rf]|\bRemove-Item\b|\bdel\s|\berase\s|\brmdir\b|\brd\s+/s",
     "ファイルやフォルダを削除する", True, "file_del"),
    (r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f",
     "作業中の変更を捨てて元に戻す", True, "git"),
    (r"\b(format|diskpart|mkfs)\b", "ディスクを初期化する", True, "disk"),
    (r"\b(shutdown|Restart-Computer|Stop-Computer)\b",
     "PC を再起動・シャットダウンする", True, "power"),
    (r"\breg\s+(add|delete|import)\b|\bNew-ItemProperty\b"
     r"|\bSet-ItemProperty\b[^\n]*HK(LM|CU)",
     "Windows の設定（レジストリ）を書き換える", True, "registry"),
    (r"\b(taskkill|Stop-Process)\b",
     "起動中のプログラムを強制終了する", True, "process"),
    (r"\bgit\s+push\b",
     "変更をリモートに送信する（git push）", True, "git"),
    (r"\b(pip|npm|yarn|pnpm)\s+uninstall\b|\bpip\s+remove\b",
     "ライブラリを削除する", True, "package"),
    (r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b",
     "インターネットに通信する", True, "network"),
    (r"\b(icacls|takeown|attrib)\b",
     "ファイルの権限や属性を変更する", True, "perm"),

    # ---------------- Git ----------------
    (r"\bgit\s+commit\b", "変更を記録する（git commit）", False, "git"),
    (r"\bgit\s+add\b", "変更をコミット対象に追加する", False, "git"),
    (r"\bgit\s+(pull|fetch|clone)\b", "リモートの変更を取得する", False, "git"),
    (r"\bgit\s+(status|log|diff|show|reflog)\b",
     "Git の状態を確認する", False, "git"),
    (r"\bgit\s+(checkout|switch|branch|merge|rebase|stash)\b",
     "ブランチを操作する", False, "git"),
    (r"\bgit\b", "Git を操作する", False, "git"),

    # ---------------- パッケージ管理 ----------------
    (r"\bpip\s+install\b", "Python のライブラリを追加する", False, "package"),
    (r"\b(npm|yarn|pnpm)\s+(install|add|ci)\b",
     "Node.js のパッケージを追加する", False, "package"),
    (r"\b(npm|yarn|pnpm)\s+run\b",
     "Node.js のスクリプトを実行する", False, "package"),
    (r"\b(winget|choco)\b", "アプリをインストールする", True, "package"),

    # ---------------- Python ----------------
    (r"\bPyInstaller\b", "アプリを .exe にビルドする", False, "python"),
    (r"\bpy_compile\b",
     "Python コードの文法をチェックする", False, "python"),
    (r"\b(pytest|unittest)\b", "テストを実行する", False, "python"),
    (r"-m\s+venv\b|\bvirtualenv\b",
     "Python の作業環境を新しく作る", False, "python"),
    (r"\bpython(w|3)?(\.exe)?\b",
     "Python のプログラムを実行する", False, "python"),

    # ---------------- 圧縮・展開 ----------------
    # ファイル名の「.zip」に反応しないよう、コマンド名だけを見る
    (r"\bExpand-Archive\b|\btar\s+-[a-z]*x|\bunzip\b",
     "ZIP などを展開する", False, "archive"),
    (r"\bCompress-Archive\b|\btar\s+-[a-z]*c|\b7z\s+a\b|\bzip\s+-",
     "ファイルを ZIP にまとめる", False, "archive"),

    # ---------------- ファイル操作 ----------------
    (r"\b(Copy-Item|xcopy|robocopy)\b|\bcopy\s",
     "ファイルをコピーする", False, "file_copy"),
    (r"\b(Move-Item|Rename-Item)\b|\bmove\s|\bren\s",
     "ファイルを移動・名前変更する", False, "file_move"),
    (r"\b(New-Item|mkdir)\b|\bmd\s",
     "フォルダやファイルを新しく作る", False, "file_new"),
    (r"\b(Set-Content|Add-Content|Out-File)\b",
     "ファイルに書き込む", False, "file_write"),
    (r"\b(Get-Content|Select-String|findstr|more)\b|\btype\s|\bcat\s",
     "ファイルの中身を読む", False, "file_read"),
    (r"\b(Get-ChildItem|Get-Item|Test-Path)\b|\bdir\b|\bls\b",
     "ファイルの一覧や有無を調べる", False, "file_list"),
    (r"\b(Get-FileHash|certutil)\b",
     "ファイルが同一か確認する", False, "file_hash"),

    # ---------------- 調査・その他 ----------------
    (r"\b(ping|nslookup|tracert|Test-NetConnection|ipconfig)\b",
     "ネットワークの状態を確認する", False, "network"),
    (r"\b(tasklist|Get-Process|systeminfo)\b",
     "実行中のプログラムや PC の情報を調べる", False, "process"),
    (r"\b(Start-Process)\b|\bstart\s", "プログラムを起動する", False, "run"),
    (r"\b(Start-Sleep|timeout)\b", "少し待つだけ", False, "wait"),
    (r"\b(echo|Write-Host|Write-Output)\b",
     "文字を表示するだけ", False, "echo"),
]


def extract_command(detail: str) -> str:
    """承認メッセージから実際のコマンド部分だけを取り出す。"""
    if not detail:
        return ""
    m = _APPROVAL_PREFIX_RE.search(detail)
    text = detail[m.end():] if m else detail
    return text.strip(" /").strip()


def explain_command(detail: str, max_labels: int = 3) -> str:
    """コマンドを初心者にも分かる日本語に言い換える。

    判定はすべてこのアプリ内の文字列マッチで行う（通信はしない）。
    該当する操作が複数あるときは「＋」でつなぐ。
    """
    cmd = extract_command(detail)
    if not cmd:
        return "内容を読み取れませんでした"

    labels: list[str] = []
    used_families: set[str] = set()
    caution = False
    for pattern, label, danger, family in _COMMAND_RULES:
        if len(labels) >= max_labels:
            break
        if family in used_families:
            continue          # 同じ系統では最初に一致した説明だけを使う
        try:
            if re.search(pattern, cmd, re.IGNORECASE):
                used_families.add(family)
                if label not in labels:
                    labels.append(label)
                    if danger:
                        caution = True
        except re.error:
            continue

    if not labels:
        return "コマンドを実行する"
    text = " ＋ ".join(labels)
    return f"【注意】{text}" if caution else text


def summarize_approval(lines: list[str], s: dict) -> str:
    """ログと画面に出すために 1 行へまとめる。

    承認ブロックには「作業フォルダ」「コマンド」「承認メッセージ」などが
    別々の要素として並び、同じコマンドが重複して現れる。そのまま並べると
    読みにくいので整理する。

    1. 承認メッセージ本体が見つかればそれだけを使う（コマンドを含む）
    2. 見つからない場合は、他の行に丸ごと含まれている行を落としてつなぐ
    """
    if not lines:
        return ""
    limit = int(s.get("approval_detail_max_chars", 300))

    main = ""
    for n in lines:
        if _APPROVAL_PREFIX_RE.search(n):
            main = n
            break

    if main:
        text = main
    else:
        kept = [n for n in lines if not any(n != o and n in o for o in lines)]
        text = " / ".join(kept or lines)

    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def scan(uia: UiaSession, cfg: dict, check_busy: bool = False) -> ScanResult:
    """Kiro の承認 UI を探し、クリックしてよい Allow ボタンを 1 つだけ決める。

    check_busy=True のときは、あわせて「Kiro が処理中か」も判定する。
    """
    res = ScanResult()
    s = cfg["safety"]
    target_name = cfg["target_button_name"]
    never = {str(x).lower() for x in cfg.get("never_click_names", [])}
    ncfg = cfg.get("notify", {})
    busy_texts = {normalize_name(x) for x in ncfg.get("busy_text_names", [])}
    busy_buttons = {normalize_name(x) for x in ncfg.get("busy_button_names", [])}

    res.windows = find_kiro_windows(cfg)
    if not res.windows:
        return res

    target_found = False
    for win in res.windows:
        try:
            root = uia.element_from_handle(win.hwnd)
        except Exception:
            res.skip_reasons.append(f"{win.describe()} の UI 要素を取得できませんでした")
            continue
        if root is None:
            continue

        buttons = uia.find_buttons(root)

        # --- Kiro が処理中か（完全一致で判定する。チャット本文に拾われないように） ---
        if check_busy and not res.busy:
            for b in buttons:
                if b.norm_name in busy_buttons and b.enabled and not b.offscreen:
                    res.busy = True
                    res.busy_reason = f"ボタン '{b.norm_name}' が表示されています"
                    break
            if not res.busy and busy_texts:
                for t in uia.find_texts(root):
                    if t.norm_name in busy_texts and not t.offscreen:
                        res.busy = True
                        res.busy_reason = f"'{t.norm_name}' が表示されています"
                        break

        if not buttons or target_found:
            continue

        # --- 承認 UI 関連のボタンを拾う（ログ用） ---
        related = [b for b in buttons if b.norm_name.lower() in APPROVAL_NAMES_LOWER]
        for b in related:
            res.approval_buttons.append((win, b))

        # --- 「Allow」完全一致の候補 ---
        candidates = [b for b in buttons if b.norm_name == target_name]
        # 絶対にクリックしない名前は二重に排除
        candidates = [b for b in candidates if b.norm_name.lower() not in never]
        if not candidates:
            continue

        # --- 基本的な妥当性 ---
        usable = []
        for b in candidates:
            if not b.enabled:
                res.skip_reasons.append("Allow ボタンが無効状態のため対象外にしました")
                continue
            if b.offscreen:
                res.skip_reasons.append("Allow ボタンが画面外のため対象外にしました")
                continue
            if b.pid and b.pid != win.pid:
                res.skip_reasons.append("Allow ボタンの所有プロセスが Kiro と違うため対象外にしました")
                continue
            if not win.contains(b.rect):
                res.skip_reasons.append("Allow ボタンが Kiro ウィンドウの外にあるため対象外にしました")
                continue
            if not _size_ok(b, s):
                res.skip_reasons.append(
                    f"Allow ボタンの大きさが想定外（{b.width}x{b.height}）のため対象外にしました"
                )
                continue
            usable.append(b)
        if not usable:
            continue

        # --- 同じ承認ブロックに Always allow / Deny が揃っているか ---
        gtol = int(s["group_vertical_tolerance_px"])
        rtol = int(s["same_row_tolerance_px"])
        required = [str(x).lower() for x in s.get("require_sibling_buttons", [])]

        verified: list[ElemInfo] = []
        for b in usable:
            group = [
                o for o in related
                if abs(o.center_y - b.center_y) <= gtol and win.contains(o.rect)
            ]
            group_names = {o.norm_name.lower() for o in group}
            missing = [r for r in required if r not in group_names]
            if missing:
                res.skip_reasons.append(
                    "承認 UI と確認できませんでした（同じブロックに "
                    + " / ".join(missing) + " が見つかりません）"
                )
                continue
            res.approval_ui_found = True

            if s.get("require_allow_is_leftmost", True):
                same_row = [o for o in group if abs(o.center_y - b.center_y) <= rtol]
                if same_row and b.left > min(o.left for o in same_row):
                    res.skip_reasons.append(
                        "Allow が同じ行の一番左ではなかったのでクリックしませんでした"
                    )
                    continue
            verified.append(b)

        if not verified:
            continue

        # --- 候補が複数のとき ---
        if len(verified) > 1:
            mode = str(s.get("on_multiple_candidates", "skip"))
            if mode == "bottommost":
                verified.sort(key=lambda b: b.center_y)
                chosen = verified[-1]
                res.checks.append(
                    f"Allow 候補が {len(verified)} 個あったため一番下のものを選びました"
                )
            else:
                res.skip_reasons.append(
                    f"Allow 候補が {len(verified)} 個見つかり特定できないためクリックしませんでした"
                )
                continue
        else:
            chosen = verified[0]

        # --- 何を許可するのかを承認ブロックから読み取る ---
        block = [
            o for o in related
            if abs(o.center_y - chosen.center_y) <= gtol and win.contains(o.rect)
        ] or [chosen]
        res.approval_lines = approval_block_texts(uia, root, block, s)
        res.approval_detail = summarize_approval(res.approval_lines, s)
        patterns = [str(p).lower() for p in s.get("approval_text_patterns", [])]
        for n in res.approval_lines:
            if any(p in n.lower() for p in patterns):
                res.approval_text = n
                break
        if s.get("require_approval_text", False) and not res.approval_text:
            res.skip_reasons.append(
                "承認メッセージの文字列が見つからないためクリックしませんでした"
            )
            continue

        res.checks.append(f"Kiro ウィンドウ {win.describe()}")
        res.checks.append(
            f"ボタン名が完全一致（'{chosen.norm_name}'）/ ControlType={chosen.control_type_name}"
        )
        res.checks.append(f"座標 {chosen.rect}")
        res.target = (win, chosen)
        target_found = True
        if not check_busy:
            return res

    return res


# =============================================================================
#  クリック
# =============================================================================


def click_allow(uia: UiaSession, win: WindowInfo, info: ElemInfo, cfg: dict, log: LogBus) -> bool:
    """クリック直前にもう一度全部確認してから実行する。"""
    ccfg = cfg["click"]
    target_name = cfg["target_button_name"]
    never = {str(x).lower() for x in cfg.get("never_click_names", [])}

    # --- 直前の再確認（ここが最後の砦） ---
    if not user32.IsWindow(win.hwnd):
        log.warn("クリック直前に Kiro ウィンドウが消えたため中止しました")
        return False
    if os.path.basename(process_image_path(window_pid(win.hwnd))).lower() not in {
        str(x).lower() for x in cfg["kiro"]["process_names"]
    }:
        log.warn("クリック直前のプロセス確認に失敗したため中止しました")
        return False

    fresh = uia.current_info(info.element)
    if fresh.norm_name != target_name:
        log.warn(
            f"クリック直前にボタン名が変わっていました（'{fresh.norm_name}'）。中止しました"
        )
        return False
    if fresh.norm_name.lower() in never:
        log.warn("クリック禁止リストに一致したため中止しました")
        return False
    if fresh.control_type != UIA_ButtonControlTypeId:
        log.warn("対象が Button ではなくなっていたため中止しました")
        return False
    if not fresh.enabled or fresh.offscreen:
        log.warn("対象が無効／画面外になっていたため中止しました")
        return False
    if fresh.pid and fresh.pid != win.pid:
        log.warn("対象の所有プロセスが Kiro ではなくなったため中止しました")
        return False
    cur_rect = window_rect(win.hwnd)
    if not WindowInfo(win.hwnd, win.pid, win.title, win.class_name, win.exe_path, cur_rect).contains(fresh.rect):
        log.warn("対象が Kiro ウィンドウの外に出たため中止しました")
        return False

    # クリックで Kiro が前面に出てくることがあるので、元の前面ウィンドウを覚えておく
    prev_fg = foreground_window()
    keep_fg = bool(ccfg.get("keep_foreground", True)) and prev_fg != win.hwnd

    def restore_fg() -> None:
        """クリック後に元のウィンドウへ戻す。

        Kiro は実行開始のタイミングでも前面化してくるので、少し待って複数回試す。
        """
        if not keep_fg:
            return
        for delay in (0.05, 0.35, 0.9):
            time.sleep(delay)
            if foreground_window() == prev_fg:
                continue
            if restore_foreground(prev_fg):
                log.info("元のウィンドウにフォーカスを戻しました")
                return
        if foreground_window() != prev_fg:
            log.warn("元のウィンドウへフォーカスを戻せませんでした")

    def gone() -> bool:
        """押せたかどうかを実際に確認する。要素が消える／無効になれば成功。"""
        time.sleep(0.7)
        try:
            nm = normalize_name(fresh.element.CurrentName)
            off = bool(fresh.element.CurrentIsOffscreen)
            en = bool(fresh.element.CurrentIsEnabled)
        except Exception:
            return True  # 要素自体が無くなった＝押せた
        return not (nm == target_name and en and not off)

    # --- 1) Invoke（マウスを動かさない。最も安全） ---
    if ccfg.get("use_invoke", True):
        if uia.invoke(fresh.element):
            if gone():
                log.ok("Allow をクリックしました（UI Automation / Invoke）")
                restore_fg()
                return True
            log.warn("Invoke は実行できましたが承認 UI が残っています。別の方法を試します")
        else:
            log.warn("Invoke パターンを使えませんでした。別の方法を試します")

    # --- 2) LegacyIAccessible の既定アクション ---
    if ccfg.get("use_legacy_default_action", True):
        if uia.legacy_default_action(fresh.element):
            if gone():
                log.ok("Allow をクリックしました（UI Automation / DoDefaultAction）")
                restore_fg()
                return True
            log.warn("DoDefaultAction でも承認 UI が残っています。別の方法を試します")

    # --- 3) 物理クリック（最後の手段） ---
    if not ccfg.get("allow_mouse_fallback", True):
        log.warn("UI Automation でクリックできず、物理クリックは無効設定のため見送りました")
        return False
    if ccfg.get("mouse_requires_foreground", True):
        if int(user32.GetForegroundWindow()) != win.hwnd:
            log.warn(
                "UI Automation でクリックできず、Kiro が最前面でないため物理クリックを見送りました"
            )
            return False
    x, y = fresh.center
    if physical_left_click(x, y, bool(ccfg.get("restore_cursor", True))):
        if gone():
            log.ok(f"Allow をクリックしました（物理クリック {x},{y}）")
            restore_fg()
            return True
        log.error("物理クリックを実行しましたが承認 UI が残っています")
        return False
    log.error("クリックに失敗しました")
    return False


# =============================================================================
#  UI 要素ダンプ（デバッグ用）
# =============================================================================


def dump_ui(cfg: dict, log: LogBus) -> Path | None:
    """Kiro から取得できる UI 要素をファイルに書き出す。"""
    try:
        uia = UiaSession()
    except Exception as e:
        log.error(f"UI Automation を初期化できませんでした: {e}")
        return None

    wins = find_kiro_windows(cfg)
    if not wins:
        log.warn("Kiro のウィンドウが見つかりません。Kiro を起動してから実行してください")
        return None

    dcfg = cfg["dump"]
    max_depth = int(dcfg["max_depth"])
    max_nodes = int(dcfg["max_nodes"])
    lines: list[str] = []
    lines.append(f"{APP_NAME} {APP_VERSION} UI 要素ダンプ")
    lines.append(f"日時: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    for win in wins:
        lines.append("=" * 100)
        lines.append(f"ウィンドウ名   : {win.title}")
        lines.append(f"ウィンドウ handle: 0x{win.hwnd:X}")
        lines.append(f"クラス名        : {win.class_name}")
        lines.append(f"PID / 実行ファイル: {win.pid} / {win.exe_path}")
        lines.append(f"ウィンドウ座標  : {win.rect}")
        lines.append("=" * 100)

        try:
            root = uia.element_from_handle(win.hwnd)
        except Exception as e:
            lines.append(f"  UI 要素を取得できませんでした: {e}")
            continue

        # --- 1) ボタン一覧（承認 UI の確認に一番役立つ） ---
        buttons = uia.find_buttons(root)
        lines.append("")
        lines.append(f"--- Button 一覧 ({len(buttons)} 個) ---")
        for b in buttons:
            mark = ""
            low = b.norm_name.lower()
            if low in APPROVAL_NAMES_LOWER:
                mark = "  <<< 承認 UI 関連"
            if b.norm_name == cfg["target_button_name"]:
                mark = "  <<< クリック対象候補（完全一致）"
            lines.append(
                f"  Name='{b.norm_name}' ControlType={b.control_type_name} "
                f"AutomationId='{b.automation_id}' Class='{b.class_name}' "
                f"Rect={b.rect} Enabled={b.enabled} Offscreen={b.offscreen} "
                f"Invokable={b.invokable} Pid={b.pid}{mark}"
            )

        # --- 2) 承認メッセージらしいテキスト ---
        patterns = [str(p).lower() for p in cfg["safety"]["approval_text_patterns"]]
        texts = uia.find_texts(root)
        hits = [t for t in texts if t.norm_name and any(p in t.norm_name.lower() for p in patterns)]
        lines.append("")
        lines.append(f"--- 承認メッセージ候補 ({len(hits)} 件 / Text 要素 {len(texts)} 個) ---")
        for t in hits[:20]:
            lines.append(f"  '{t.norm_name}' Rect={t.rect}")

        # --- 3) ツリー全体 ---
        lines.append("")
        lines.append(f"--- UI ツリー (最大深さ {max_depth} / 最大 {max_nodes} ノード) ---")
        count = 0

        def walk(el, depth: int, parent_desc: str) -> None:
            nonlocal count
            if el is None or depth > max_depth or count >= max_nodes:
                return
            info = uia.current_info(el)
            count += 1
            pad = "  " * depth
            lines.append(
                f"{pad}[{depth}] {info.control_type_name} Name='{info.norm_name}' "
                f"AutomationId='{info.automation_id}' Class='{info.class_name}' "
                f"Rect={info.rect} Enabled={info.enabled} Offscreen={info.offscreen}"
            )
            lines.append(f"{pad}     親: {parent_desc}")
            me = f"{info.control_type_name}'{info.norm_name}'"
            child = uia.first_child(el)
            while child is not None and count < max_nodes:
                walk(child, depth + 1, me)
                child = uia.next_sibling(child)

        try:
            walk(root, 0, "(なし＝ウィンドウ自身)")
        except Exception as e:
            lines.append(f"  ツリー走査中にエラー: {e}")
        lines.append(f"--- ノード数 {count} ---")

    path = dumps_dir() / f"ui_dump_{datetime.now():%Y%m%d_%H%M%S}.txt"
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        log.error(f"ダンプの保存に失敗しました: {e}")
        return None

    log.ok(f"UI 要素を書き出しました: {path}")
    if dcfg.get("open_after_dump", True):
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception:
            pass
    return path


# =============================================================================
#  監視スレッド
# =============================================================================


class AppState:
    def __init__(self, cfg: dict) -> None:
        self.auto_allow = bool(cfg["ui"]["auto_allow_default"])
        self.notify_enabled = bool(cfg["notify"]["enabled"])
        self.kiro_busy = False
        self.ready_pending = False   # 「送信できます」を GUI に出しているか
        self.click_count = 0
        self.detect_count = 0
        self.kiro_found = False
        self.last_error = ""


class Monitor(threading.Thread):
    daemon = True

    def __init__(self, cfg: dict, state: AppState, log: LogBus,
                 on_emergency_stop, on_ready=None) -> None:
        super().__init__(name="KiroAutoAllowMonitor", daemon=True)
        self.cfg = cfg
        self.state = state
        self.log = log
        self.on_emergency_stop = on_emergency_stop
        self.on_ready = on_ready
        self._seen_busy = False
        self._idle_since: float | None = None
        self._notified = True   # 起動直後にいきなり通知しない
        self._stop = threading.Event()
        self._clicks = deque(maxlen=120)
        self._clicked_keys: dict[str, float] = {}
        self._cooldown_until = 0.0
        self._last_kiro_found: bool | None = None
        self._last_approval_key = ""
        self._no_click_logged: set[str] = set()

    def stop(self) -> None:
        self._stop.set()

    def reset_dedupe(self) -> None:
        """ON/OFF 切り替え時に「処理済み」の記録を捨てる。

        これをしないと、OFF 中に出ていた承認画面を
        ON にした直後にクリックできない。
        """
        self._clicked_keys.clear()
        self._no_click_logged.clear()
        self._last_approval_key = ""
        self._cooldown_until = 0.0

    # -------------------------------------------------- 送信可能になったら通知
    def _update_ready(self, res: ScanResult) -> None:
        """Kiro の処理中表示が消えて落ち着いたら 1 回だけ通知する。"""
        ncfg = self.cfg["notify"]
        idle_need = float(ncfg.get("ready_idle_sec", 2.0))
        now = time.monotonic()
        self.state.kiro_busy = res.busy

        if res.busy:
            if not self._seen_busy:
                self.log.info(f"Kiro が処理中です（{res.busy_reason}）")
            self._seen_busy = True
            self._idle_since = None
            self._notified = False
            if self.state.ready_pending:
                self.state.ready_pending = False
            return

        # 承認待ちが残っているあいだは「完了」にしない
        if res.target is not None or res.approval_buttons:
            self._idle_since = None
            return

        if self._idle_since is None:
            self._idle_since = now
        if self._notified or not self._seen_busy:
            return
        if now - self._idle_since < idle_need:
            return

        self._notified = True
        self._seen_busy = False
        self.state.ready_pending = True
        self.log.ok("処理が完了しました。次のプロンプトを送信できます")
        if self.on_ready:
            try:
                self.on_ready()
            except Exception:
                pass

    # ------------------------------------------------------------------ 本体
    def run(self) -> None:
        try:
            uia = UiaSession()
        except Exception as e:
            self.log.error(f"UI Automation を初期化できませんでした: {e}")
            self.state.last_error = str(e)
            return

        esc = DoubleEscWatcher(int(self.cfg["emergency"]["double_esc_window_ms"]))
        use_esc = bool(self.cfg["emergency"]["double_esc"])
        interval = float(self.cfg["poll_interval_sec"])
        next_scan = 0.0

        while not self._stop.is_set():
            # 緊急停止の監視は ON/OFF に関係なく回す
            if use_esc and esc.poll() and self.state.auto_allow:
                self.log.warn("Esc 2 回押しを検知したため緊急停止しました")
                self.on_emergency_stop()

            # Auto Allow と通知のどちらかが ON なら監視する
            if not (self.state.auto_allow or self.state.notify_enabled):
                self._last_kiro_found = None
                self.state.kiro_busy = False
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if now < next_scan or now < self._cooldown_until:
                time.sleep(0.05)
                continue
            next_scan = now + interval

            try:
                self._tick(uia)
            except Exception as e:
                self.log.error(f"監視中にエラーが発生しました: {e}")
                time.sleep(1.0)

    def _tick(self, uia: UiaSession) -> None:
        notify_on = self.state.notify_enabled
        res = scan(uia, self.cfg, check_busy=notify_on)

        # --- Kiro の検出状況 ---
        found = bool(res.windows)
        self.state.kiro_found = found
        if found != self._last_kiro_found:
            self._last_kiro_found = found
            if found:
                self.log.info(
                    "Kiro を検出しました: "
                    + " / ".join(w.describe() for w in res.windows[:3])
                )
            else:
                self.log.warn("Kiro のウィンドウが見つかりません")
        if not found:
            return

        # --- 「次のプロンプトを送れる状態」の判定 ---
        if notify_on:
            self._update_ready(res)

        # --- 承認 UI の処理は Auto Allow が ON のときだけ ---
        if not self.state.auto_allow:
            return

        # --- 承認 UI 関連ボタンのログ（同じ画面で繰り返し出さない） ---
        if res.approval_buttons:
            key = "|".join(
                sorted(f"{b.norm_name}@{b.rect}" for _, b in res.approval_buttons)
            )
            if key != self._last_approval_key:
                self._last_approval_key = key
                self.log.detect("承認画面を検出しました")
                if res.approval_detail:
                    self.log.info(
                        f"許可を求められた内容: {explain_command(res.approval_detail)}"
                    )
                    self.log.info(f"　コマンド: {res.approval_detail}")
                for _w, b in res.approval_buttons:
                    self.log.info(
                        f"検出したボタン：{b.norm_name}"
                        f"（ControlType={b.control_type_name} Rect={b.rect}）"
                    )
        else:
            self._last_approval_key = ""

        # --- クリック対象 ---
        if res.target is None:
            if res.skip_reasons:
                for reason in dict.fromkeys(res.skip_reasons):
                    if reason not in self._no_click_logged:
                        self._no_click_logged.add(reason)
                        self.log.warn(f"検出しましたがクリックしませんでした: {reason}")
            return

        self._no_click_logged.clear()
        win, btn = res.target

        # --- 同じボタンを二重に処理しない（ログも出さない） ---
        k = btn.key()
        now = time.monotonic()
        self._clicked_keys = {
            kk: tt for kk, tt in self._clicked_keys.items() if now - tt < 30.0
        }
        if k in self._clicked_keys:
            return

        self.state.detect_count += 1
        self.log.detect(f"Allow ボタンを検出しました（{btn.rect}）")
        for c in res.checks:
            self.log.info(f"確認: {c}")

        # --- 暴走検知 ---
        limit = int(self.cfg["max_clicks_per_minute"])
        recent = [t for t in self._clicks if now - t < 60.0]
        if len(recent) >= limit:
            self.log.error(
                f"1 分間に {limit} 回を超えるクリックを検知したため安全のため停止しました"
            )
            self.on_emergency_stop()
            return

        self.log.ok("クリック対象確認 OK")
        if click_allow(uia, win, btn, self.cfg, self.log):
            self.state.click_count += 1
            self._clicks.append(now)
            self._clicked_keys[k] = now
            detail = res.approval_detail
            self.log.allowed(explain_command(detail), detail)
        self._cooldown_until = time.monotonic() + float(self.cfg["post_click_cooldown_sec"])


# =============================================================================
#  GUI
# =============================================================================

LEVEL_COLORS = {
    INFO: "#d4d4d4",
    OK: "#4ec9b0",
    WARN: "#dcdcaa",
    ERROR: "#f48771",
    DETECT: "#569cd6",
}


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.log = LogBus()
        self.state = AppState(self.cfg)

        self._hwnd = 0
        self._topmost_job = None

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("760x560")
        self.root.minsize(660, 460)
        if self.cfg["ui"].get("always_on_top"):
            self.root.attributes("-topmost", True)

        try:
            dpi = self.root.winfo_fpixels("1i")
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.monitor = Monitor(
            self.cfg, self.state, self.log, self._emergency_stop, self._on_ready
        )
        self.monitor.start()

        self.log.info(f"{APP_NAME} {APP_VERSION} を起動しました")
        self.log.info(f"設定ファイル: {config_path()}")
        self.log.info(
            "クリック対象は Kiro の承認 UI 内で名前が完全一致する "
            f"'{self.cfg['target_button_name']}' だけです"
        )
        self.log.info("Always allow / Deny / Always deny / Cancel はクリックしません")
        self.log.info("[Auto Allow: ON] にすると監視を開始します")

        self._pump()

    # ------------------------------------------------------------------ 画面
    def _build(self) -> None:
        bg = "#1e1e1e"
        self.root.configure(bg=bg)

        head = tk.Frame(self.root, bg=bg)
        head.pack(fill="x", padx=12, pady=(12, 6))

        self.btn_auto = tk.Button(
            head, text="Auto Allow: OFF", width=20, height=2,
            font=("Segoe UI", 12, "bold"), relief="raised",
            command=self._toggle_auto,
        )
        self.btn_auto.pack(side="left")



        self.btn_notify = tk.Button(
            head, text="完了通知: ON", width=18, height=2,
            font=("Segoe UI", 12, "bold"), relief="raised",
            command=self._toggle_notify,
        )
        self.btn_notify.pack(side="left", padx=(10, 0))

        self.btn_stop = tk.Button(
            head, text="緊急停止", width=12, height=2,
            font=("Segoe UI", 12, "bold"), bg="#8b1a1a", fg="white",
            activebackground="#a52020", activeforeground="white",
            command=self._emergency_stop_from_gui,
        )
        self.btn_stop.pack(side="right")

        info = tk.Frame(self.root, bg=bg)
        info.pack(fill="x", padx=12, pady=(0, 6))

        self.lbl_state = tk.Label(
            info, text="", bg=bg, fg="#d4d4d4", font=("Segoe UI", 10), anchor="w",
            justify="left",
        )
        self.lbl_state.pack(side="left")

        self.lbl_ready = tk.Label(
            self.root, text="", bg="#1e1e1e", fg="#1e1e1e",
            font=("Segoe UI", 13, "bold"), anchor="center",
        )
        self.lbl_ready.pack(fill="x", padx=12, pady=(0, 4))
        self.lbl_ready.bind("<Button-1>", lambda _e: self._clear_ready())

        allowf = tk.LabelFrame(
            self.root, text=" Allow した内容（新しいものが上） ", bg=bg,
            fg="#9cdcfe", font=("Segoe UI", 9),
        )
        allowf.pack(fill="x", padx=12, pady=(0, 6))
        self.txt_allowed = tk.Text(
            allowf, height=7, bg="#101820", fg="#9cdcfe",
            insertbackground="#9cdcfe", font=("Consolas", 9), wrap="word",
            state="disabled", borderwidth=0,
        )
        self.txt_allowed.pack(fill="x", padx=4, pady=4)
        # 1 行目（要約）と 2 行目（実際のコマンド）を色で区別する
        self.txt_allowed.tag_configure("allow_sum", foreground="#9cdcfe")
        self.txt_allowed.tag_configure("allow_warn", foreground="#f0a45a")
        self.txt_allowed.tag_configure("allow_raw", foreground="#6a8ba0")

        tools = tk.Frame(self.root, bg=bg)
        tools.pack(fill="x", padx=12, pady=(0, 6))
        tk.Button(tools, text="UI要素を確認", command=self._dump_ui).pack(side="left")

        logf = tk.Frame(self.root, bg=bg)
        logf.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.txt = tk.Text(
            logf, bg="#141414", fg="#d4d4d4", insertbackground="#d4d4d4",
            font=("Consolas", 10), wrap="word", state="disabled", borderwidth=0,
        )
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for lv, col in LEVEL_COLORS.items():
            self.txt.tag_configure(lv, foreground=col)

        tk.Label(
            self.root,
            text="緊急停止: 画面の[緊急停止]ボタン、または Esc を素早く2回押す",
            bg=bg, fg="#888888", font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))

        self._refresh_labels()

    # ------------------------------------------------------------------ 操作
    def _toggle_auto(self) -> None:
        self.state.auto_allow = not self.state.auto_allow
        self.monitor.reset_dedupe()
        if self.state.auto_allow:
            self.log.ok("Auto Allow を開始しました")
        else:
            self.log.info("Auto Allow を停止しました")
        self._refresh_labels()

    def _clear_ready(self) -> None:
        """緑帯をクリックしたら通知表示と一時的な最前面を解除する。"""
        self.state.ready_pending = False
        if self._topmost_job is not None:
            try:
                self.root.after_cancel(self._topmost_job)
            except Exception:
                pass
            self._topmost_job = None
        self._release_topmost()
        self._refresh_labels()

    def _toggle_notify(self) -> None:
        self.state.notify_enabled = not self.state.notify_enabled
        if self.state.notify_enabled:
            self.log.info("完了通知を ON にしました")
        else:
            self.log.info("完了通知を OFF にしました")
            self.state.ready_pending = False
        self._refresh_labels()

    def _top_hwnd(self) -> int:
        """自分のトップレベルウィンドウのハンドルを返す。

        winfo_id() は 'TkChild' という子ウィンドウを返すため、
        そのまま使うと最前面化もタスクバー点滅も効かない。
        ウィンドウが実体化してから親をたどって 'TkTopLevel' を得る。
        """
        if self._hwnd and user32.IsWindow(self._hwnd):
            return self._hwnd
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        h = 0
        try:
            h = int(self.root.wm_frame(), 16)
        except Exception:
            h = 0
        if not h:
            try:
                h = int(self.root.winfo_id())
            except Exception:
                return 0
        h = root_hwnd(h)
        # トップレベル（親を持たない）と確認できたときだけ覚える
        try:
            if h and not user32.GetParent(h):
                self._hwnd = h
        except Exception:
            pass
        return h

    def _on_ready(self) -> None:
        """Kiro の処理が終わったときに呼ばれる（監視スレッドから）。"""
        ncfg = self.cfg["notify"]
        if ncfg.get("sound", True):
            notify_beep()
        if ncfg.get("flash_taskbar", True):
            flash_window(self._top_hwnd())
        # GUI 操作は必ず GUI スレッドで行う
        self.root.after(0, self._refresh_labels)
        if ncfg.get("bring_to_front", True):
            self.root.after(0, self._bring_to_front)

    def _bring_to_front(self) -> None:
        """完了通知のときだけ最前面に出す。常時固定はしない。"""
        ncfg = self.cfg["notify"]
        hwnd = self._top_hwnd()
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
        except Exception:
            pass
        restore_if_minimized(hwnd)
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except Exception:
            pass
        if ncfg.get("focus_window", False):
            # Windows はフォーカスの横取りを制限しているので失敗しても続行する。
            # -topmost が効いているので画面上は最前面に出ている。
            restore_foreground(hwnd)
        # 一定時間後に最前面を解除する
        if self._topmost_job is not None:
            try:
                self.root.after_cancel(self._topmost_job)
            except Exception:
                pass
        secs = max(0.5, float(ncfg.get("front_seconds", 5.0)))
        self._topmost_job = self.root.after(
            int(secs * 1000), self._release_topmost
        )

    def _release_topmost(self) -> None:
        """一時的な最前面表示を解除する。"""
        self._topmost_job = None
        if self.cfg["ui"].get("always_on_top"):
            return  # 常時最前面設定のときは触らない
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _emergency_stop_from_gui(self) -> None:
        if self.state.auto_allow:
            self.log.warn("緊急停止しました")
        self.state.auto_allow = False
        self._refresh_labels()

    def _emergency_stop(self) -> None:
        self.state.auto_allow = False
        self.root.after(0, self._refresh_labels)

    def _dump_ui(self) -> None:
        self.log.info("UI 要素を取得します…")
        threading.Thread(
            target=lambda: dump_ui(self.cfg, self.log), daemon=True
        ).start()

    def _on_close(self) -> None:
        self.state.auto_allow = False
        self.monitor.stop()
        self.root.after(120, self.root.destroy)

    # ------------------------------------------------------------------ 表示
    def _refresh_labels(self) -> None:
        on = self.state.auto_allow
        self.btn_auto.configure(
            text=f"Auto Allow: {'ON' if on else 'OFF'}",
            bg="#0e639c" if on else "#3a3a3a",
            fg="white",
            activebackground="#1177bb" if on else "#4a4a4a",
            activeforeground="white",
        )
        notify = self.state.notify_enabled
        self.btn_notify.configure(
            text=f"完了通知: {'ON' if notify else 'OFF'}",
            bg="#2d7d46" if notify else "#3a3a3a",
            fg="white",
            activebackground="#379553" if notify else "#4a4a4a",
            activeforeground="white",
        )
        if on:
            status = "現在の状態: 監視中（Allow を自動クリックします）"
        elif notify:
            status = "現在の状態: 自動クリックは停止中（完了通知のみ監視）"
        else:
            status = "現在の状態: 停止中（何もしません）"
        kiro = "検出中" if self.state.kiro_found else "未検出"
        if notify and self.state.kiro_found:
            kiro += "／処理中" if self.state.kiro_busy else "／待機中"
        self.lbl_state.configure(
            text=(
                f"{status}\n"
                f"Kiro: {kiro}    Allow クリック回数: {self.state.click_count}    "
                f"Allow 検出回数: {self.state.detect_count}"
            )
        )
        if self.state.ready_pending:
            self.lbl_ready.configure(
                text="✓ 処理が完了しました。次のプロンプトを送信できます",
                bg="#2d7d46", fg="white",
            )
        else:
            self.lbl_ready.configure(text="", bg="#1e1e1e", fg="#1e1e1e")

    def _pump(self) -> None:
        allowed = self.log.drain_allowed()
        if allowed:
            show_raw = bool(self.cfg["ui"].get("show_raw_command", True))
            self.txt_allowed.configure(state="normal")
            for stamp, summary, detail in allowed:
                # 1 行目に分かりやすい要約、2 行目に実際のコマンドを出す。
                # 新しいものを上にするため、2 行まとめて先頭へ挿入する。
                block = f"{stamp}  {summary}\n"
                raw_line = ""
                if show_raw and detail:
                    raw_line = f"          {detail}\n"
                    block += raw_line
                self.txt_allowed.insert("1.0", block)
                tag = "allow_warn" if summary.startswith("【注意】") else "allow_sum"
                self.txt_allowed.tag_add(tag, "1.0", "1.end")
                if raw_line:
                    self.txt_allowed.tag_add("allow_raw", "2.0", "2.end")
            per = 2 if show_raw else 1
            keep = max(1, int(self.cfg["ui"].get("max_allowed_items", 30))) * per
            total = int(self.txt_allowed.index("end-1c").split(".")[0])
            if total > keep:
                self.txt_allowed.delete(f"{keep + 1}.0", "end")
            self.txt_allowed.configure(state="disabled")

        rows = self.log.drain()
        if rows:
            self.txt.configure(state="normal")
            for stamp, msg, level in rows:
                self.txt.insert("end", f"{stamp} {msg}\n", level)
            limit = int(self.cfg["ui"]["max_log_lines"])
            total = int(self.txt.index("end-1c").split(".")[0])
            if total > limit:
                self.txt.delete("1.0", f"{total - limit}.0")
            self.txt.configure(state="disabled")
            self.txt.see("end")
        self._refresh_labels()
        self.root.after(150, self._pump)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    enable_dpi_awareness()
    try:
        return App().run()
    except Exception as e:
        try:
            import tkinter.messagebox as mb

            mb.showerror(APP_NAME, f"起動に失敗しました:\n{e}")
        except Exception:
            print(f"起動に失敗しました: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
