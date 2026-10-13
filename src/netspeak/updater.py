# -*- coding: utf-8 -*-
"""Проверка обновлений по релизам GitHub.

Сервер и клиент — разные программы и разные релизы: тег релизов
`client-v1.0.0` относится к NetSpeak.exe, `server-v1.0.0` — к
NetSpeakServer.exe. Общего релиза нет намеренно: обновление клиента
никогда не должно трогать сервер и наоборот.

Как это работает
----------------
1. `latest(component)` спрашивает GitHub о последнем релизе с нужным
   префиксом тега и сравнивает версию с текущей.
2. Сервер скачивает новую версию сразу, как только нашёл, и кладёт её в
   `update/` — на случай если разговор не даст перезапуститься сейчас.
3. Перезапуск (по кнопке, в 3:00 ночи или вручную) находит скачанный файл
   и подменяет программу.

Про Windows честно: файл, который сейчас запущен, перезаписать нельзя.
Поэтому подмена делается маленьким .cmd, который ждёт выхода программы,
подменяет файл и запускает его заново.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Куда смотрим за обновлениями.
REPO = "mishandri/NetSpeak"

#: Адрес API GitHub. Отдельная переменная, а не константа в f-строке:
#: на время проверки её можно подменить на свой адрес.
API_BASE = "https://api.github.com"

#: Префикс тега по компоненту. Релиз 1.0.0 сервера — server-v1.0.0.
COMPONENTS = {
    "server": "server-v",
    "client": "client-v",
}

#: GitHub отвечает медленно, когда народу много. Ждём недолго: обновление
#: не должно подвешивать запуск программы.
TIMEOUT = 12

#: Как часто сервер проверяет новые версии: раз в сутки.
SERVER_CHECK_SECONDS = 24 * 3600

#: Когда сервер перезапускается ради обновления.
NIGHT_HOUR = 3

_TAG = re.compile(r"^(client-v|server-v)(\d+)\.(\d+)\.(\d+)$")


@dataclass
class Release:
    """Что GitHub знает о последнем релизе."""

    component: str
    version: str
    tag: str
    url: str = ""              # страница релиза, для показа пользователю
    date: str = ""             # когда выпущен, для показа
    asset_name: str = ""       # файл, который надо скачать
    asset_url: str = ""        # куда качать
    size: int = 0
    notes: str = ""

    @property
    def key(self) -> tuple[int, int, int]:
        m = _TAG.match(self.tag)
        if not m:
            return (0, 0, 0)
        return (int(m.group(2)), int(m.group(3)), int(m.group(4)))

    def newer_than(self, current: str) -> bool:
        """Новее ли релиз, чем то, что уже запущено."""
        cur = parse_version(current)
        return self.key > cur if cur else True

    def short(self) -> str:
        """«NetSpeakServer 1.1.0» — одной строкой в журнал."""
        title = "NetSpeakServer" if self.component == "server" else "NetSpeak"
        return f"{title} {self.version}"


@dataclass
class UpdateState:
    """Что уже скачано и когда в последний раз смотрели."""

    last_check: float = 0.0
    downloaded: str = ""       # путь к скачанному файлу
    from_version: str = ""
    to_version: str = ""
    release: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "lastCheck": round(self.last_check, 0),
            "downloaded": self.downloaded,
            "fromVersion": self.from_version,
            "toVersion": self.to_version,
            "release": self.release,
        }

    @classmethod
    def from_json(cls, data) -> "UpdateState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            last_check=float(data.get("lastCheck") or 0),
            downloaded=str(data.get("downloaded") or ""),
            from_version=str(data.get("fromVersion") or ""),
            to_version=str(data.get("toVersion") or ""),
            release=data.get("release") or {},
        )


def parse_version(text: str) -> tuple[int, int, int] | None:
    """«1.2.3» → (1, 2, 3). Не разобралось — None."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ───────────────────────────────── запрос к GitHub ──────────────────────────

