# -*- coding: utf-8 -*-
"""NetSpeak — клиент.

Программа только подключается: спрашивает адрес сервера и открывает
приложение — отдельным окном или в браузере.

Сервер поднимает отдельная программа, NetSpeakServer.exe. Разделено потому,
что сервер живёт долго и им нужно управлять (остановить, перезапустить),
а клиент открывают на минуту и закрывают.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from netspeak import APP_NAME, __version__, netinfo, paths, profile
    from netspeak.app_proxy import LocalApp
    from netspeak.window_keeper import WindowKeeper, icon_path
else:
    from . import APP_NAME, __version__, netinfo, paths, profile
    from .app_proxy import LocalApp
    from .window_keeper import WindowKeeper, icon_path

import webview


def _no_verify_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def server_is_up(host: str, port: int, timeout: float = 1.5) -> bool:
    """Отвечает ли сервер по этому адресу."""
    for url, ctx in ((f"https://{host}:{port}/api/info", _no_verify_ctx()),
                     (f"http://{host}:{port}/api/info", None)):
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def local_addresses() -> list[tuple[str, str]]:
    return netinfo.local_addresses()


def default_address() -> str:
    return netinfo.local_ip()


def _is_own_machine(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in ("127.0.0.1", "localhost", "::1"):
        return True
    return h in {ip for _, ip in local_addresses()}


def loopback_port(secure_port: int) -> int:
    """Порт, по которому обращаемся к своему же серверу.

    Порт, который вводит пользователь, — защищённый, там HTTPS. Соседний
    отдаёт то же обычным HTTP, и именно он нужен: браузер считает 127.0.0.1
    защищённым контекстом, поэтому микрофон работает и предупреждения о
    сертификате нет. Обращаться к защищённому порту обычным HTTP нельзя —
    соединение обрывается пустым ответом.
    """
    return max(1, int(secure_port) - 1)


def with_version(url: str) -> str:
    """Дописать к адресу версию клиента.

    Страница читает её из адреса и показывает в настройках. Заодно это
    верный признак: если в настройках пусто или стоит чужое число, значит
    страницу отдал старый сервер.
    """
    base = (url or "").rstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}cv={urllib.parse.quote(__version__)}"


def app_url(host: str, port: int) -> str:
    """Куда открывать приложение.

    Свой сервер — через 127.0.0.1 и обычный HTTP. Чужой — только HTTPS:
    по обычному HTTP из сети микрофон не отдадут, адрес не считается
    защищённым.
    """
    if _is_own_machine(host):
        return f"http://127.0.0.1:{loopback_port(port)}"
    return f"https://{host}:{port}"


def local_serves_app(port: int) -> bool:
    """Отдаёт ли локальный порт приложение целиком (а не редирект)."""
    url = f"http://127.0.0.1:{loopback_port(port)}/"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = r.read(400)
        return r.status == 200 and b"NetSpeak" in body
    except (urllib.error.URLError, OSError, ValueError):
        return False


def note(text: str) -> None:
    """Пометка в logs/launcher.log."""
    try:
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(paths.logs_dir() / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {stamp[-8:]} {text}\n")
    except OSError:
        pass


# ────────────────────────────── обновления ────────────────────────────────

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


def start_update_watcher():
    """Наблюдатель за обновлениями клиента.

    Состояние лежит в settings/update.json, а не в app.json: save_app()
    перезаписывает настройки целиком и всё постороннее оттуда вычищает.
    Отдельный файл переживает любые правки настроек.
    """
    from netspeak import updater

    return updater.UpdateWatcher(
        "client", __version__, paths.root(),
        paths.settings_dir() / "update.json", note=note)


# ─────────────────────────── окно приложения ─────────────────────────────

def open_app_window(url: str, state: dict) -> None:
    """Открывает приложение и прячет окно подключения.

    Окно подключения именно прячется, а не закрывается: если закрыть его
    сразу после создания окна приложения, pywebview успевает решить, что
    окон больше нет, и завершиться. А с закрытием приложения мы закрываем
    и его — тогда процесс завершается правильно.
    """
    window = webview.create_window(
        APP_NAME, url,
        width=1280, height=800, min_size=(880, 560),
        background_color="#0e1319", text_select=True,
    )

    launcher = state.get("window")
    keeper_holder = state.get("keeper")

    def on_app_exit():
        # пользователь подтвердил закрытие приложения — убираем и его окно
        if keeper_holder is not None:
            keeper_holder.stop_tray()
        if launcher is not None:
            try:
                launcher.destroy()
            except Exception:
                pass
        state["window"] = None

    WindowKeeper(window, browser_url=url + "/", ask_on_close=True,
                 on_exit=on_app_exit).start_tray()
    window.show()
    note(f"окно приложения открыто: {url}")

    # прячем окно подключения и убираем его значок: остаётся один трей
    if keeper_holder is not None:
        keeper_holder.stop_tray()
    if launcher is not None:
        try:
            launcher.hide()
        except Exception:
            pass


def client_ui_file() -> Path:
    base = Path(getattr(sys, "_MEIPASS", paths.root()))
    for candidate in (base / "launcher_ui" / "client.html",
                      paths.root() / "src" / "launcher_ui" / "client.html"):
        if candidate.is_file():
            return candidate
    return base / "launcher_ui" / "client.html"


class ClientApi:
    """Методы страницы подключения."""

    def __init__(self, state: dict):
        self.state = state

    def get_state(self) -> dict:
        app = profile.load_app()
        addresses = local_addresses()
        data = {
            "root": str(paths.root()),
            "defaultPort": app.get("defaultPort", 8766),
            "defaultAddress": default_address(),
            "lastServer": app.get("lastServer") or {},
            "ownAddresses": [ip for _, ip in addresses] + ["127.0.0.1", "localhost"],
        }
        note("связь со страницей работает, адресов найдено: " + str(len(addresses)))
        return data

    def connect(self, host: str, port: int, in_browser: bool = False) -> bool:
        port = int(port)
        profile.save_app({"lastServer": {"host": host, "port": port}})

        if not server_is_up(host, port):
            note(f"по адресу {host}:{port} никто не отвечает")
            return False

        own = _is_own_machine(host)
        # Свой сервер должен отдавать приложение целиком. Если не отдаёт —
        # открывать окно бессмысленно: пользователь увидит пустоту.
        if own and not local_serves_app(port):
            note(f"локальный адрес {app_url(host, port)} не отдаёт приложение")
            return False

        # Открывать в браузере оставляем как есть: там всё равно придётся
        # подтверждать сертификат, и посредник тут не поможет — браузер
        # открывает адрес сам, мимо нас.
        if in_browser:
            url = with_version(app_url(host, port))
            webbrowser.open(url + "/")
            note(f"открыто в браузере: {url}")
            return True

        if own:
            # Свой сервер: 127.0.0.1 браузер считает защищённым и так,
            # посредник тут только лишний процесс.
            url = with_version(app_url(host, port))
            open_app_window(url, self.state)
            return True

        # Чужой сервер. Открывать прямо на его адрес нельзя: браузер на
        # каждом чужом компьютере спотыкался о самоподписанный сертификат и
        # просил нажать «Дополнительно → Перейти дальше». Вместо этого
        # поднимаем посредник на 127.0.0.1: для браузера адрес свой и
        # доверенный, предупреждения нет совсем, а разговор с сервером
        # по-прежнему идёт по https.
        app = LocalApp(host, port, secure=True)
        try:
            url = with_version(app.start())
        except OSError as exc:
            note(f"не удалось открыть посредник: {exc}")
            return False
        # прежний посредник, если он был: человек мог уйти и вернуться,
        # и без этого старый так и слушал бы порт до конца работы клиента
        previous = self.state.get("app_proxy")
        if previous is not None:
            previous.stop()
        self.state["app_proxy"] = app
        note(f"приложение открыто через посредник {url} на {host}:{port}")
        open_app_window(url, self.state)
        return True


def run_client() -> int:
    paths.ensure_dirs()
    profile.ensure_files()

    # Обновление надеваем до всяких окон. Скачанное при прошлом запуске
    # перезаписать можно только сняв с программы образ запуска, поэтому
    # подменяем файл и сразу отдаём запуск новому экземпляру.
    watcher = start_update_watcher()
    if watcher.has_pending():
        note(f"устанавливаю обновление {watcher.state.from_version} → "
             f"{watcher.state.to_version}")
        if watcher.apply_pending(current_exe()):
            return 0
        watcher.discard_pending()

    state: dict = {"window": None, "keeper": None, "stay_open": False}
    api = ClientApi(state)

    ui_file = client_ui_file()
    note(f"клиент открывается из: {ui_file} (найден: {ui_file.is_file()})")

    window = webview.create_window(
        f"{APP_NAME} {__version__}",
        str(ui_file),
        js_api=api,
        # Высота с запасом: содержимое занимает около 300px, но при
        # масштабировании в Windows (125%, 150%) оно растёт, и кнопка
        # «Подключиться» уезжала за нижний край окна.
        width=520, height=430, min_size=(480, 340),
        background_color="#0e1319",
    )
    state["window"] = window

    keeper = WindowKeeper(window, browser_url="", ask_on_close=False)
    state["keeper"] = keeper
    keeper.start_tray()

    window.events.closing += (lambda: True)

    # Проверка обновлений — на каждом запуске, как и просили. Раньше здесь
    # стоял суточный шаг, общий с сервером, и клиент после первой проверки
    # молчал до следующих суток. Теперь у клиента свой короткий шаг,
    # см. CLIENT_CHECK_SECONDS.
    threading.Thread(target=_check_update_once, args=(watcher,),
                     daemon=True).start()

    # Значок окна задаётся здесь, а не в create_window: у pywebview он
    # общий для всех окон и передаётся при запуске
    webview.start(icon=icon_path() or None, **_webview_storage("client"))
    watcher.stop()
    keeper.stop_tray()
    # посредник живёт в этом же процессе: без остановки он остался бы
    # слушать порт после выхода
    proxy = state.get("app_proxy")
    if proxy is not None:
        proxy.stop()
    return 0


def _check_update_once(watcher) -> None:
    """Один раз посмотреть новые версии и скачать, если есть.

    Отдельно от UpdateWatcher.start(): клиенту не нужен круглосуточный
    фон, ему хватает одной проверки за запуск.
    """
    try:
        # небольшая пауза, чтобы не мешать появлению окна
        time.sleep(4.0)
        if watcher.due():
            watcher.look()
        if watcher.has_pending():
            note("обновление скачано, программа обновится при следующем запуске")
    except Exception as exc:
        note(f"проверка обновлений не удалась: {exc}")


def _quiet_print_streams() -> None:
    """Для оконного режима: заглушка, чтобы случайный print() не уронил окно."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main() -> int:
    _quiet_print_streams()
    return run_client()


if __name__ == "__main__":
    sys.exit(main())