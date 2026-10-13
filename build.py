# -*- mode: python ; coding: utf-8 -*-
"""Сборка двух программ: NetSpeakServer.exe и NetSpeak.exe.

Обе переносные: Python уезжает внутрь файла, ставить получателю нечего.

Сервер и клиент разведены по разным exe намеренно: сервер живёт долго и им
нужно управлять (остановить, перезапустить), а клиент открывают на минуту.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
BIN = ROOT / "bin"

#: (имя exe, точка входа, значок). Значки разные, чтобы окна сервера и
#: клиента не путались в панели задач и в трее.
TARGETS = [
    ("NetSpeakServer", SRC / "serverapp.py", "icon-server.ico"),
    ("NetSpeak", SRC / "launcher.py", "icon.ico"),
]

#: Что нужно для сборки. В готовых exe этого нет: Python и все пакеты уезжают
#: внутрь файла, поэтому получателю ничего ставить не приходится.
REQUIRED = ["PyInstaller", "webview", "pystray", "PIL"]

IMPORTS = {
    "PyInstaller": "PyInstaller",
    "webview": "webview",
    "pystray": "pystray",
    "PIL": "PIL.Image",
}


def missing_packages() -> list[str]:
    import importlib.util

    return [n for n in REQUIRED if importlib.util.find_spec(IMPORTS[n]) is None]


def check_deps() -> bool:
    missing = missing_packages()
    if not missing:
        return True
    print("Для сборки не хватает пакетов:")
    for name in missing:
        print(f"    — {name}")
    print("\nУстановите их одной командой:")
    print("    python -m pip install " + " ".join(missing))
    return False


def size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def main() -> int:
    if not check_deps():
        return 1

    BIN.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build"
    shutil.rmtree(work, ignore_errors=True)

    for name, entry, icon in TARGETS:
        print(f"Собираю {name}.exe…")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            str(SRC / "netspeak.spec"),
            "--noconfirm", "--clean",
            "--distpath", str(BIN),
            "--workpath", str(work / name),
        ]
        env = dict(os.environ)
        env["NETSPEAK_ENTRY"] = entry.name
        env["NETSPEAK_NAME"] = name
        env["NETSPEAK_ICON"] = icon
        result = subprocess.run(cmd, cwd=str(ROOT), env=env)
        if result.returncode != 0:
            print(f"Сборка {name} не удалась")
            return result.returncode

    shutil.rmtree(work, ignore_errors=True)

    missing = [n for n, _e, _i in TARGETS if not (BIN / f"{n}.exe").is_file()]
    if missing:
        print("Ожидались файлы, но их нет:", ", ".join(missing))
        return 1

    print("\nГотово:")
    for name, _entry, _icon in TARGETS:
        exe = BIN / f"{name}.exe"
        print(f"  {exe.name:<22} {size_mb(exe):.1f} МБ")
    print("\nСкопируйте оба файла в одну папку и запустите NetSpeakServer.exe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())