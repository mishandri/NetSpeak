# -*- mode: python ; coding: utf-8 -*-
"""Сборка двух программ: NetSpeakServer.exe и NetSpeak.exe.

Обе переносные: Python уезжает внутрь файла, ставить получателю нечего.

Сервер и клиент разведены по разным exe намеренно: сервер живёт долго и им
нужно управлять (остановить, перезапустить), а клиент открывают на минуту.

Версия попадает и внутрь файла — в свойствах exe видно, что запущено и
какая это программа. Иначе два файла в одной папке выглядят одинаково,
и понять, какой из них свежий, можно только по дате.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
BIN = ROOT / "bin"
BUILD = ROOT / "build"

#: (имя exe, точка входа, значок, описание для свойств файла).
#: Значки разные, чтобы окна сервера и клиента не путались в панели задач и
#: в трее. Описания тоже разные: в свойствах файла должно быть видно, что
#: это сервер, а не клиент, — иначе запускаешь одно, а думаешь про другое.
TARGETS = [
    ("NetSpeakServer", SRC / "serverapp.py", "icon-server.ico",
     "Сервер NetSpeak — раздаёт голос и переписку в локальной сети"),
    ("NetSpeak", SRC / "launcher.py", "icon.ico",
     "NetSpeak — клиент для разговора в локальной сети"),
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


def app_version() -> str:
    """Версия из исходников — единственный источник.

    Берём оттуда же, откуда её берёт программа, поэтому расходиться они не
    могут: подняли версию в одном месте — поправилась и в свойствах файла.
    """
    text = (SRC / "netspeak" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("не нашёл __version__ в src/netspeak/__init__.py")
    return m.group(1)


def version_numbers(version: str) -> tuple[int, int, int, int]:
    """«1.2.3» → (1, 2, 3, 0). Для свойств файла нужны четыре числа."""
    parts = re.findall(r"\d+", version or "")
    while len(parts) < 4:
        parts.append("0")
    try:
        return tuple(int(p) for p in parts[:4])
    except ValueError:
        return (0, 0, 0, 0)


def write_version_file(path: Path, version: str, name: str, description: str) -> None:
    """Записать описание версии для свойств файла.

    Windows показывает его в «Свойствах → Подробно» и в списке программ
    установки. Файл — обычный текст, который читает PyInstaller при сборке.
    """
    major, minor, build_, patch = version_numbers(version)
    built = time.strftime("%Y-%m-%d %H:%M")
    # Описание и название компании по-русски Windows не покажет (у него своя
    # кодовая страница для этих полей), поэтому пишем по-английски, а
    # понятную дату и версию добавляем отдельным полем.
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {build_}, {patch}),
    prodvers=({major}, {minor}, {build_}, {patch}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'NetSpeak'),
            StringStruct('FileDescription', '{description}'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', '{name}'),
            StringStruct('LegalCopyright', 'NetSpeak'),
            StringStruct('OriginalFilename', '{name}.exe'),
            StringStruct('ProductName', 'NetSpeak'),
            StringStruct('ProductVersion', '{version}'),
            StringStruct('Comments', 'Build {built}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


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

    version = app_version()
    print(f"Версия: {version}")

    BIN.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    for name, entry, icon, description in TARGETS:
        print(f"Собираю {name}.exe…")
        vfile = BUILD / f"{name}-version.txt"
        write_version_file(vfile, version, name, description)

        cmd = [
            sys.executable, "-m", "PyInstaller",
            str(SRC / "netspeak.spec"),
            "--noconfirm", "--clean",
            "--distpath", str(BIN),
            "--workpath", str(BUILD / name),
        ]
        env = dict(os.environ)
        env["NETSPEAK_ENTRY"] = entry.name
        env["NETSPEAK_NAME"] = name
        env["NETSPEAK_ICON"] = icon
        env["NETSPEAK_VERSION_FILE"] = str(vfile)
        result = subprocess.run(cmd, cwd=str(ROOT), env=env)
        if result.returncode != 0:
            print(f"Сборка {name} не удалась")
            return result.returncode

    shutil.rmtree(BUILD, ignore_errors=True)

    missing = [n for n, *_ in TARGETS if not (BIN / f"{n}.exe").is_file()]
    if missing:
        print("Ожидались файлы, но их нет:", ", ".join(missing))
        return 1

    print("\nГотово:")
    for name, _e, _i, _d in TARGETS:
        exe = BIN / f"{name}.exe"
        print(f"  {exe.name:<22} {size_mb(exe):.1f} МБ   версия {version}")
    print("\nСкопируйте оба файла в одну папку и запустите NetSpeakServer.exe.")
    print("Версия сборки видна в свойствах файла: правый клик → Свойства → Подробно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())