def _get_json(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "NetSpeak-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def latest(component: str, repo: str = REPO, timeout: int = TIMEOUT) -> Release | None:
    """Последний релиз по компоненту (server или client).

    Спрашиваем список, а не /latest: у GitHub один «последний» на репозиторий,
    а релизов здесь два — на клиента и на сервер.
    """
    prefix = COMPONENTS.get(component)
    if not prefix:
        return None
    # адрес API можно переопределить: зеркало или проверка на своём сервере
    base = os.environ.get("NETSPEAK_UPDATE_API") or API_BASE
    data = _get_json(f"{base}/repos/{repo}/releases?per_page=30",
                     timeout=timeout)
    if not isinstance(data, list):
        return None

    best: Release | None = None
    for item in data:
        tag = str(item.get("tag_name") or "")
        m = _TAG.match(tag)
        if not m or not tag.startswith(prefix):
            continue
        rel = Release(
            component=component,
            version=tag[len(prefix):],
            tag=tag,
            url=str(item.get("html_url") or ""),
            date=str(item.get("published_at") or ""),
            notes=str(item.get("body") or "")[:2000],
        )
        for asset in item.get("assets") or []:
            name = str(asset.get("name") or "")
            if name.lower().endswith(".exe"):
                rel.asset_name = name
                rel.asset_url = str(asset.get("browser_download_url") or "")
                rel.size = int(asset.get("size") or 0)
                break
        if best is None or rel.key > best.key:
            best = rel
    return best


def check(component: str, current: str, repo: str = REPO) -> Release | None:
    """Есть ли релиз новее текущей версии. None — не с чем сравнивать."""
    if os.environ.get("NETSPEAK_NO_UPDATE") == "1":
        return None
    rel = latest(component, repo=repo)
    if rel is None:
        return None
    return rel if rel.newer_than(current) else None


# ───────────────────────────────── скачивание ──────────────────────────────

def download(url: str, dest: Path, progress=None) -> bool:
    """Скачать файл. Сначала во временный, потом переименовать.

    Так получатель никогда не увидит наполовину записанный файл: если
    пропадёт связь посередине, старый файл останется в покое.
    """
    if not url:
        return False
    dest = Path(dest)
    temp = dest.with_suffix(dest.suffix + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "NetSpeak-updater"})
        with urllib.request.urlopen(req, timeout=120) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(temp, "wb") as fh:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if total and temp.stat().st_size != total:
            temp.unlink(missing_ok=True)
            return False
        temp.replace(dest)
        return True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        temp.unlink(missing_ok=True)
        return False


# ───────────────────────────── подмена программы ───────────────────────────

_CMD_TEMPLATE = """@echo off
setlocal
rem Подмена программы на новую версию.
rem Windows не даёт перезаписать запущенный файл, поэтому ждём выхода.
set /a tries=0

:wait
timeout /t 2 /nobreak >nul
tasklist /fi "PID eq {pid}" /nh 2>nul | find /i "{pid}" >nul
if not errorlevel 1 goto again

copy /y "{source}" "{target}" >nul
if errorlevel 1 exit /b 1
del /q "{source}" >nul 2>nul
start "" "{target}" {args}
endlocal
exit /b 0

:again
set /a tries+=1
if {tries} lss {max_tries} goto wait
exit /b 1
"""


def stage_and_relaunch(exe: Path, new_file: Path, args: str = "",
                       max_tries: int = 90) -> bool:
    """Подготовить подмену exe и перезапуск. Программа должна сразу выйти.

    max_tries — сколько раз ждать выхода (две секунды на попытку). Нужно,
    потому что иначе скрипт будет висеть вечно, если программа почему-то
    не завершится.

    Возвращает False, если подготовить не вышло — тогда выходим как обычно.
    """
    exe = Path(exe)
    new_file = Path(new_file)
    if not exe.is_file() or not new_file.is_file():
        return False
    # Страховка от подмены не того файла: программа подменяет только себя,
    # и опечатка в пути не должна привести к порче чужого файла.
    if not exe.name.lower().startswith("netspeak"):
        return False

    script = exe.parent / "update" / "replace.cmd"
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            _CMD_TEMPLATE.format(pid=os.getpid(), source=str(new_file),
                                 target=str(exe), args=args,
                                 tries=0, max_tries=int(max_tries)),
            encoding="cp1251", errors="replace")
    except OSError:
        return False

    # CREATE_NO_WINDOW, DETACHED_PROCESS: окно не мигает и не держит
    # родителя, иначе программа не завершится
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
        getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "/min", str(script)],
                         cwd=str(exe.parent), creationflags=flags)
    except OSError:
        return False
    return True


# ───────────────────────────────── фоновая проверка ─────────────────────────

def seconds_until_night(now: float | None = None, hour: int = NIGHT_HOUR) -> float:
    """Сколько секунд до ближайшего NIGHT_HOUR. Для тестов можно передать now."""
    now = time.time() if now is None else now
    parts = time.localtime(now)
    midnight = time.mktime((parts.tm_year, parts.tm_mon, parts.tm_mday,
                            hour, 0, 0, 0, 0, -1))
    target = midnight if midnight > now else midnight + 24 * 3600
    return max(0.0, target - now)


