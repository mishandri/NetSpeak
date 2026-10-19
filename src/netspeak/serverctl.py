# -*- coding: utf-8 -*-
"""Запуск, остановка и перезапуск локального сервера.

Живёт отдельно от окна запуска: сервер теперь самостоятельная программа
(NetSpeakServer.exe), а клиент только подключается.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, paths

#: Имя программы сервера. Рядом с клиентом — на случай «запустить сервер».
#: На Linux файлы собираются без расширения, поэтому и имя другое.
SERVER_EXE = "NetSpeakServer" + (".exe" if os.name == "nt" else "")


def server_argv(port: int, plain: int | None = None) -> list[str]:
    """Команда запуска сервера.

    port — тот, по которому подключаются. Соседний (на единицу меньше)
    перенаправляет на него, чтобы старые ссылки не ломались.
    """
    plain = max(1, int(plain if plain is not None else int(port) - 1))
    if paths.is_frozen():
        exe = paths.root() / SERVER_EXE
        # при сборке оба файла рядом; если нет — пробуем текущий
        if not exe.is_file():
            exe = Path(sys.executable)
        return [str(exe), "--serve", "--tls-port", str(port), "--port", str(plain)]
    return [sys.executable, str(paths.root() / "src" / "server.py"),
            "--tls-port", str(port), "--port", str(plain)]


def server_exe_path() -> Path | None:
    """Файл программы сервера рядом с клиентом — если он там есть."""
    for name in (SERVER_EXE, "NetSpeakServer" + (".exe" if os.name == "nt" else "")):
        p = paths.root() / name
        if p.is_file():
            return p
    return None


def log_note(text: str) -> None:
    """Пометка в logs/launcher.log: она помогает разобраться, если что-то
    пошло не так, а окна уже нет."""
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(paths.logs_dir() / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {text}\n")
    except OSError:
        pass


class ServerProcess:
    """Сервер в фоне. Журнал пишется в logs/server.log."""

    def __init__(self, port: int):
        self.port = int(port)
        self.proc: subprocess.Popen | None = None
        self.log_path = paths.logs_dir() / "server.log"
        self.log_file = None
        self.started_at = ""

    def start(self) -> None:
        paths.ensure_dirs()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        # без этого вывод копится в буфере и в журнале появляется с задержкой
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("NETSPEAK_NO_TLS", None)
        self.log_file = open(self.log_path, "a", encoding="utf-8",
                             errors="replace", buffering=1)
        self.log_file.write(f"\n===== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} "
                            f"порт {self.port} =====\n")
        self.proc = subprocess.Popen(
            server_argv(self.port),
            stdout=self.log_file, stderr=subprocess.STDOUT,
            env=env, cwd=str(paths.root()),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.started_at = time.strftime("%H:%M:%S")
        log_note(f"{APP_NAME}: сервер запущен, порт {self.port}, pid {self.proc.pid}")

    def tail(self, lines: int = 120) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            log_note(f"{APP_NAME}: сервер остановлен")
        try:
            if self.log_file:
                self.log_file.close()
        except Exception:
            pass


class ServerControl:
    """Состояние сервера для окна управления.

    Окно может быть одно, а сервер запускается и останавливается сколько
    угодно раз — поэтому состояние живёт здесь, а не в обработчиках.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = int(port)
        self.proc: ServerProcess | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.is_running():
                return
            self.proc = ServerProcess(self.port)
            self.proc.start()

    def stop(self) -> None:
        with self._lock:
            if self.proc:
                self.proc.stop()

    def restart(self) -> None:
        with self._lock:
            if self.proc:
                self.proc.stop()
            # пауза, чтобы порт успел освободиться
            time.sleep(0.6)
            self.proc = ServerProcess(self.port)
            self.proc.start()

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.is_running())

    def started_at(self) -> str:
        return self.proc.started_at if self.proc else ""

    def tail(self, lines: int = 200) -> str:
        return self.proc.tail(lines) if self.proc else ""