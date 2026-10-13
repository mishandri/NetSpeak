# -*- coding: utf-8 -*-
"""Что хранится в user/, а что — в settings/.

Пользовательское (user/profile.json) — кто вы:
    имя, аватар, показывать ли цветной кружок.
Это переносимо между машинами: скопировали папку user — получили себя.

Настройки приложения (settings/app.json) — как программа работает на ЭТОМ
компьютере:
    тема, микрофон/динамики по умолчанию, параметры звука, клавиши,
    последний адрес сервера, порт.
Это привязано к железу, поэтому при смене компьютера не переносится.
"""

from __future__ import annotations

from pathlib import Path

from . import paths, store

# ── пользователь ──────────────────────────────────────────────────────────
USER_DEFAULT = {
    "name": "",
    "avatar": "",        # имя файла в user/avatars/; пусто — рисуем кружок
    "showCircle": True,  # показывать цветной кружок с первой буквой имени
}

# ── приложение ────────────────────────────────────────────────────────────
APP_DEFAULT = {
    "theme": "dark",
    "mode": "on",                  # по умолчанию «Всегда»
    "key": "Space",
    "hotkey": "ControlRight+Insert",
    "mic": "",
    "speaker": "",
    "gain": 100,
    "highpass": 70,
    "gate": 6,
    "leveling": 40,
    "echo": True,
    "noise": True,
    "agc": True,
    "lastServer": {"host": "", "port": 8766},
    "defaultPort": 8766,
    "openBrowser": True,
}

#: Ключи, которые клиент имеет право прислать. Всё остальное игнорируем,
#: чтобы случайный запрос не записал в файл мусор.
USER_KEYS = set(USER_DEFAULT)
APP_KEYS = set(APP_DEFAULT)


def profile_path() -> Path:
    return paths.user_dir() / "profile.json"


def settings_path() -> Path:
    return paths.settings_dir() / "app.json"


def load_user() -> dict:
    data = store.load_json(profile_path(), dict(USER_DEFAULT))
    merged = dict(USER_DEFAULT)
    for key in USER_KEYS:
        if key in data:
            merged[key] = data[key]
    return merged


def save_user(patch: dict) -> dict:
    """Меняет только пользовательские поля, остальное не трогает."""
    clean = {k: v for k, v in (patch or {}).items() if k in USER_KEYS}
    data = load_user()
    data.update(clean)
    store.save_json(profile_path(), data)
    return data


def load_app() -> dict:
    data = store.load_json(settings_path(), dict(APP_DEFAULT))
    merged = dict(APP_DEFAULT)
    for key in APP_KEYS:
        if key in data:
            merged[key] = data[key]
    if not isinstance(merged.get("lastServer"), dict):
        merged["lastServer"] = dict(APP_DEFAULT["lastServer"])
    return merged


def save_app(patch: dict) -> dict:
    clean = {k: v for k, v in (patch or {}).items() if k in APP_KEYS}
    data = load_app()
    data.update(clean)
    store.save_json(settings_path(), data)
    return data


def avatar_file(name: str) -> Path:
    """Путь файла аватара. Имя приводим к безопасному виду, чтобы не выйти
    за пределы папки через «..»."""
    safe = "".join(ch for ch in (name or "") if ch.isalnum() or ch in "._-")
    return paths.avatars_dir() / safe


def avatar_name(uid: str, ext: str = ".png") -> str:
    """Имя файла аватара по постоянному id человека.

    Раньше имя выбирал клиент, и на одном сервере двое легко могли
    назвать картинку одинаково — тогда один аватар молча затирал другого,
    и все видели не ту картинку. Теперь имя выводится из uid: оно у
    человека своё и не совпадёт ни с чьим.
    """
    safe = "".join(ch for ch in (uid or "") if ch.isalnum() or ch in "._-")
    if not safe:
        return ""
    if not ext.startswith("."):
        ext = ".png"
    return safe + ext


def avatar_owner(name: str) -> str:
    """Кому принадлежит файл аватара — uid, если он назван по uid.

    Имя, полученное по постоянному id, — это 16 шестнадцатеричных цифр.
    Старое (uuid, 12 цифр) владельца не имеет: оно остаётся «ничьим» и
    достаётся тому, кто загружал последним, — как и раньше.
    """
    stem = Path(name or "").stem
    if len(stem) == 16 and all(c in "0123456789abcdefABCDEF" for c in stem):
        return stem
    return ""


def clear_avatars() -> None:
    d = paths.avatars_dir()
    if not d.is_dir():
        return
    for f in d.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass


def ensure_files() -> None:
    """Создаёт файлы настроек при первом запуске.

    Так пользователь сразу видит, что настройки — обычный текст в обычной
    папке: можно открыть блокнотом и поправить руками.
    """
    if not profile_path().exists():
        store.save_json(profile_path(), dict(USER_DEFAULT))
    if not settings_path().exists():
        store.save_json(settings_path(), dict(APP_DEFAULT))