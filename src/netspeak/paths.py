# -*- coding: utf-8 -*-
"""Рабочие директории portable-приложения.

В собранном виде (PyInstaller) корень — папка рядом с NetSpeak.exe.
При запуске из исходников — корень репозитория (папка уровнем выше src/).

Пользователь ничего не устанавливает: он кладёт exe куда угодно, и там же
при первом запуске появляются settings/, user/, logs/ и data/.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Каталоги, которые создаются при первом запуске.
DIRS = ("settings", "user", "logs", "data", "history")


def is_frozen() -> bool:
    """Запущено ли собранное приложение (внутри PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def root() -> Path:
    """Корень portable-приложения: папка, куда положили exe."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/netspeak/paths.py -> src/netspeak -> src -> корень
    return Path(__file__).resolve().parent.parent.parent


def ensure_dirs(base: Path | None = None) -> Path:
    """Создаёт рабочие директории, если их ещё нет. Возвращает корень."""
    base = base or root()
    for name in DIRS:
        (base / name).mkdir(parents=True, exist_ok=True)
    (base / "user" / "avatars").mkdir(parents=True, exist_ok=True)
    (base / "data" / "files").mkdir(parents=True, exist_ok=True)
    return base


def settings_dir() -> Path:
    return root() / "settings"


def user_dir() -> Path:
    return root() / "user"


def logs_dir() -> Path:
    return root() / "logs"


def history_dir() -> Path:
    """Переписка по каналам. Лежит рядом с exe, переносится вместе с ним."""
    return root() / "history"


def webview_dir(kind: str = "") -> Path | None:
    """Папка профиля окна WebView2 или None, если её некуда класть.

    pywebview по умолчанию открывает окно в приватном режиме, где
    localStorage не сохраняется: постоянный идентификатор человека
    обнулялся бы при каждом запуске, и прошлые сообщения снова
    становились бы чужими. Поэтому хранилище задаём сами и кладём рядом
    с exe — заодно оно переезжает вместе с программой.

    kind разделяет клиента и сервер, чтобы они не делили одно хранилище.
    None означает «создать не вышло»: программа должна запуститься и без
    него, лишь без переживающего перезапуск идентификатора.
    """
    name = f"webview-{kind}" if kind else "webview"
    try:
        path = root() / "data" / name
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return None


def data_dir() -> Path:
    return root() / "data"


def avatars_dir() -> Path:
    return user_dir() / "avatars"


def web_dir() -> Path:
    """Папка с интерфейсом.

    Сначала ищем рядом с exe (чтобы можно было править CSS/JS без пересборки),
    и только потом — внутри собранного пакета.
    """
    if is_frozen():
        beside = root() / "web"
        if beside.is_dir():
            return beside
        return Path(getattr(sys, "_MEIPASS", root())) / "web"
    return Path(__file__).resolve().parent.parent / "web"


def describe() -> dict:
    """Диагностика для окна лога и для проверки прав."""
    out = {"root": str(root()), "frozen": is_frozen(), "dirs": {}}
    for name in DIRS:
        p = root() / name
        out["dirs"][name] = {"path": str(p), "exists": p.is_dir()}
    return out