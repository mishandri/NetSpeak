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
except Exception:
    # Не только ImportError: pystray при импорте сам подбирает бэкенд, и
    # на Linux без описаний индикатора из него вылетает ValueError
    # («Namespace AyatanaAppIndicator3 not available») — его pystray не
    # ловит, и через наш except ImportError он проходил насквозь, роняя
    # программу целиком ещё до первого окна, у клиента и у сервера.
    # Значок в трее необязателен, поэтому глотаем всё.
    pystray = None


def _give_tray_its_own_context() -> None:
    """Трею — собственный GLib-контекст, иначе он ссорится с окном.

    pystray заводит свой цикл на контексте по умолчанию: в его GtkIcon._run
    стоит GLib.MainLoop.new(None, ...). А окно на Linux работает через
    Gtk.Application, которому нужен тот же контекст по умолчанию, но на
    главном потоке. Кто первый успел, тот и прав: успел значок — окно
    умирало с «g_application_run() cannot acquire the default main
    context», успевало окно — не заводился значок. Разводим их по разным
    контекстам: окну остаётся контекст по умолчанию, значку выдаём
    собственный.

    Тело ниже повторяет pystray один в один, отличаясь только контекстом;
    pystray закреплён за 0.19, так что тело не уплывёт.
    """
    try:
        from gi.repository import GLib
        gtk_icon = pystray._util.gtk.GtkIcon
    except Exception:
        return  # моста нет — значок и так не заведётся

    def _run(self):
        self._loop = GLib.MainLoop.new(GLib.MainContext.new(), False)
        self._initialize()
        try:
            self._loop.run()
        except Exception:
            self._log.error(
                "An error occurred in the main loop", exc_info=True)
        finally:
            self._finalize()

    gtk_icon._run = _run


def _give_page_mic_permission() -> None:
    """Странице — разрешение на микрофон без окна с вопросом.

    WebKitGTK сам никакого диалога не показывает: если у окна нет
    обработчика сигнала permission-request, запрос получает отказ
    молча — и микрофон не открывается никогда, даже при живых списках
    устройств. В Windows (WebView2) этот вопрос решает сама система, а
    в GTK диалога нет совсем, поэтому обработчик ставим мы.

    Разрешается только медиа (UserMediaPermissionRequest — микрофон с
    камерой): это цель программы, и окно знает точно, какую страницу
    оно показывает. Всё остальное — уведомления, геолокация и прочее —
    отказ: страница об этом не просила, а разрешения собираются
    без напоминаний и не вслепую.
    """
    try:
        from webview.platforms import gtk as gtk_backend
    except Exception:
        return  # не Linux/без GTK — вопрос и не встанет
    try:
        from gi.repository import WebKit2
        media_class = getattr(WebKit2, "UserMediaPermissionRequest", None)
    except Exception:
        media_class = None

    original = gtk_backend.BrowserView.__init__
    if getattr(original, "_netspeak_media", False):
        return  # уже обёрнуто

    def _ns_asked(webview, request):
        try:
            if media_class is not None and isinstance(request, media_class):
                request.allow()
            else:
                request.deny()
        except Exception:
            pass
        return True

    def wrapped(self, *args, **kwargs):
        original(self, *args, **kwargs)
        try:
            self.webview.connect("permission-request", _ns_asked)
        except Exception:
            pass  # окно и так откроется — только без авто-разрешения

    wrapped._netspeak_media = True
    gtk_backend.BrowserView.__init__ = wrapped


if pystray is not None and sys.platform.startswith("linux"):
    _give_tray_its_own_context()
if sys.platform.startswith("linux"):
    # разрешение на микрофон — отдельная история от трея и не зависит от него
    _give_page_mic_permission()


# ────────────────────────────── значок ────────────────────────────────────

def _find_icon_file(stem: str, ext: str) -> str:
    """Ищет файл значка: сперва рядом с exe, потом в пакете и исходниках.

    Рядом с exe — чтобы можно было подменить значок, не пересобирая
    (сборщик Linux кладёт там png для меню приложений).
    """
    base = Path(getattr(sys, "_MEIPASS", paths_root()))
    candidates = [
        paths_root() / f"{stem}{ext}",
        base / "web" / f"{stem}{ext}",
        paths_root() / "web" / f"{stem}{ext}",
        paths_root() / "src" / "web" / f"{stem}{ext}",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return ""


def icon_path(variant: str = "") -> str:
    """Файл значка для трея: сперва png, затем .ico.

    variant: "" — клиент (зелёный), "server" — бордовый. png первым:
    PIL, что рисует значок в трее, открывает оба формата, а png в Linux
    вокруг и так есть — тот же файл, что показывает меню приложений.
    """
    stem = "icon-server" if variant == "server" else "icon"
    png = _find_icon_file(stem, ".png")
    return png if png else _find_icon_file(stem, ".ico")


def window_icon(variant: str = "") -> str:
    """Путь значка для заголовка окна, или пусто — если нечего показать.

    Windows грузит значок окна из .ico сам, а в Linux его берёт
    gdk-pixbuf: сжатые .ico он не читает («Сжатые значки не
    поддерживаются»), и с нашим файлом окно падало при создании.
    Поэтому для Linux — только png. Нет png — окно выйдет с системным
    значком: это лучше, чем не открыться вовсе.
    """
    stem = "icon-server" if variant == "server" else "icon"
    ext = ".ico" if sys.platform == "win32" else ".png"
    return _find_icon_file(stem, ext)


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
        # Прятать окно можно только там, где значок есть: без него
        # спрятанное окно не достать никак — ни из трея, ни откуда.
        # Без значка «_» остаётся обычным сворачиванием на панель задач.
        if self._icon is not None:
            # событие приходит из отдельного потока, но скрыть окно можно:
            # внутри pywebview используется Invoke, он переходит в поток
            # интерфейса
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
        try:
            self._icon = pystray.Icon(
                "NetSpeak", image, APP_NAME,
                menu=pystray.Menu(
                    pystray.MenuItem("Показать окно", lambda *_: self.show(), default=True),
                    pystray.MenuItem("Открыть в браузере", lambda *_: self.open_browser()),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Выход", lambda *_: self.quit()),
                ),
            )
        except Exception:
            # значок мог не собраться даже при живом pystray: в среде нет
            # области уведомлений или индикатор сломан. Окно важнее.
            self._icon = None
            return None
        # Значок крутится в отдельном потоке: на главном он заморозил бы окно
        threading.Thread(target=self._run_icon, daemon=True).start()
        return self._icon

    def _run_icon(self):
        try:
            self._icon.run()
        except Exception:
            # индикатор мог отвалиться уже после запуска. Тихо гасим:
            # трей — вежливость, а не жизненно важная часть.
            self._icon = None

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