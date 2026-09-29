"""設定・ログ・ダンプの保存先。

exe をどこに置いても書き込みに失敗しないよう、既定では
%LOCALAPPDATA%\\KiroAutoAllow 配下を使う。
"""

from __future__ import annotations

import os
from pathlib import Path


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
