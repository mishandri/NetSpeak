# -*- coding: utf-8 -*-
"""Кто это устройство.

Задача простая: дать окну приложения число, которое на этом компьютере
одно, а на другом — другое. Именно этого не хватало: идентификатор
человека лежал в хранилище браузера, а папку NetSpeak можно спокойно
скопировать на ноутбук или положить в общую папку — и вот человек уже
один и тот же на двух машинах, с одним именем и одним аватаром.

Откуда берём число
------------------
Windows держит в реестре MachineGuid, Linux — файл /etc/machine-id.
Оба переживают перезагрузку, переустановку программ и перенос папки.
Дальше по убыванию надёжности:
серийный номер тома, сетевой адрес карты, имя компьютера. Если всё
отвалилось — возвращаем пустую строку, и тогда приложение работает как
раньше, на хранилище браузера.

Это не надо и не надо угадывать: значение только смешивается с зёрнышем
и хешируется. Само по себе оно нигде не показывается и никуда не уходит.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import socket
import uuid
from pathlib import Path

from . import paths, store


# ── источники ──────────────────────────────────────────────────────────────

def _machine_guid() -> str:
    """MachineGuid из реестра Windows."""
    try:
        import winreg
    except ImportError:
        return ""
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography\MachineGuid"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value)
        except OSError:
            continue
    return ""


def _machine_id_file() -> str:
    """machine-id Linux — то же, чем для Windows является MachineGuid.

    Системный файл, переживает переустановку программ и перенос папки, а
    потому годится на ту же роль, что и реестр.
    """
    if os.name == "nt":
        return ""
    for путь in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            значение = Path(путь).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        # 32 шестнадцатеричных символа — так выглядит machine-id
        if re.fullmatch(r"[0-9a-f]{32}", значение):
            return значение
    return ""


def _volume_serial() -> str:
    """Серийный номер системного тома."""
    try:
        buf = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint32(0)
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p("C:\\"), buf, 261,
            ctypes.byref(serial), None, None, None, 0)
        return str(serial.value)
    except (AttributeError, OSError):
        return ""


def _node_id() -> str:
    """Аппаратный адрес сетевой карты."""
    node = uuid.getnode()
    # 0x0240… и 0xFE… — заглушки, когда карту найти не удалось
    if node in (0, 0x02406E440016):  # noqa: E741
        return ""
    return f"{node:012x}"


_memo: str | None = None


def machine_id() -> str:
    """Устройство, в котором мы работаем. Одна строка, всегда одна и та же.

    Значение пересчитывается только при первом обращении: читать реестр
    на каждый запрос незачем, а тем более запускать что-то.
    """
    global _memo
    if _memo is not None:
        return _memo
    parts = [_machine_guid(), _machine_id_file(), _volume_serial(), _node_id()]
    parts = [p for p in parts if p]
    if parts:
        raw = "|".join(parts)
        _memo = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    else:
        _memo = ""
    return _memo


# ── зёрныш установки ───────────────────────────────────────────────────────

def _device_file() -> Path:
    return paths.user_dir() / "device.json"


def device_seed() -> str:
    """Своё случайное число для этой папки программы.

    Нужен рядом с машинкой: если на одном компьютере запустят две копии
    (или программу переместят на сетевой диск), это число их различит.
    Хранится рядом с exe — переносится вместе с папкой, и потому у
    нового компьютера своё.
    """
    path = _device_file()
    try:
        # именно {}: без подсказки по умолчанию load_json возвращает None
        # даже для вполне читаемого файла — проверка isinstance(data, dict)
        data = store.load_json(path, {})
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    seed = str(data.get("seed") or "")
    if re.fullmatch(r"[0-9a-f]{16}", seed):
        return seed
    seed = uuid.uuid4().hex[:16]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.save_json(path, {**data, "seed": seed})
    except OSError:
        pass          # без зёрныша работаем, просто на машинке
    return seed


def _uid(machine: str, seed: str) -> str:
    """Номер человека на этой машине.

    Считается из номера компьютера и зёрныша — без всякой случайности.
    Это главное: если в нём участвует случайное число, то потеря папки
    настроек браузера сделала бы нового человека, и он потерял бы своё
    имя и аватар. А так номер выводится из устройства и восстанавливается
    сам, а имя и аватар ему вернёт сервер из базы по этому номеру.
    """
    if not machine:
        return ""
    raw = f"{machine}:{seed}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def identity() -> dict:
    """Всё, что нужно окну приложения: устройство, зёрныше и номер."""
    machine = machine_id()
    seed = device_seed()
    return {
        "machine": machine,
        "seed": seed,
        # устройство одно и то же само с собой? тогда это «наш» компьютер
        "bound": bool(machine),
        "uid": _uid(machine, seed),
        "name": _friendly_name(),
    }


_NAME_CACHE: str | None = None


def _friendly_name() -> str:
    """Название устройства — чтобы в списке людей было видно, кто где."""
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    _NAME_CACHE = name.split(".")[0].strip() or "Компьютер"
    return _NAME_CACHE