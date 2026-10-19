# -*- mode: python ; coding: utf-8 -*-
"""Сборка двух программ NetSpeak под Linux.

Построена по образцу build.py для Windows — тот же порядок, те же имена,
тот же способ узнать версию. Отличия только вынужденные:

  * файлы без расширения: Linux им не полагается, а слово «exe» в имени
    на рабочем столе Ubuntu сбивает с толку;
  * нет описания версии для свойств файла: такого свойства в Linux нет,
    версия видна в настройках приложения;
  * рядом кладётся запускной скрипт и .desktop, иначе программу не
    запустить двойным щелчком, а окно не получит значок.

Запуск:
    python build_linux.py
Результат — в bin-linux/.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
BIN = ROOT / "bin-linux"
BUILD = ROOT / "build-linux"

#: (имя программы, точка входа, значок для .desktop и трея, описание).
#: Значки разные, чтобы окна сервера и клиента не путались.
TARGETS = [
    ("NetSpeakServer", SRC / "serverapp.py", "icon-server.ico",
     "Сервер NetSpeak — раздаёт голос и переписку в локальной сети"),
    ("NetSpeak", SRC / "launcher.py", "icon.ico",
     "NetSpeak — клиент для разговора в локальной сети"),
]

#: Что нужно для сборки. В готовой программе этого нет: Python и все
#: пакеты уезжают внутрь файла, ставить получателю не приходится.
REQUIRED = ["PyInstaller", "webview", "pystray", "PIL", "gi"]

IMPORTS = {
    "PyInstaller": "PyInstaller",
    "webview": "webview",
    "pystray": "pystray",
    "PIL": "PIL.Image",
    "gi": "gi",
}

#: Что нужно системе, чтобы вообще появилось окно.
#:
#: Разделено на две части, и разница принципиальная:
#:
#:   ДЛЯ СБОРКИ — нужно один раз, на машине, где собираем. Без них
#:   PyInstaller не найдёт мост между Python и GTK и не упакует его:
#:   пользователь как раз получил бинарник, который падал с
#:   «No module named 'gi'».
#:
#:   ДЛЯ РАБОТЫ — нужно тому, кто запускает готовый файл. Это gtk,
#:   webkit и их описания: настоящий оконный движок внутрь чата не
#:   положишь.
#:
#: Имена webkit и libsoup различаются между Ubuntu: на 22.04 это версии
#: 4.0 и 2.4, на 24.04 — 4.1 и 3.0. Скрипт сборки не угадывает, а
#: просит поставить оба: лишнее apt не считает ошибкой, а отсутствие
#: нужного ловится на первой же попытке открыть окно.
SYSTEM_PACKAGES_BUILD = (
    "python3-pip",                    # ставить PyInstaller
    "python3-gi",                     # МОСТ между Python и GTK. Не pip!
    "python3-gi-cairo",
    "gir1.2-gtk-3.0",                 # описание GTK для моста
    "gir1.2-webkit2-4.0",             # описание движка страницы (22.04)
    "gir1.2-webkit2-4.1",             # то же для 24.04 и новее
    "gir1.2-soup-2.4",                # сеть, нужна webkit (22.04)
    "gir1.2-soup-3.0",                # то же для 24.04 и новее
    "libgtk-3-0",
    "libwebkit2gtk-4.0-37",           # движок страницы (22.04)
    "libwebkit2gtk-4.1-0",            # то же для 24.04 и новее
    "patchelf",                       # PyInstaller чинит импорты в файле
)

#: Значок в трее — по желанию: без него программа работает, но закрывать
#: окно придётся завершая процесс.
SYSTEM_PACKAGES_OPTIONAL = ("libayatana-appindicator3-1",)


def app_version() -> str:
    """Версия из исходников — тот же источник, что и на Windows."""
    text = (SRC / "netspeak" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("не нашёл __version__ в src/netspeak/__init__.py")
    return m.group(1)


def missing_packages() -> list[str]:
    import importlib.util

    return [n for n in REQUIRED if importlib.util.find_spec(IMPORTS[n]) is None]


def check_gi() -> bool:
    """Есть ли мост между Python и GTK.

    Без него окно не откроется ни при каких настройках: pywebview на
    Linux смотрит только в GTK и Qt, а мост между ними ставится
    системой, а не через pip. Собрать бинарник можно и без него — он
    получится, но при запуске упадёт с «No module named 'gi'», и
    человек не поймёт почему.
    """
    import importlib.util
    if importlib.util.find_spec("gi") is not None:
        return True

    print("Нет моста между Python и GTK (модуль gi).")
    print("Без него окно не откроется, а готовый файл упадёт с:")
    print("    ModuleNotFoundError: No module named 'gi'")
    print()
    print("Мост ставится системой, через pip он не устанавливается:")
    print("    sudo apt install python3-gi python3-gi-cairo "
          "gir1.2-gtk-3.0 gir1.2-webkit2-4.1")
    print()
    print("И собирать надо тем же Python, под который он собран:")
    print("    python3 -m venv --system-site-packages .venv")
    print("    source .venv/bin/activate")
    print("    pip install -r requirements-linux.txt")
    print()
    print("Обычным venv модуль не виден: он лежит в системной папке,")
    print("и именно поэтому простая сборка даёт бинарник без окна.")
    return False


def check_deps() -> bool:
    missing = missing_packages()
    if missing:
        print("Для сборки не хватает пакетов:")
        for name in missing:
            print(f"    — {name}")
        if "gi" in missing:
            print()
            print("gi ставится не через pip, а системой — см. ниже.")
        через_пип = [m for m in missing if m != "gi"]
        if через_пип:
            print("\nУстановите их одной командой:")
            print("    python -m pip install " + " ".join(через_пип))
        if "gi" in missing:
            print("\nА мост между Python и GTK ставится системой:")
            print("    sudo apt install python3-gi python3-gi-cairo "
                  "gir1.2-gtk-3.0 gir1.2-webkit2-4.1")
        return False

    if sys.platform != "linux":
        print("Этот сценарий собирает под Linux, а система — "
              f"{sys.platform}.")
        print("Собирать надо на Linux: PyInstaller не делает Linux из других систем.")
        return False

    return check_gi()


def check_system() -> list[str]:
    """Каких системных пакетов не хватает. Предупреждение, а не отказ."""
    if shutil.which("dpkg-query") is None:
        return []
    установлены = set()
    r = subprocess.run(["dpkg-query", "-W", "-f=${Package} ${Status}\n"],
                       capture_output=True)
    for строка in r.stdout.decode("utf-8", "replace").splitlines():
        части = строка.split()
        if len(части) >= 4 and части[-1] == "installed":
            установлены.add(части[0])
    # имена пакетов в Ubuntu 22.04 и 24.04 различаются (webkit 4.0 против
    # 4.1), поэтому отсутствие половины списка — это норма, а не беда
    return [имя for имя in SYSTEM_PACKAGES_BUILD if имя not in установлены]


def size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def make_executable(path: Path) -> None:
    """Право на запуск.

    В Linux собранный файл приходит без него: скачали по сети — не запускается.
    Ставим здесь, чтобы человеку не пришлось гуглить, что делать с ошибкой
    «Permission denied».
    """
    режим = path.stat().st_mode
    path.chmod(режим | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def png_icon(иконка: Path, размер: int = 256) -> Path | None:
    """Переделать .ico в .png — в Linux .ico не показывается.

    Windows рисует значок сам из .ico, а меню приложений Linux ждёт png
    (или имя значка из темы). Отсюда и переделка: тот же рисунок, но в
    том виде, в каком его умеет показывать система. PIL и так стоит —
    он нужен для значка в трее.
    """
    if not иконка.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(иконка) as рисунок:
            рисунок = рисунок.convert("RGBA").resize((размер, размер),
                                                      Image.LANCZOS)
            результат = BIN / f"{иконка.stem}.png"
            рисунок.save(результат, "PNG")
            return результат
    except (OSError, ValueError):
        return None


def desktop_file(name: str, icon: Path | None, comment: str, version: str) -> str:
    """Файл для меню приложений Ubuntu.

    Exec — полный путь: относительный в .desktop не работает, а положить
    файл куда-то заранее нельзя, потому что человек сам решает, куда
    разложить программу.
    """
    # без значка файл всё равно работает — просто без рисунка
    значок = str(icon) if icon else "network-workgroup"
    return f"""[Desktop Entry]
