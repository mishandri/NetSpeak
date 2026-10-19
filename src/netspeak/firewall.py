# -*- coding: utf-8 -*-
"""Правила брандмауэра для голоса.

Только для Windows: правила ставит netsh, которого на других системах нет.

Голос идёт напрямую между компьютерами, минуя сервер: браузер спрашивает у
собеседника напрямую, по UDP. Если брандмауэр Windows не пускает входящие
пакеты, связи не будет совсем — а выглядит это так же, как «звука нет»,
хотя всё остальное работает.

Поэтому здесь ровно одна функция: создать для обоих файлов разрешающие
правила. Правила добавляются молча, ничего не перенастраивая, и только по
желанию — по кнопке в окне сервера.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RULE_NAMES = ("NetSpeak — входящие подключения",
              "NetSpeak Server — входящие подключения")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Правила ставит netsh, а он есть только в Windows. Под Linux то же самое
#: делает ufw, но он требует sudo из окна — это отдельный разговор про
#: права, и молча тянуть его сюда нельзя. Поэтому на других системах
#: модуль честно говорит «не поддерживается», а окно прячет кнопку.
SUPPORTED = hasattr(subprocess, "CREATE_NO_WINDOW")


def exes() -> list[Path]:
    """Какие файлы разрешаем: оба, клиент и сервер.

    В собранной программе — два файла рядом. При запуске из исходников exe
    ещё нет, и разрешать тогда нечего: правило на интерпретатор Python
    бесполезно, он всё равно не переживёт переустановку.
    """
    from . import paths
    if not paths.is_frozen():
        return []
    here = Path(sys.executable).resolve().parent
    out: list[Path] = []
    # расширение есть только у файлов Windows; на Linux собирается без него
    суффикс = ".exe" if os.name == "nt" else ""
    for name in (f"NetSpeak{sufфикс}", f"NetSpeakServer{sufфикс}"):
        path = here / name
        if path.is_file():
            out.append(path)
    return out


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=25,
                           creationflags=_NO_WINDOW)
        return r.returncode, (r.stderr or b"").decode("utf-8", "replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def has_rules() -> bool:
    """Есть ли уже наши правила — чтобы не добавлять их по десять раз."""
    code, out = _run(["netsh", "advfirewall", "firewall", "show", "rule",
                      "name=all"])
    if code != 0:
        return False
    return all(name in out for name in RULE_NAMES)


def allow_incoming() -> dict:
    """Разрешить входящие подключения для обоих файлов.

    Возвращает разбор: что сделано, что не вышло и что делать дальше.
    """
    if not SUPPORTED:
        return {"ok": False, "added": [], "error": "только для Windows"}

    if not SUPPORTED:
        return {"ok": False, "added": [], "error": "только для Windows"}

    if not is_admin():
        return {"ok": False, "added": [], "needsAdmin": True,
                "error": "нужны права администратора"}

    targets = exes()
    if not targets:
        return {"ok": False, "added": [],
                "error": "правило добавляется только для собранной программы "
                         "(NetSpeakServer.exe)"}

    added, failed = [], []
    for rule_name, path in zip(RULE_NAMES, targets):
        # сначала убираем прежнее правило с тем же именем — иначе правил
        # накапливается по одному на каждый запуск
        _run(["netsh", "advfirewall", "firewall", "delete", "rule",
              f"name={rule_name}"])
        code, err = _run([
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=in", "action=allow",
            "protocol=UDP",
            f"localip={_local_ips()}",
            "profile=private,domain",
            f"program={path}",
            "enable=yes",
        ])
        (added if code == 0 else failed).append(
            path.name if code == 0 else f"{path.name}: {err}")

    return {
        "ok": not failed,
        "added": added,
        "failed": failed,
        "error": "; ".join(failed) if failed else "",
    }


def _local_ips() -> str:
    """Свои адреса одной строкой — правило не должно быть лишним."""
    ips = {"127.0.0.1"}
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.2)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip:
                ips.add(ip)
        finally:
            s.close()
    except OSError:
        pass
    return ",".join(sorted(ips))
