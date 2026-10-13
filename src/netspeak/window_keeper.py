# -*- coding: utf-8 -*-
"""Окно приложения: трей, сворачивание, подтверждение закрытия.

Логика одна и та же для окна запуска и для самого приложения: различаются
только адрес для браузера и то, спрашивать ли перед закрытием.

Почему не Tkinter: pywebview требует главный поток, и Tkinter тоже требует
главный поток. В одном процессе оба работать не станут, поэтому интерфейс
один — на WebView2.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from netspeak import APP_NAME
else:
    from . import APP_NAME

try:
    from PIL import Image
except ImportError:  # без него просто не будет значка в трее
    Image = None

try:
    import pystray
except ImportError:
    pystray = None


# ────────────────────────────── значок ────────────────────────────────────

def icon_path(variant: str = "") -> str:
    """Файл значка: сперва рядом с exe, потом внутри собранного пакета.

    Рядом с exe — чтобы можно было подменить значок, не пересобирая.
    variant: "" — клиент (зелёный), "server" — бордовый.
    """
    name = "icon-server.ico" if variant == "server" else "icon.ico"
    candidates = [
        Path(getattr(sys, "_MEIPASS", paths_root())) / "web" / name,
        paths_root() / "web" / name,
        paths_root() / "src" / "web" / name,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return ""


def paths_root() -> Path:
    """Корень программы: папка с exe."""
    from netspeak import paths
    return paths.root()


def tray_image(size: int = 64, variant: str = ""):
    """Значок для трея.

    Меньше 32 пикселей он нечитаем, поэтому рисуем крупно — система
    уменьшит сама.
    """
    if Image is None:
        return None
    src = icon_path(variant)
    if not src:
        return None
    try:
        return Image.open(src).convert("RGBA").resize((size, size), Image.LANCZOS)
    except OSError:
        return None


def confirm_exit(title: str, text: str) -> bool:
    """Спросить «точно закрыть?» и получить ответ.

    Настоящий диалог Windows с кнопками «Да» и «Нет». Свой рисовать
    пришлось бы потому, что встроенный у pywebview предлагает «ОК/Отмена».
    """
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons, DialogResult

        result = MessageBox.Show(
            text, title, MessageBoxButtons.YesNo, MessageBoxIcon.Question
        )
        return result == DialogResult.Yes
    except Exception:
        # без pythonnet просто закрываем: подтверждение — вежливость,
        # а не требование безопасности
        return True


try:  # нужен для confirm_exit, но модуль должен грузиться и без него
    import clr  # noqa: F401

    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import MessageBoxIcon  # noqa: F401
except Exception:  # pragma: no cover
    MessageBoxIcon = None


# ───────────────────────────── окно ───────────────────────────────────────

class WindowKeeper:
    """Трей, «_» в трей и «x» с вопросом — для одного окна."""

    def __init__(self, window, browser_url: str = "", ask_on_close: bool = True,
                 on_quit=None, on_exit=None, variant: str = ""):
        self.window = window
        self.browser_url = browser_url
        self.ask_on_close = ask_on_close
        self.on_quit = on_quit
        # вызывается, когда пользователь подтвердил закрытие. Нужен, чтобы
        # закрыть второе окно программы: пока оно живо, процесс не завершится.
        self.on_exit = on_exit
        # значок: "" — клиент (зелёный), "server" — бордовый
        self.variant = variant
        self.quitting = False
        self._icon = None
        self._wire()

    # ── события окна ──────────────────────────────────────────
    def _wire(self) -> None:
        # «_»: сворачиваем не в панель задач, а в трей
        self.window.events.minimized += self._on_minimized
        # «x»: спрашиваем. Возврат False отменяет закрытие — событие
        # выполняется синхронно, поэтому диалог успевает показаться
        self.window.events.closing += self._on_closing

    def _on_minimized(self):
        # событие приходит из отдельного потока, но скрыть окно можно:
        # внутри pywebview используется Invoke, он переходит в поток интерфейса
        self.hide()

    def _on_closing(self):
        if self.quitting:
            return True
        if self.ask_on_close and not confirm_exit(
            f"Закрыть {APP_NAME}?", "Вы уверены, что хотите закрыть?"
        ):
            return False
        self.quitting = True
        if self.on_exit:
            try:
                self.on_exit()
            except Exception:
                pass
        return True

    # ── трей ──────────────────────────────────────────────────
    def start_tray(self):
        if self._icon is not None or pystray is None:
            return self._icon
        image = tray_image(variant=self.variant)
        if image is None:
            return None
        self._icon = pystray.Icon(
            "NetSpeak", image, APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("Показать окно", lambda *_: self.show(), default=True),
                pystray.MenuItem("Открыть в браузере", lambda *_: self.open_browser()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", lambda *_: self.quit()),
            ),
        )
        # Значок крутится в отдельном потоке: на главном он заморозил бы окно
        threading.Thread(target=self._icon.run, daemon=True).start()
        return self._icon

    def stop_tray(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ── команды ───────────────────────────────────────────────
    def show(self):
        try:
            self.window.show()
            self.window.restore()
            self.window.native.Activate()
        except Exception:
            try:
                self.window.show()
                self.window.restore()
            except Exception:
                pass

    def hide(self):
        try:
            self.window.hide()
        except Exception:
            pass

    def open_browser(self):
        self.show()
        if self.browser_url:
            webbrowser.open(self.browser_url)

    def quit(self):
        self.quitting = True
        self.stop_tray()
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass
        try:
            self.window.destroy()
        except Exception:
            pass
        os._exit(0)