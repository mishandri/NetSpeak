# -*- coding: utf-8 -*-
"""NetSpeak Server — локальный сервер общения.

Отдельная программа. Запустили — сервер поднялся, окно с журналом всегда на
виду, и его можно остановить или перезапустить.

Запуск:  NetSpeakServer.exe
Ключи:   --serve --tls-port N --port M   (внутренний режим без окна)
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from netspeak import APP_NAME, __version__, netinfo, paths, profile
    from netspeak import firewall as nsfirewall
    from netspeak.serverctl import ServerControl, log_note
    from netspeak.window_keeper import WindowKeeper, icon_path
else:
    from . import APP_NAME, __version__, netinfo, paths, profile
    from . import firewall as nsfirewall
    from .serverctl import ServerControl, log_note
    from .window_keeper import WindowKeeper, icon_path

import webview


def _no_verify_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def server_is_up(host: str, port: int, timeout: float = 1.2) -> bool:
    for url, ctx in ((f"https://{host}:{port}/api/info", _no_verify_ctx()),
                     (f"http://{host}:{port}/api/info", None)):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def server_ui_file() -> Path:
    base = Path(getattr(sys, "_MEIPASS", paths.root()))
    for candidate in (base / "launcher_ui" / "server.html",
                      paths.root() / "src" / "launcher_ui" / "server.html"):
        if candidate.is_file():
            return candidate
    return base / "launcher_ui" / "server.html"


def local_addresses() -> list[tuple[str, str]]:
    return netinfo.local_addresses()


def default_address() -> str:
    return netinfo.local_ip()


def _webview_storage(kind: str) -> dict:
    """Настройки окна: где хранить localStorage и не в приватном режиме.

    Приватный режим pywebview включён по умолчанию, а в нём localStorage не
    сохраняется: идентификатор человека обнулялся бы при каждом запуске.
    Если папку создать не вышло, запускаемся как есть — без идентификатора
    хуже, чем вообще без запуска.
    """
    folder = paths.webview_dir(kind)
    if folder is None:
        return {"private_mode": True}
    return {"private_mode": False, "storage_path": str(folder)}


def current_exe() -> Path:
    """Файл программы, который надо подменять при обновлении."""
    if paths.is_frozen():
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def start_update_watcher(component: str):
    """Наблюдатель за обновлениями этого компонента.

    Состояние — в settings/server.json: сервер и клиент обновляются
    раздельно, и общий app.json они бы друг другу затирали.
    """
    from netspeak import updater

    return updater.UpdateWatcher(
        component, __version__, paths.root(),
        paths.settings_dir() / "server.json",
        note=log_note)


def _night_update(api, stop_event: threading.Event) -> None:
    """Ровно в 3:00 ночи — перезапуск с новой версией, если она есть.

    Разговор в это время прервётся: так и просили, а ждать «когда все
    выйдут» опаснее — разговор может не кончиться никогда.
    """
    from netspeak import updater

    while not stop_event.is_set():
        end = time.time() + updater.seconds_until_night()
        # ждём кусками по пять минут: поток просыпается быстро и его видно
        # в отладке, а висеть сутки одним sleep неудобно
        while time.time() < end:
            if stop_event.wait(300):
                return
        if api._maybe_update("3:00 ночи"):
            return


#: Сколько раз подряд поднимаем упавший сервер, прежде чем сдаться.
MAX_AUTOSTART = 5

#: После скольких секунд работы счётчик неудачных запусков обнуляется.
AUTOSTART_RESET = 300

#: Сколько проверок подряд сервер должен не отвечать, чтобы счесть сокет
#: мёртвым. Три по 15 секунд — почти минута, за которую он заведомо
#: успевает ответить.
DEAD_CHECKS = 3


def _watch_server(control: ServerControl, state: dict,
                  stop_event: threading.Event, host: str, port: int) -> None:
    """Следит за сервером и поднимает его, если тот перестал работать.

    Две разные беды, а выглядят они одинаково — «все пропали из сети»:

    1. Процесс завершился. Компьютер заснул, сеть мигнула, кто-то прибил
       процесс. Трассировки при этом не остаётся никакой.
    2. Процесс жив, но не отвечает. Обычно это aftermath сна: после
       пробуждения сокет остаётся в памяти, но новые соединения уже не
       принимает. Проверка «жив ли процесс» тут ничего не видит, и
       пользователь безнадёжно не понимает, что надо перезапустить.

    Поэтому проверяем оба: и процесс, и что сервер отвечает.

    Сдаёмся, если сервер падает подряд несколько раз: значит дело не в
    случайности, и бесконечные попытки только замусочат журнал.
    """
    starts = 0          # сколько раз поднимали упавший процесс
    silent = 0          # сколько проверок подряд сервер не отвечает
    last_started = 0.0

    while not stop_event.is_set():
        if stop_event.wait(15):
            return
        # пользователь сам остановил — не трогаем
        if not state.get("want_server", True):
            continue

        # 1. процесса нет
        if not control.is_running():
            starts += 1
            if starts > MAX_AUTOSTART:
                log_note(f"{APP_NAME}: сервер завершился {starts} раз подряд — "
                         f"больше не поднимаю, посмотрите logs/server.log")
                state["want_server"] = False
                return
            log_note(f"{APP_NAME}: сервер молча завершился, поднимаю заново "
                     f"(попытка {starts} из {MAX_AUTOSTART})")
            if stop_event.wait(2):
                return
            try:
                control.start()
                last_started = time.time()
            except Exception as exc:
                log_note(f"{APP_NAME}: не удалось поднять сервер: {exc}")
            continue

        # 2. процесс есть — отвечает ли он
        try:
            alive = server_is_up(host, port, timeout=4.0)
        except Exception:
            alive = False
        if alive:
            silent = 0
            if starts and time.time() - last_started > AUTOSTART_RESET:
                starts = 0
            continue

        silent += 1
        if silent < DEAD_CHECKS:
            continue
        silent = 0
        log_note(f"{APP_NAME}: сервер жив, но не отвечает — перезапускаю. "
                 f"Обычно это значит, что компьютер был в спящем режиме.")
        try:
            control.restart()
            last_started = time.time()
        except Exception as exc:
            log_note(f"{APP_NAME}: не удалось перезапустить сервер: {exc}")



def push_to_page(window, func: str, *args) -> None:
    """Дёргает функцию на странице окна сервера.

    Аргументы распаковываются: страница ждёт set_addr(host, port), а не
    set_addr([host, port]). Без «...» второй аргумент приходил
    неопределённым, и адрес показывался как «8766undefined».
    """
    import json
    payload = json.dumps(list(args), ensure_ascii=False)
    try:
        window.evaluate_js(f"window.{func} && window.{func}(...{payload})")
    except Exception:
        pass


class ServerApi:
    """Методы окна сервера: остановить, перезапустить, открыть в браузере."""

    def __init__(self, control: ServerControl, state: dict):
        self.control = control
        self.state = state

    def _watcher(self):
        return self.state.get("watcher")

    def get_state(self) -> dict:
        return {
            "host": self.control.host,
            "port": self.control.port,
            "url": f"http://127.0.0.1:{max(1, self.control.port - 1)}/",
            "running": self.control.is_running(),
            "canStart": True,
            "startedAt": self.control.started_at(),
            "update": self._update_info(),
        }

    def _update_info(self) -> dict:
        """Что с обновлением — окну показывать незачем, но полезно знать."""
        w = self._watcher()
        if w is None:
            return {"pending": False}
        return {
            "pending": w.has_pending(),
            "from": w.state.from_version,
            "to": w.state.to_version,
        }

    def poll(self) -> dict:
        """Состояние и хвост журнала одним заходом.

        Окно спрашивает само, а не ждёт толчков с сервера: журнал пишется
        по мере работы, и при одном заполнении при открытии он оставался бы
        пустым — сервер печатает шапку уже после старта окна.
        """
        return {
            "running": self.control.is_running(),
            "startedAt": self.control.started_at(),
            "log": self.control.tail(),
            "update": self._update_info(),
        }

    def open_browser(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{max(1, self.control.port - 1)}/")

    def toggle_server(self) -> None:
        if self.control.is_running():
            self.control.stop()
            # пометка для сторожа: сервер остановили руками, поднимать не надо
            self.state["want_server"] = False
        else:
            self.control.start()
            self.state["want_server"] = True
        log_note(f"{APP_NAME}: сервер {'остановлен' if not self.control.is_running() else 'запущен'}")
        self.refresh()

    def restart_server(self) -> None:
        # Нажали «Перезапустить» — самое время одеть новое exe, если оно
        # уже скачано. Иначе обновление ждало бы следующего перезапуска.
        if self._maybe_update("перезапуск по кнопке"):
            return
        # сеть за это время могла смениться — прежние адреса больше не годятся
        netinfo.reset_cache()
        self.state["want_server"] = True
        self.control.restart()
        log_note(f"{APP_NAME}: сервер перезапущен")
        self.refresh()

    def note_firewall(self) -> None:
        """Один раз при запуске сказать в журнал, пускает ли брандмауэр.

        Голос идёт напрямую между компьютерами. Если входящие закрыты,
        связи не будет, а выглядит всё исправно: сервер работает, все на
        месте, только звука нет. Пусть журнал об этом скажет сразу, а не
        после получаса догадок.
        """
        if nsfirewall is None:
            return
        try:
            if nsfirewall.has_rules():
                log_note(f"{APP_NAME}: брандмауэр разрешает входящие — голос должен идти")
                return
            if not nsfirewall.exes():
                return          # запущено из исходников: правила пока не про что
            if nsfirewall.is_admin():
                log_note(f"{APP_NAME}: брандмауэр НЕ пускает входящие — "
                         f"нажмите «Разрешить в брандмауэре», иначе звука не будет")
            else:
                log_note(f"{APP_NAME}: брандмауэр НЕ пускает входящие — "
                         f"запустите от имени администратора и нажмите "
                         f"«Разрешить в брандмауэре», иначе звука не будет")
        except Exception:
            pass      # проверка брандмауэра не должна мешать запуску

    def allow_firewall(self) -> dict:
        """Разрешить входящие подключения — по кнопке, не молча.

        Голос идёт напрямую между компьютерами, минуя сервер, и брандмауэр
        Windows часто его не пускает. Обойти это иначе нечем, а найти
        трудно: выглядит всё исправно, просто никто никого не слышит.
        """
        if nsfirewall is None:
            return {"ok": False, "error": "модуль брандмауэра не собран"}
        try:
            result = nsfirewall.allow_incoming()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if result.get("ok"):
            what = ", ".join(result.get("added") or []) or "оба файла"
            log_note(f"{APP_NAME}: брандмауэр разрешил входящие для {what}")
        else:
            log_note(f"{APP_NAME}: брандмауэр не тронут: {result.get('error')}")
        return result

    def _maybe_update(self, why: str) -> bool:
        """Подменить программу, если обновление скачано. True — мы выходим."""
        w = self._watcher()
        if w is None or not w.has_pending():
            return False
        log_note(f"{APP_NAME}: обновление {w.state.from_version} → "
                 f"{w.state.to_version}, {why}")
        state = self.state
        w.apply_pending(current_exe())
        # Окно закрываем и выходим: дальше запустится уже новая программа
        state["update_exit"] = True
        win = state.get("window")
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        return True

    def refresh(self) -> None:
        win = self.state.get("window")
        if win is None:
            return
        running = self.control.is_running()
        push_to_page(win, "set_state", running,
                     f"запущен в {self.control.started_at()}" if running else "")
        push_to_page(win, "log_text", self.control.tail())


def run_server_app() -> int:
    paths.ensure_dirs()
    profile.ensure_files()

    watcher = start_update_watcher("server")

    # Скачанное при прошлом запуске надеваем сразу, до всяких окон: иначе
    # пользователь увидел бы «переписка», а на следующем старте программа
    # молча сменилась бы версией.
    if watcher.has_pending():
        if watcher.apply_pending(current_exe()):
            log_note(f"{APP_NAME}: обновление {watcher.state.from_version} → "
                     f"{watcher.state.to_version} устанавливается")
            return 0
        watcher.discard_pending()

    app = profile.load_app()
    port = int(app.get("defaultPort", 8766))
    host = default_address()
    control = ServerControl(host, port)

    # Если на порту уже кто-то отвечает — не лезем второй раз
    state = {"window": None, "watcher": watcher, "update_exit": False,
             "stop": threading.Event()}
    if server_is_up(host, port):
        log_note(f"{APP_NAME}: на {host}:{port} уже отвечает сервер")
        # чужой сервер: наш поднимать нельзя, порт занят
        state["want_server"] = False
    else:
        control.start()
        state["want_server"] = True

    api = ServerApi(control, state)

    window = webview.create_window(
        f"{APP_NAME} — сервер",
        str(server_ui_file()),
        js_api=api,
        width=700, height=540, min_size=(560, 420),
        background_color="#0e1319",
    )
    state["window"] = window

    # Окно само спрашивает состояние и журнал по poll(). Одноразовое
    # заполнение при loaded бесполезно: сервер печатает шапку в файл уже
    # после открытия окна, и в «живой» раз читалось бы пустое.

    keeper = WindowKeeper(window, browser_url="", ask_on_close=False,
                          on_quit=control.stop, variant="server")
    keeper.start_tray()

    # Обновление в 3:00 ночи: сервер молча перезапускается с новой версией.
    threading.Thread(target=_night_update, args=(api, state["stop"]),
                     daemon=True).start()

    # Сторож: если сервер умер или перестал отвечать — поднимаем заново.
    api = ServerApi(control, state)
    # про брандмауэр — сразу, до всякой диагностики: если входящие закрыты,
    # это главная причина «звука нет», и лучше сказать об этом на старте
    threading.Thread(target=lambda: (api.note_firewall()), daemon=True).start()
    threading.Thread(target=_watch_server,
                     args=(control, state, state["stop"], host, port),
                     daemon=True).start()

    # раз в сутки смотрим, не вышло ли нового для себя. Скачиваем сразу.
    watcher.start(first_delay=10.0)


    # Закрытие окна сервера = остановка сервера: незачем оставлять его
    # висеть, если управлять им уже некому
    def on_closing():
        return True

    window.events.closing += on_closing

    webview.start(icon=icon_path("server") or None,
                **_webview_storage("server"))
    watcher.stop()
    state["stop"].set()
    control.stop()
    keeper.stop_tray()
    return 0


def main() -> int:
    args = sys.argv[1:]

    # Внутренний режим: сервер без окна. Его поднимает сам NetSpeakServer,
    # чтобы перезапуск не мигал окном.
    if "--serve" in args:
        _attach_log_to_file()
        try:
            if paths.is_frozen():
                import server
            else:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import server
        except ImportError as exc:
            print(f"Не удалось загрузить сервер: {exc}")
            return 2
        return server.main(args[args.index("--serve") + 1:])

    _quiet_print_streams()
    return run_server_app()


def _attach_log_to_file() -> None:
    """Вывод сервера всегда идёт в logs/server.log.

    Раньше файл подключался только когда консоли не было. Из проводника
    работало, а из консоли вывод уходил в неё и журнал оставался пустым.
    Теперь всегда в файл: журнал и есть то, что показывает окно сервера.
    Запись построчная, иначе строки появлялись бы пачками и с задержкой.
    """
    paths.ensure_dirs()
    handle = open(paths.logs_dir() / "server.log", "a", encoding="utf-8",
                  errors="replace", buffering=1)
    handle.write(f"\n===== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = handle
    sys.stderr = handle


def _quiet_print_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())