class UpdateWatcher:
    """Раз в сутки смотрит за новой версией. Один поток, состояние в файле.

    Работает и для сервера (с перезапуском в 3:00), и для клиента
    (только скачивание — перезапуск делает пользователь).
    """

    def __init__(self, component: str, current: str, root: Path,
                 state_path: Path, note=lambda text: None,
                 repo: str = REPO):
        self.component = component
        self.current = current
        self.root = Path(root)
        self.state_path = Path(state_path)
        self.note = note
        self.repo = repo
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = UpdateState.from_json(_read_json(self.state_path))

    # ── состояние ────────────────────────────────────────────────────────
    def _save(self) -> None:
        _write_json(self.state_path, self.state.to_json())

    @property
    def update_dir(self) -> Path:
        return self.root / "update"

    @property
    def pending_file(self) -> Path:
        return self.update_dir / f"{self.component}-update.exe"

    def has_pending(self) -> bool:
        return bool(self.state.downloaded) and Path(self.state.downloaded).is_file()

    # ── шаги ─────────────────────────────────────────────────────────────
    def look(self) -> Release | None:
        """Раз за сутки: спросить GitHub и при необходимости скачать."""
        self.state.last_check = time.time()
        if os.environ.get("NETSPEAK_NO_UPDATE") == "1":
            self._save()
            return None
        rel = check(self.component, self.current, repo=self.repo)
        self._save()
        if rel is None:
            self.note("обновлений нет")
            return None
        if rel.asset_url and rel.newer_than(self.current):
            self.note(f"найдено обновление: {rel.short()}")
            if self.download_release(rel):
                return rel
            self.note("скачать не вышло, попробуем при следующей проверке")
            return None
        return None

    def download_release(self, rel: Release) -> bool:
        """Скачать новую версию и запомнить, что она готова к подмене."""
        target = self.pending_file
        size_mb = f"{rel.size / 1048576:.1f}" if rel.size else "?"
        self.note(f"качаю {rel.asset_name} ({size_mb} МБ)…")
        ok = download(rel.asset_url, target,
                      progress=lambda d, t: None)   # долгий — без процентов
        if not ok:
            return False
        self.state.downloaded = str(target)
        self.state.from_version = self.current
        self.state.to_version = rel.version
        self.state.release = {"tag": rel.tag, "url": rel.url, "date": rel.date,
                              "asset": rel.asset_name}
        self._save()
        self.note(f"скачано: {rel.short()} → {target.name}")
        return True

    def apply_pending(self, exe: Path, args: str = "") -> bool:
        """Подменить работающую программу скачанной. Вызывать перед выходом."""
        if not self.has_pending():
            return False
        src = Path(self.state.downloaded)
        if not stage_and_relaunch(exe, src, args=args):
            return False
        self.note(f"обновление уйдёт в дело при следующем запуске: "
                  f"{self.state.from_version} → {self.state.to_version}")
        self.state.downloaded = ""
        self._save()
        return True

    def discard_pending(self) -> None:
        """Выбросить скачанное: не вышло, пусть качается заново."""
        if self.state.downloaded:
            try:
                Path(self.state.downloaded).unlink(missing_ok=True)
            except OSError:
                pass
        self.state.downloaded = ""
        self._save()

    # ── цикл ─────────────────────────────────────────────────────────────
    def due(self) -> bool:
        """Пора ли смотреть: сутки прошло или ни разу не смотрели."""
        if not self.state.last_check:
            return True
        return (time.time() - self.state.last_check) >= SERVER_CHECK_SECONDS

    def start(self, first_delay: float = 8.0) -> None:
        """Запустить фоновую проверку. first_delay — чтобы не мешать старту."""
        if self._thread is not None:
            return

        def loop() -> None:
            if self._stop.wait(first_delay):
                return
            while not self._stop.is_set():
                try:
                    if self.due():
                        self.look()
                except Exception as exc:            # фон не должен падать
                    self.note(f"проверка обновлений не удалась: {exc}")
                # спим короткими кусками, чтобы остановка была быстрой
                if self._stop.wait(60):
                    return
                if time.time() - self.state.last_check < SERVER_CHECK_SECONDS:
                    continue

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name=f"update-{self.component}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".part")
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        temp.replace(path)
    except OSError:
        pass