Type=Application
Name={name}
Comment={comment}
Exec={BIN / name}
Icon={значок}
Terminal=false
Categories=Network;AudioVideo;
StartupNotify=true
X-NetSpeak-Version={version}
"""


def main() -> int:
    if not check_deps():
        return 1

    version = app_version()
    print(f"Версия: {version}")

    нет_пакетов = check_system()
    if нет_пакетов:
        print("\nВнимание: возможно, не хватает системных пакетов:")
        for имя in нет_пакетов:
            print(f"    — {имя}")
        print("\n  sudo apt-get install -y " + " ".join(нет_пакетов))
        print("Без них программа соберётся, но окно может не появиться.\n")

    BIN.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    for name, entry, _icon, _description in TARGETS:
        print(f"Собираю {name}…")
        cmd = [
            sys.executable, "-m", "PyInstaller",
            str(SRC / "netspeak-linux.spec"),
            "--noconfirm", "--clean",
            "--distpath", str(BIN),
            "--workpath", str(BUILD / name),
        ]
        env = dict(os.environ)
        env["NETSPEAK_ENTRY"] = entry.name
        env["NETSPEAK_NAME"] = name
        result = subprocess.run(cmd, cwd=str(ROOT), env=env)
        if result.returncode != 0:
            print(f"Сборка {name} не удалась")
            return result.returncode

    shutil.rmtree(BUILD, ignore_errors=True)

    missing = [n for n, *_ in TARGETS if not (BIN / n).is_file()]
    if missing:
        print("Ожидались файлы, но их нет:", ", ".join(missing))
        return 1

    for name, _e, _i, _d in TARGETS:
        make_executable(BIN / name)

    # .desktop — чтобы программа запускалась двойным щелчком и имела значок
    for name, _e, icon, description in TARGETS:
        значок = png_icon(SRC / "web" / icon)
        (BIN / f"{name}.desktop").write_text(
            desktop_file(name, значок, description, version),
            encoding="utf-8", newline="\n")

    print("\nГотово:")
    for name, _e, _i, _d in TARGETS:
        файл = BIN / name
        print(f"  {файл.name:<22} {size_mb(файл):.1f} МБ   версия {version}")
    print("\nЗапуск:")
    print(f"    cd {BIN}")
    print(f"    ./{TARGETS[0][0]}")
    print("\nПодробности — docs/ubuntu.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
