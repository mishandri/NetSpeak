# -*- coding: utf-8 -*-
"""Какие адреса у этой машины и что на них слушает сервер.

Живёт в отдельном модуле, потому что одно и то же нужно и окну запуска
(выбор адреса), и серверу (что написать в журнал).

На Windows адреса берутся из ipconfig, на Linux — из `ip -4 addr`. Вывод
Linux переписывается в вид ipconfig, чтобы разбор был один: иначе в коде
оказалось бы две копии правил разбора, и они разошлись бы со временем.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time

# Виртуальные адаптеры (гипервизоры, VPN, Bluetooth, Docker) пользователю
# не нужны: с другого компьютера по ним не подключишься, а предложить такой
# адрес — значит человек потратит вечер, выясняя, почему никого не слышно.
# Список названий общий для обеих систем: имена адаптеров у них разные,
# но смысл один и тот же.
_VIRTUAL = re.compile(
    r"virtual|hyper-v|vmware|virtualbox|loopback|bluetooth|isatap|teredo"
    r"|npcap|wireguard|docker|veth|virbr|br-[0-9a-f]",
    re.IGNORECASE,
)
_UNUSABLE = ("127.", "169.254.", "0.0.0.0", "255.")


#: Ответ ipconfig (или ip addr на Linux) держим в памяти. Раньше его брали заново на каждый запрос
#: (а /api/info дёргают и окно сервера, и все клиенты), и каждая такая
#: проверка запускала ipconfig — консольное окно выскакивало на экране
#: каждые несколько секунд и тут же исчезало.
#:
#: Пяти минут хватает с запасом: сеть за это время не меняется, а если
#: сменилась — окно сервера умеет сбрасывать кэш по кнопке.
_CACHE_TTL = 300.0
_cache: dict = {"at": 0.0, "value": ""}


def reset_cache() -> None:
    """Забыть прежние адреса — например, после смены сети."""
    _cache["at"], _cache["value"] = 0.0, ""


def primary_ip() -> str:
    """Главный адрес машины без запуска чего-либо.

    Открываем сокет и подключаем, ничего не отправляя: система тут же
    выбирает тот адрес, который пойдёт наружу. Способ мгновенный и не
    показывает никаких окон — им пользуемся, когда ipconfig недоступен.
    """
    for target in ("10.255.255.255", "192.168.255.255", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0.2)
            s.connect((target, 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith(_UNUSABLE):
                return ip
        except OSError:
            continue
        finally:
            s.close()
    return ""

#: Окно консоли от ipconfig. Даже с кэшем он запускается при смене сети,
#: а мигающее чёрное окно поверх приложения — это ровно то, на что жалуются.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
if os.name == "nt":
    # STARTUPINFO с флагом «не показывать окно» — второй способ, вдруг
    # первый почему-то не сработает
    _startupinfo = subprocess.STARTUPINFO()
    _startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
else:
    _startupinfo = None


def _run_ip_addr() -> str:
    """Адреса с Linux, приведённые к тому же виду, что отдаёт ipconfig.

    Чтобы не тащить в проект разбор двух форматов, вывод команды
    переписывается в вид «Адаптер …: IPv4 …» — дальше работает один и тот
    же разбор на обеих системах.
    """
    try:
        raw = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                             capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    строки = []
    for строка in raw.decode("utf-8", "replace").splitlines():
        # «3: eth0    inet 192.168.1.5/24 brd … scope global eth0»
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+([0-9.]+)", строка)
        if m:
            строки.append(f"   Адаптер {m.group(1)}:")
            строки.append(f"      IPv4-адрес: {m.group(2)}")
    return "\n".join(строки)


def _run_ipconfig() -> str:
    """Текст ipconfig в читаемом виде.

    Windows отдаёт его в OEM-кодировке (обычно cp866, а при включённом
    PYTHONUTF8 — вообще в utf-8 с битыми байтами). Поэтому перебираем
    кодировки и берём ту, где читается слово «адаптер».
    """
    now = time.monotonic()
    if _cache["value"] and now - _cache["at"] < _CACHE_TTL:
        return _cache["value"]
    if os.name != "nt":
        # на Linux ipconfig нет, и запускать нечего — «Адаптер …» приходит
        # уже приведённым к тому же виду
        text = _run_ip_addr()
        _cache["at"], _cache["value"] = now, text
        return text
    try:
        raw = subprocess.run(
            ["ipconfig", "/all"], capture_output=True, timeout=10,
            creationflags=_NO_WINDOW, startupinfo=_startupinfo).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for enc in ("utf-8", "cp866", "cp1251", "cp1252"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        low = text.lower()
        if "адаптер" in low or "adapter" in low:
            _cache["at"], _cache["value"] = now, text
            return text
    return raw.decode("utf-8", errors="replace")


def local_addresses() -> list[tuple[str, str]]:
    """Список (название адаптера, IPv4) с реальных сетевых карт."""
    found: list[tuple[str, str]] = []
    adapter = ""
    for raw_line in _run_ipconfig().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # заголовок вида «Адаптер Ethernet Wi-Fi:» или «adapter Ethernet:»
        m = re.match(r"^(?:Адаптер|Adapter)\s+(.+?):\s*$", line, re.IGNORECASE)
        if m:
            adapter = m.group(1).strip()
            continue
        m = re.match(r"IPv4[^:]*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line, re.IGNORECASE)
        if not m:
            continue
        ip = m.group(1)
        if ip.startswith(_UNUSABLE) or _VIRTUAL.search(adapter or ""):
            continue
        if not any(ip == old for _, old in found):
            found.append((adapter or "Сетевой адаптер", ip))

    if not found:  # запасной путь, если ipconfig не сработал
        # сперва пробуем узнать адрес без запуска ipconfig: так ничего
        # не мигает на экране
        ip = primary_ip()
        if ip:
            return [("Сетевой адаптер", ip)]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))  # ничего не отправляет
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith(_UNUSABLE):
                found.append(("Сетевой адаптер", ip))
        except OSError:
            pass
    if not found:
        found.append(("Сетевой адаптер", "127.0.0.1"))
    return found


def local_ip() -> str:
    """Основной адрес — тот, по которому к нам обращаются из сети."""
    return local_addresses()[0][1]


def all_ips() -> list[str]:
    """Все адреса этой машины, включая виртуальные.

    Нужны сертификату: он выписывается один раз, и если адрес не попал
    в список, браузер ругается на имя при подключении с этой машины.
    """
    ips = {local_ip(), "127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(_UNUSABLE):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)