# -*- coding: utf-8 -*-
"""Приложение с localhost, а разговор с сервером — по-настоящему.

Зачем это нужно
---------------
Микрофон браузер отдаёт только «защищённому» адресу: https или
http://127.0.0.1. Поэтому приложение приходилось открывать по https, а
сервер без интернета не может получить настоящий сертификат — только
самоподписанный. Браузер на чужом компьютере показывал предупреждение, и
человеку приходилось каждый раз нажимать «Дополнительно → Перейти
дальше».

Ключами браузера это не лечится: WebView2 не даёт их менять, а
Chromium-флаг «считать небезопасное безопасным» — это молчаливое
понижение защиты, и он к тому же не часть поддерживаемого интерфейса.

Почему не «просто включить IGNORE_SSL_ERRORS»
--------------------------------------------
У pywebview есть такая настройка, и она решает задачу одной строкой.
Мы её сознательно не используем.

Что она делает: на любое предупреждение о сертификате в окне отвечает
«всегда разрешать», молча и без спроса. Настройка общая для всего окна,
а не адресная: она действует на любой узел, который окно когда-либо
откроет, и человек о самом факте подмены сертификата не узнаёт.

Чем это хуже того, что здесь. Здесь недоверенным остаётся ровно один
адрес — тот, который человек сам вписал в клиент. Посредник слушает
только 127.0.0.1 и из сети недоступен; всё, что он делает, — переслать
запрос на конкретный сервер и вернуть ответ. Страница приложения при
этом вообще не знает, что где-то в сети есть сервер: для неё адрес
localhost — доверенный по определению, а не «по доверию после проверки».

Разница в одном предложении: с IGNORE_SSL_ERRORS окно доверяет любому,
кто ответит по сети; с посредником окно доверяет только тому, кого
человек назвал сам, и всё остальное в сети для него — постороннее.

Что делается здесь
-----------------
Клиент поднимает у себя маленький посредник на 127.0.0.1 и открывает
приложение через него. Для браузера адрес свой и доверенный, поэтому:

* микрофон работает — 127.0.0.1 браузер считает защищённым;
* предупреждения о сертификате нет вообще;
* никаких кнопок нажимать не нужно, каждый раз и при первом запуске.

Сам разговор с сервером по-прежнему идёт по https и без проверки
сертификата — так же, как раньше делал сам клиент для своих запросов.
Отличие в том, что проверку сертификата делает не браузер перед
человеком, а клиент, который заранее знает, к какому серверу подключается.

Посредник слушает только 127.0.0.1: из сети он недоступен.

Что стоит помнить этому коду
----------------------------
Посредник — это посредник, а не «просто переадресация». Он получил три
ошибки за время написания, и все три выглядели одинаково — «ничего не
работает»:

1. Читать поток надо по мере поступления (read1), а не порциями: обычный
   read(n) ждёт, пока наберётся n байт, а события весят сотни байт и
   приходят раз в минуту. Собеседник замолкал бы на минуты.
2. Заголовок Origin приходит строчными — Chrome шлёт «origin», а не
   «Origin». Регистр пришлось не учитывать: иначе подмена молча не
   срабатывала, и сервер отвечал 403.
3. В 403 попадали все запросы POST, а с ними — сигналы связи между
   участниками и сохранение имени с аватаром. Симптом был обманчив:
   человек появлялся в списке (поток событий шёл по GET), а звук не
   шёл и настройки не сохранялись.

4. Порт посредника обязан быть постоянным. Браузер хранит localStorage
   по адресу целиком, вместе с портом, а в хранилище лежит номер человека
   и его имя с аватаром. Со случайным портом адрес менялся при каждом
   запуске, и на чужом компьютере человек просыпался новым: всё
   настраивал заново. Проверяется сравнением хранилища на двух адресах.

Отсюда правило: трогая этот файл, проверяй не только «страница
открылась», а предложение, ответ и кандидаты соединения, сохранение
имени с аватаром и — отдельно — что адрес приложения не меняется от
запуска к запуску. Первые три делают ns_signal_test.py и ns_keep_test.py,
четвёртое — ns_port_test.py.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Заголовки, которые не переносятся через посредник: они относятся к
#: соединению «здесь» и «там», а не к содержимому ответа.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

#: Размер порции при пересылке потока. Мелкий, чтобы объявления и «кто
#: говорит» доезжали сразу, а не копились.
CHUNK = 8192

#: Порт посредника. Он обязан быть ПОСТОЯННЫМ, и это не украшение:
#: браузер хранит localStorage по адресу страницы целиком, вместе с
#: портом. Случайный порт — новый адрес при каждом запуске — и человек
#: каждое утро просыпался новым: номер и сохранённое имя с аватаром
#: остаются в хранилище прошлого адреса. Ровно это и было «настраиваю
#: заново каждый раз».
DEFAULT_LOCAL_PORT = 8768

#: Сколько портов перебирать, если заняты. Два клиента на одной машине —
#: редкость, но пусть второй просто возьмёт следующий свободный.
LOCAL_PORT_SPREAD = 10


def _кто_то_слушает(порт: int) -> bool:
    """Есть ли на порту живой слушатель.

    Короткое соединение, которое сразу закрываем: нам важно лишь дойти
    или не дойти, ничего читать не нужно.
    """
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", порт))
        except OSError:
            return False
    return True


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- настройки окна ----------------------------------------------------
    def log_message(self, *args):      # тихий посредник
        pass

    # -- что умеем ---------------------------------------------------------
    def do_GET(self):
        self._relay("GET")

    def do_HEAD(self):
        self._relay("HEAD")

    def do_POST(self):
        self._relay("POST")

    def do_PUT(self):
        self._relay("PUT")

    def do_DELETE(self):
        self._relay("DELETE")

    def do_OPTIONS(self):
        self._relay("OPTIONS")

    def do_PATCH(self):
        self._relay("PATCH")

    # -- сама пересылка ---------------------------------------------------
    def _relay(self, method: str) -> None:
        app = self.server.app                     # тип: LocalApp
        try:
            body = self._read_body()
            conn = app.connect(self.path)
            try:
                headers = self._request_headers()
                conn.request(method, self.path, body=body or None, headers=headers)
                resp = conn.getresponse()
                self._send_head(resp)
                if method == "HEAD":
                    return
                self._send_body(resp)
            finally:
                conn.close()
        except (OSError, ssl.SSLError, ValueError, http.client.HTTPException):
            # Сервер не ответил или рухнул посреди разговора. Клиент сам
            # переподключится, и молчать тут нельзя: иначе EventSource
            # будет думать, что связь жива, и не переспросит.
            self.close_connection = True
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass

    def _read_body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if n <= 0:
            return b""
        # тело читаем целиком: наш клиент шлёт маленький JSON
        return self.rfile.read(n)

    def _request_headers(self) -> dict:
        out = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP or key.lower() == "host":
                continue
            out[key] = value
        out["Host"] = self.server.app.host_port
        # без этого urllib отдаёт «Connection: close» и рвёт поток событий
        out["Connection"] = "keep-alive"
        out["Accept-Encoding"] = "identity"
        self._fix_origin(out)
        return out

    def _fix_origin(self, out: dict) -> None:
        """Адрес страницы должен совпадать с адресом сервера.

        Сервер отказывает запросам, у которых Origin не его собственный:
        так чужие сайты не могут обращаться к нему от имени человека
        (подробно — server.py, _origin_ok). Через посредник браузер
        честно отдаёт свой адрес 127.0.0.1:внутренний-порт, и сервер
        принимает его за чужой сайт — отвечает 403.

        Поэтому Origin переписываем на адрес сервера, но только если он
        и так был наш. Чужой сайт сюда не достучится: посредник слушает
        лишь 127.0.0.1, а его Origin остаётся прежним и его по-прежнему
        отвергают.
        """
        # Регистр заголовка браузер выбирает на свой вкус: Chrome шлёт
        # «origin» строчными, а не «Origin». Ищем на любой регистр — иначе
        # переписывание молча не срабатывало, и сервер отвечал 403.
        key = next((k for k in out if k.lower() == "origin"), None)
        if key is None:
            return                                  # не браузерный запрос
        origin = out[key]
        if urlparse(origin).netloc != self.headers.get("Host"):
            return                                  # не наш — не трогаем
        out[key] = self.server.app.origin

    #: send_response добавляет эти два сам — пересылать их от сервера
    #: незачем, иначе в ответе они оказываются дважды
    OUR_OWN = {"server", "date"}

    def _send_head(self, resp) -> None:
        self.send_response(resp.status)
        length = None
        for key, value in resp.getheaders():
            low = key.lower()
            if low in HOP_BY_HOP or low in self.OUR_OWN:
                continue
            if low == "content-length":
                length = value
            self.send_header(key, value)
        if length is None:
            # поток без длины (события): выдаём порциями
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _send_body(self, resp) -> None:
        """Переслать тело ответа.

        Важно читать по мере поступления, а не порциями заданного размера.
        Обычный read(n) ждёт, пока наберётся n байт: событие «кто говорит»
        весит сотни байт, а между событиями проходят минуты — и всё это
        время висело бы в ожидании, а собеседник молчал. read1 отдаёт что
        уже пришло, не блокируясь.
        """
        length = resp.getheader("Content-Length")
        total = int(length) if length else None
        left = total
        chunked = total is None
        reader = getattr(resp, "read1", None)
        try:
            while total is None or left > 0:
                want = CHUNK if (left is None or left > CHUNK) else left
                if reader is not None:
                    data = reader(want)
                else:
                    data = resp.read(want)
                if not data:
                    break
                if chunked:
                    self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()          # сразу, а не когда наберётся
                if left is not None:
                    left -= len(data)
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (OSError, http.client.IncompleteRead, ValueError):
            # нас закрыли или оборвалось соединение: для потока событий это
            # обычное дело — клиент сам переподключится
            self.close_connection = True


class LocalApp:
    """Посредник между браузером на этом компьютере и сервером в сети."""

    def __init__(self, host: str, port: int, secure: bool = True,
                 verify: bool = False):
        self.host = str(host)
        self.port = int(port)
        self.secure = secure
        self.host_port = f"{self.host}:{self.port}"
        self.scheme = "https" if secure else "http"
        self.origin = f"{self.scheme}://{self.host_port}"
        self._verify = verify
        self._server: ThreadingHTTPServer | None = None
        self._ctx = None

    # -- соединение вверх -------------------------------------------------
    def connect(self, path: str) -> http.client.HTTPConnection:
        if not self.secure:
            return http.client.HTTPConnection(self.host, self.port, timeout=30)
        if self._ctx is None:
            ctx = ssl.create_default_context()
            if not self._verify:
                # Сертификат сервера самоподписанный: проверить его нечем,
                # и это ровно то, что человек уже подтверждал руками.
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._ctx = ctx
        # поток событий держится долго и молчит между сообщениями:
        # без этого таймаут оборвал бы его каждые полминуты
        return http.client.HTTPSConnection(self.host, self.port,
                                           context=self._ctx, timeout=None)

    # -- запуск ------------------------------------------------------------
    def start(self) -> str:
        """Поднять и вернуть адрес, который открывать.

        Порт — постоянный (DEFAULT_LOCAL_PORT), и менять его на случайный
        нельзя: браузер путает хранилища разных адресов, а в них лежит
        номер человека и его имя с аватаром. Подробнее — в начале файла.
        """
        if self._server is not None:
            return self.url()
        self._server = self._listen()
        self._server.daemon_threads = True
        self._server.app = self
        threading.Thread(target=self._server.serve_forever,
                         kwargs={"poll_interval": 0.2},
                         daemon=True, name="netspeak-app").start()
        return self.url()

    def _listen(self) -> ThreadingHTTPServer:
        """Занять свободный порт из известного ряда.

        Занятость проверяем не ошибкой при привязке, а попыткой
        соединиться. Потому что сервер на Python разрешает привязку к
        занятому порту (повторное использование адреса) — второй клиент
        на этой же машине поднялся бы на том же адресе, и запросы
        попадали бы то туда, то сюда, неизвестно куда.

        А после аварийного выхода порт ещё какое-то время числится занятым
        из-за ожидающих соединений, и проверка соединением это видит
        правильно: там никто не отвечает. Привязка же сочла бы его
        занятым и сбила бы человека с его номером без причины.

        Если заняты все — берём любой свободный: лучше забытый адрес, чем
        неоткрывшееся окно.
        """
        for порт in range(DEFAULT_LOCAL_PORT, DEFAULT_LOCAL_PORT + LOCAL_PORT_SPREAD):
            if _кто_то_слушает(порт):
                continue
            try:
                return ThreadingHTTPServer(("127.0.0.1", порт), _Handler)
            except OSError:
                continue
        return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)

    def url(self) -> str:
        assert self._server is not None, "посредник не запущен"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except OSError:
            pass
        finally:
            self._server = None