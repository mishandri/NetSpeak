# -*- mode: python ; coding: utf-8 -*-
"""Спека PyInstaller для NetSpeak под Linux.

Отличается от виндовой src/netspeak.spec тремя вещами, и все три —
вынужденные:

  * нет значка и файла версии: и то и другое понятия Windows. В Linux
    версия видна в настройках приложения, а значок — в свойствах файла
    и в меню приложений, куда его кладёт .desktop-файл;
  * нет pythonnet (clr): он ставит окна Windows, на Linux его не бывает;
  * значок трея берётся из того же .ico — PIL его открывает и на Linux.

Всё остальное — тот же код, что и на Windows: одна программа на две
системы лучше, чем две почти одинаковые.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH).resolve()          # каталог спеки — это src
SRC = HERE

#: Точка входа и имя файла задаёт build_linux.py через окружение.
ENTRY = os.environ.get("NETSPEAK_ENTRY", "launcher.py")
NAME = os.environ.get("NETSPEAK_NAME", "NetSpeak")

#: На Linux ресурса версии нет. PyInstaller на Linux параметр version и
#: так игнорирует, но передавать его с Windows-путём нельзя: файл-то не
#: существует, и сборка упадёт на проверке.
VERSION_FILE = None

datas = [
    (str(SRC / "web"), "web"),                 # интерфейс приложения
    (str(SRC / "launcher_ui"), "launcher_ui"),  # окна сервера и подключения
]

hiddenimports = [
    "server",       # запускается как server.main() внутри файла
    "netspeak",
    # значок в трее. PIL умеет открывать .ico и на Linux, так что тот же
    # значок годится — просто перерисовывается в нужный размер.
    "PIL", "PIL.Image",
    "pystray",
    "webview",
]
hiddenimports += collect_submodules("netspeak")
hiddenimports += collect_submodules("webview")

#: pythonnet ставит окна Windows. На Linux его нет и ставить не нужно, а
#: в hiddenimports он означал бы только лишнюю ошибку при сборке.
if sys.platform == "win32":
    hiddenimports += ["clr"]

a = Analysis(
    [str(SRC / ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "numpy", "PyQt5", "PySide2", "PySide6", "pytest", "setuptools",
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
    strip=False,          # символы пригодятся, если полезут краши
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=None,            # значок задаёт .desktop, а не сам файл
    version=VERSION_FILE,
)
