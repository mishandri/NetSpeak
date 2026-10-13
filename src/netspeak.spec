# -*- mode: python ; coding: utf-8 -*-
"""Спека PyInstaller для NetSpeak.

Собираются две программы из одного описания: сервер и клиент. Точка входа
и имя файла приходят через переменные окружения — build.py ставит их перед
запуском:

    set NETSPEAK_ENTRY=src/serverapp.py
    set NETSPEAK_NAME=NetSpeakServer
    python -m PyInstaller src/netspeak.spec ...

Что попадает внутрь каждого файла:
  * точка входа
  * server.py — сервер запускается тем же exe по ключу --serve
  * netspeak/ — пути, профиль, хранилище, адреса сети, окно и трей
  * launcher_ui/ — страницы окна сервера и окна подключения

Папка web рядом с exe перекрывает встроенную: чтобы поправить вёрстку или
скрипт, не нужно пересобирать exe — достаточно положить папку рядом.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH).resolve()          # каталог спеки — это src
SRC = HERE

#: Точка входа, имя файла и значок задаёт build.py. Без них — клиент.
ENTRY = os.environ.get("NETSPEAK_ENTRY", "launcher.py")
NAME = os.environ.get("NETSPEAK_NAME", "NetSpeak")
ICON = os.environ.get("NETSPEAK_ICON", "icon.ico")
# Файл описания версии для свойств exe. build.py готовит его на основе
# __version__, так что подняли версию в исходнике — поправились свойства.
VERSION_FILE = os.environ.get("NETSPEAK_VERSION_FILE") or None
if VERSION_FILE and not os.path.isfile(VERSION_FILE):
    VERSION_FILE = None

datas = [
    (str(SRC / "web"), "web"),               # интерфейс приложения
    (str(SRC / "launcher_ui"), "launcher_ui"),  # окна сервера и подключения
]

hiddenimports = [
    "server",       # запускается как server.main() внутри exe
    "netspeak",
    "netspeak.paths",
    "netspeak.profile",
    "netspeak.store",
    "netspeak.netinfo",
    "netspeak.window_keeper",
    "netspeak.serverctl",
    "netspeak.db",
    "netspeak.firewall",
    "netspeak.device",
    "netspeak.history",
    "netspeak.updater",
    # окно приложения: WebView2. Хук hook-webview.py сам добавит
    # WebView2Loader.dll и скрипты, без него окно просто не откроется
    "webview",
    # значок в трее и значок окна
    "PIL", "PIL.Image",
    "pystray",
    # диалог «Вы уверены, что хотите закрыть?» с кнопками Да/Нет
    "clr",
]
hiddenimports += collect_submodules("netspeak")
hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(SRC / ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Лишнее не тащим. «email» исключать нельзя: urllib.request тянет его
    # при разборе заголовков, без него падает импорт сразу при старте.
    # tkinter тоже не нужен: все окна теперь на WebView2.
    excludes=[
        "numpy", "PyQt5", "PySide2", "pytest", "setuptools",
        "pip", "unittest", "pydoc", "http.server.test",
        "tkinter", "tkinter.ttk",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX ломает антивирусы и отладку, а выигрыш тут мизерный
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # окно приложения, а не чёрный консольный терминал
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SRC / "web" / ICON) if (SRC / "web" / ICON).is_file() else None,
    version=VERSION_FILE,
)