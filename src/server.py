#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NetSpeak — сервер общения по локальной сети (голос + текст).

Запуск:   python server.py
Клиенты:  открывают http://<IP-компьютера>:8765 в браузере.

Интернет не используется вообще: сигналинг идёт через этот локальный
сервер, голос — напрямую между браузерами (WebRTC, только локальные
IP-кандидаты, без STUN/TURN).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

try:  # чтобы русский текст в консоли Windows не ломался
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent

# Переносимое приложение: каталоги settings/, user/, logs/, data/ и веб-файлы
# живут рядом с exe (в собранном виде) или в корне репозитория (из исходников).
sys.path.insert(0, str(ROOT))
try:
    from netspeak import __version__
    from netspeak import db as nsdb
    from netspeak import device as nsdevice
    from netspeak import history as nshistory
    from netspeak import updater as nsupdater
    from netspeak import netinfo, paths as nspaths, profile
except ImportError:  # запуск из постороннего места — работаем на папку сервера
    nspaths = None
    profile = None
    netinfo = None
    nshistory = None
    nsdb = None
    nsdevice = None
    nsupdater = None
    __version__ = "?"

if nspaths:
    nspaths.ensure_dirs()
    profile.ensure_files()

BASE = nspaths.root() if nspaths else ROOT
WEB = nspaths.web_dir() if nspaths else ROOT / "web"
FILES = BASE / "data" / "files"
CERT_DIR = BASE / "data"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def avatar_url(name: str, rev: int = 0) -> str:
    """Ссылка на картинку профиля, если она есть.

    Номер версии в конце — обязателен. Файл называется по постоянному
    номеру человека и перезаписывается на месте, так что адрес не меняется.
    Браузер берёт старую картинку из своей памяти, и человек видит чужой
    аватар, пока не перезагрузит страницу. С ?v= адрес меняется вместе с
    картинкой, и она обновляется сама.
    """
    if not name:
        return ""
    return f"/avatars/{quote(name)}?v={int(rev or 0)}"

SERVER_NAME = os.environ.get("NETSPEAK_NAME", "NetSpeak (локальная сеть)")
PASSWORD = os.environ.get("NETSPEAK_PASSWORD", "")
DEFAULT_PORT = int(os.environ.get("NETSPEAK_PORT", "8765"))
TLS_PORT = int(os.environ.get("NETSPEAK_TLS_PORT", "8766"))
# Голос идёт «каждый с каждым», поэтому 8 человек — это предел, при котором
# разговор в одном канале ещё остаётся лёгким. Больше — уже тяжело.
MAX_USERS = int(os.environ.get("NETSPEAK_MAX_USERS", "8"))

# Браузер сам открывается только по явному желанию. Раньше он открывался
# всегда, если не выключить через NETSPEAK_NO_BROWSER, и при запуске
# NetSpeakServer.exe выскакивал Chrome поверх окна сервера — никто его
# не просил, а закрывать надо руками. Теперь включается обратной
# переменной: NETSPEAK_OPEN_BROWSER=1.
OPEN_BROWSER = os.environ.get("NETSPEAK_OPEN_BROWSER", "") == "1"

CHAT_HISTORY = 150
MAX_NAME = 24
MAX_TEXT = 800
MAX_FILE_SIZE = 32 * 1024 * 1024  # 32 МБ
FILE_TTL = 24 * 3600
STALE_AFTER = 120.0  # секунд без обращений — считаем участника отключившимся

COLORS = [
    "#e05252", "#e08a3a", "#d9b53a", "#8fce4a", "#3fc79a",
    "#3fb0e0", "#6a83e0", "#a86ae0", "#e05fa8", "#8b98a9",
]

DEFAULT_CHANNELS = [
    {"id": "general", "name": "Общий", "emoji": "💬", "about": "Просто поболтать"},
    {"id": "games", "name": "Игры", "emoji": "🎮", "about": "Играем и общаемся"},
    {"id": "work", "name": "Работа", "emoji": "💼", "about": "Дела и обсуждения"},
]

_SAFE_NAME = re.compile(r"[^\w\-. ]+", re.UNICODE)


def clean_name(raw: str, fallback: str = "Гость") -> str:
    name = (raw or "").replace("\n", " ").replace("\r", " ").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    return name[:MAX_NAME]


def stable_hash(text: str) -> int:
    """Хэш строки, одинаковый при каждом запуске сервера.

    Встроенный hash() для строк меняет значение при каждом запуске
    процесса: включено рандомизированное зерно. Из-за этого цвет
    участника менялся бы после каждой перезагрузки сервера.
    """
    value = 0
    for ch in (text or ""):
        value = (value * 131 + ord(ch)) & 0xFFFFFFFF
    return value


def safe_filename(raw: str) -> str:
    name = _SAFE_NAME.sub("_", (raw or "").strip()) or "file"
    return name[:90]


# --------------------------------------------------------------------------
# Участники
# --------------------------------------------------------------------------

class Member:
    __slots__ = ("id", "uid", "name", "channel", "color", "muted", "deafened",
                 "sharing", "ping", "since", "queue", "closed", "budget", "_t",
                 "last_seen", "avatar", "avatar_rev", "showCircle")

    def __init__(self, mid: str, name: str, channel: str, color: str,
                 uid: str = ""):
        self.id = mid
        # Постоянный идентификатор человека. id — это соединение: оно умирает
        # вместе с окном и каждый раз новое. uid переживает перезапуск, и по
        # нему мы узнаём, что «это тот же самый человек» — иначе в сохранённой
        # переписке собственные сообщения выглядели бы чужими.
        self.uid = uid
        self.name = name
        self.channel = channel
        self.color = color
        self.muted = False
        self.deafened = False
        self.sharing = False
        self.ping = 0
        self.since = time.time()
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        self.closed = False
        self.budget = 12.0  # простенький антиспам (сообщений в секунду)
        self._t = time.time()
        self.last_seen = time.time()
        self.avatar = ""      # имя файла аватара в user/avatars/
        self.avatar_rev = 0   # номер версии: растёт при каждой загрузке картинки
        self.showCircle = True  # показывать ли цветной кружок

    def view(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.name,
            "channel": self.channel,
            "color": self.color,
            "muted": self.muted,
            "deafened": self.deafened,
            "sharing": self.sharing,
            "ping": int(self.ping),
            "since": int(self.since),
            "avatar": avatar_url(self.avatar, self.avatar_rev),
            "showCircle": self.showCircle,
        }


# --------------------------------------------------------------------------
# Хаб: комнаты, чат, рассылка событий
# --------------------------------------------------------------------------

class Hub:
    def __init__(self):
        self.lock = threading.RLock()
        self.members: dict[str, Member] = {}
        self.log: list[dict] = []
        self.channels: list[dict] = list(DEFAULT_CHANNELS)
        self.db = self._open_db()
        # поле названо store, а не history: у Hub уже есть метод history(),
        # и поле с тем же именем тихо его затирало — при входе участника
        # падало «'ChatHistory' object is not callable»
        self.store = self._open_history()

    # -- база --------------------------------------------------------------
    @staticmethod
    def _open_db():
        """Открывает data/netspeak.db. None — работаем без неё."""
        if nspaths is None or nsdb is None:
            return None
        try:
            store = nsdb.Store(nspaths.data_dir() / "netspeak.db")
            return store if store.open() else None
        except Exception:
            return None

    def profile_of(self, uid: str) -> dict:
        """Кто этот человек по базе — имя и аватар, которые видят все."""
        if self.db is None or not uid:
            return {}
        try:
            return self.db.user(uid) or {}
        except Exception:
            return {}

    @staticmethod
    def _open_history():
        """Прежнее хранилище в history/. None, если папки нет.

        Осталось как подстраховка: база основная, а эти файлы ещё пишутся
        и при первом запуске переносятся в базу целиком.
        """
        if nspaths is None or nshistory is None:
            return None
        try:
            return nshistory.ChatHistory(nspaths.history_dir())
        except Exception:
            return None

    def remember_user(self, uid: str, name: str, avatar: str,
                      show_circle: bool, color: str) -> None:
        """Записать человека в базу — по постоянному uid."""
        if self.db is None or not uid:
            return
        try:
            self.db.upsert_user(uid, name, avatar, show_circle, color)
        except Exception:
            pass

    def remember_user_by_id(self, sid: str, name: str, avatar: str,
                            show_circle: bool, color: str) -> None:
        """Записать человека без uid — по id соединения.

        Так бывает, когда клиент очень старый и uid не присылает. Чтобы
        запись не перетиралась, ключ берём с префиксом: настоящие uid
        всегда hexadecimal, а этот — с чёрточкой.
        """
        if self.db is None:
            return
        self.remember_user("id:" + sid, name, avatar, show_circle, color)

    def profile_by_id(self, sid: str, uid: str) -> dict:
        """Профиль по uid, а если его нет — по id соединения."""
        if self.db is None:
            return {}
        try:
            if uid:
                found = self.db.user(uid)
                if found:
                    return found
            return self.db.user("id:" + sid) or {}
        except Exception:
            return {}

    def _remember(self, message: dict) -> None:
        """Положить сообщение в журнал и в файл истории.

        Одно место вместо пяти: раньше журнал пополнялся вручную в пяти
        местах, и одно забытое означало бы «сообщение есть у всех, но в
        файле его нет».
        """
        self.log.append(message)
        self.log[:] = self.log[-CHAT_HISTORY:]
        # База — постоянное хранилище. Файл рядом тоже пишем: он остался
        # с прошлой версии, и пока база наполняется при первом запуске,
        # подстраховка не помешает.
        if self.db is not None:
            self.db.add_message(message)
        if self.store is not None:
            self.store.append(message.get("channel") or "general", message)

    def load_history(self) -> int:
        """Вернуть сохранённую переписку в журнал при старте сервера."""
        # Сперва переносим то, что осталось в файлах от прежней версии.
        if self.db is not None and self.store is not None:
            try:
                self.store.prune()
                self.db.import_jsonl(nspaths.history_dir())
            except Exception:
                pass

        if self.db is not None:
            try:
                messages = self.db.messages(limit=CHAT_HISTORY)
            except Exception:
                messages = []
            if messages:
                with self.lock:
                    self.log[:] = (messages + self.log)[-CHAT_HISTORY:]
                return len(messages)

        if self.store is None:
            return 0
        try:
            messages = self.store.read_all()
        except OSError:
            return 0
        if not messages:
            return 0
        with self.lock:
            # прошлое не вытесняет то, что уже накопилось в этом запуске
            self.log[:] = (messages + self.log)[-CHAT_HISTORY:]
        return len(messages)

    def prune_history(self) -> int:
        """Подрезать старые сообщения. Возвращает, сколько убрано."""
        cut = 0
        if self.db is not None:
            try:
                cut += self.db.prune()
            except Exception:
                pass
        if self.store is not None:
            try:
                cut += self.store.prune()
            except OSError:
                pass
        return cut

    def history_stats(self) -> dict:
        if self.db is not None:
            try:
                counts = self.db.counts()
                return {
                    "dir": counts.get("path", ""),
                    "users": counts.get("users", 0),
                    "channels": {},
                    "messages": counts.get("messages", 0),
                }
            except Exception:
                pass
        if self.store is None:
            return {"dir": "", "channels": {}, "messages": 0}
        try:
            return self.store.stats()
        except OSError:
            return {"dir": "", "channels": {}, "messages": 0}

    # -- служебное ---------------------------------------------------------
    def channel_ids(self) -> set[str]:
        return {c["id"] for c in self.channels}

    def public_channels(self) -> list[dict]:
        counts: dict[str, int] = {}
        with self.lock:
            for m in self.members.values():
                counts[m.channel] = counts.get(m.channel, 0) + 1
        return [{**c, "count": counts.get(c["id"], 0)} for c in self.channels]

    def occupancy(self) -> dict:
        with self.lock:
            online = len(self.members)
        return {"online": online, "maxUsers": MAX_USERS,
                "free": max(0, MAX_USERS - online)}

    def public_users(self) -> list[dict]:
        with self.lock:
            users = sorted(self.members.values(), key=lambda m: (m.channel, m.since))
            members = list(users)

        # Человек мог уйти, а другой прийти — имя и аватар всё равно
        # прежние. Раньше каждый держал свой аватар у себя в браузере,
        # и чужие аватары были видны только тем, кто в сети одновременно.
        known: dict[str, dict] = {}
        if self.db is not None:
            try:
                known = self.db.users()
            except Exception:
                known = {}

        out = []
        for m in members:
            view = m.view()
            rec = known.get(m.uid) or known.get("id:" + m.id)
            if rec:
                # Имя и аватар берём у того, кто сейчас в сети: он свежее.
                # Раньше было наоборот, и выходило так, что человек
                # переименовался, а все продолжали видеть старое имя —
                # запись в базе перебивала живое состояние.
                if rec.get("avatar") and not view.get("avatar"):
                    view["avatar"] = avatar_url(rec["avatar"], rec.get("avatarRev"))
                    view["avatarRev"] = rec.get("avatarRev", 0)
                if not view.get("name") and rec.get("name"):
                    view["name"] = rec["name"]
                if not view.get("color") and rec.get("color"):
                    view["color"] = rec["color"]
            out.append(view)
        return out

    def known_profiles(self) -> list[dict]:
        """Все, кто когда-либо заходил: имя, аватар, когда видели.

        Нужен, чтобы показывать чужих и в переписке, даже если их сейчас
        нет в сети.
        """
        if self.db is None:
            return []
        try:
            people = self.db.users()
        except Exception:
            return []
        return sorted(
            (dict(p, avatar=avatar_url(p["avatar"], p.get("avatarRev"))
                        if p.get("avatar") else "")
             for p in people.values()),
            key=lambda p: p.get("lastSeen") or 0,
            reverse=True,
        )

    # -- отправка ---------------------------------------------------------
    @staticmethod
    def _push(member: Member, event: dict) -> None:
        if not member.closed:
            member.queue.put(event)

    def push(self, member_id: str, event: dict) -> None:
        with self.lock:
            member = self.members.get(member_id)
        if member:
            self._push(member, event)

    def broadcast(self, event: dict, exclude: str | None = None) -> None:
        with self.lock:
            targets = [m for m in self.members.values() if m.id != exclude]
        for m in targets:
            self._push(m, event)

    def broadcast_state(self) -> None:
        event = {"e": "users", "users": self.public_users(),
                 "channels": self.public_channels()}
        self.broadcast(event)

    # -- участники --------------------------------------------------------
    def join(self, sid: str, name: str, channel: str,
            avatar: str = "", show_circle: bool = True,
            uid: str = "") -> Member:
        with self.lock:
            old = self.members.get(sid)
            if old is not None:  # переподключение того же клиента
                old.closed = True
                old.queue.put(None)
                self.members.pop(sid, None)
                self.broadcast_state()

            # Цвет берём от постоянного uid, а не от sid: иначе после
            # перезапуска клиента человек вдруг менял бы цвет.
            color = COLORS[stable_hash(uid or sid) % len(COLORS)]
            member = Member(sid, name, channel, color, uid=uid)
            # Аватар не берём из того, что прислал клиент. Он присылает
            # ссылку вида /avatars/имя.png, а здесь нужно имя файла: со
            # сравнением на косую черту такая ссылка молча отбрасывалась, и
            # картинка не доходила ни при входе, ни при смене.
            if uid:
                rec = self.profile_by_id("", uid)
                member.avatar = str(rec.get("avatar") or "")
                member.avatar_rev = int(rec.get("avatarRev") or 0)
            elif len(avatar) <= 64 and "/" not in avatar and "\\" not in avatar \
                    and ".." not in avatar:
                member.avatar = avatar
            member.showCircle = bool(show_circle)
            self.members[sid] = member

            # Запоминаем человека: имя и аватар, чтобы их видели другие.
            # Свою строку человек перезаписывает сам — это его данные.
            # Аватар берём member.avatar, то есть уже поднятый из базы:
            # клиент его больше не присылает, и с пустым значением запись
            # стирала бы картинку при каждом входе.
            if uid:
                self.remember_user(uid, name, member.avatar, show_circle, color)
            else:
                self.remember_user_by_id(sid, name, member.avatar,
                                         show_circle, color)

            sys_msg = {
                "kind": "system", "channel": channel, "name": "",
                "text": f"{name} подключился к NetSpeak", "ts": int(time.time()),
            }
            self._remember(sys_msg)

        self.broadcast_state()
        self.broadcast({"e": "chat", "message": sys_msg}, exclude=sid)
        return member

    def leave(self, sid: str, member: "Member | None" = None) -> None:
        """Убирает участника. member — защита от «выбить нового при переподключении»."""
        with self.lock:
            current = self.members.get(sid)
            if current is None or (member is not None and current is not member):
                return
            self.members.pop(sid, None)
            current.closed = True
            current.queue.put(None)
            sys_msg = {
                "kind": "system", "channel": current.channel, "name": "",
                "text": f"{current.name} вышел из сети", "ts": int(time.time()),
            }
            self._remember(sys_msg)
        self.broadcast_state()
        self.broadcast({"e": "chat", "message": sys_msg})

    def update(self, sid: str, data: dict) -> None:
        """Обновляет состояние участника. Канал меняем только при реальной смене."""
        changed = False
        sys_msg = None
        member_uid = ""
        # что именно человек поменял — записать это в базу, чтобы не потерять
        renamed = avatar_touched = circle_touched = False
        with self.lock:
            member = self.members.get(sid)
            if member is None:
                return
            member.last_seen = time.time()
            member_uid = member.uid or ""

            if "muted" in data and bool(data["muted"]) != member.muted:
                member.muted = bool(data["muted"])
                changed = True
            if "name" in data:
                want_name = clean_name(str(data["name"] or ""), member.name)
                if want_name != member.name:
                    member.name = want_name
                    changed = True
                    renamed = True
            if "deafened" in data:
                want = bool(data["deafened"])
                if want != member.deafened:
                    member.deafened = want
                    changed = True
                if want:
                    if not member.muted:
                        member.muted = True
                        changed = True
            if "sharing" in data and bool(data["sharing"]) != member.sharing:
                member.sharing = bool(data["sharing"])
                changed = True
            if "ping" in data:
                member.ping = min(9999, max(0, int(data["ping"] or 0)))

            # аватар и «показывать кружок» — часть профиля, шлём при смене
            if "avatar" in data and member_uid:
                # Как и при входе: доверяем базе, а не присланной ссылке.
                # Раньше ссылка с косой чертой отбрасывалась, и картинка
                # пропадала при первом же изменении профиля.
                rec = self.profile_by_id("", member_uid)
                fresh = str(rec.get("avatar") or "")
                member.avatar_rev = int(rec.get("avatarRev") or 0)
                if fresh != member.avatar:
                    member.avatar = fresh
                    changed = True
                    avatar_touched = True
            if "showCircle" in data and bool(data["showCircle"]) != member.showCircle:
                member.showCircle = bool(data["showCircle"])
                changed = True
                circle_touched = True

            if "channel" in data and data["channel"] in self.channel_ids():
                if member.channel != data["channel"]:
                    member.channel = data["channel"]
                    changed = True
                    sys_msg = {
                        "kind": "system", "channel": member.channel, "name": "",
                        "text": f"{member.name} перешёл в «"
                                 f"{self.channels_name(member.channel)}»",
                        "ts": int(time.time()),
                    }
                    self._remember(sys_msg)

        # Своё имя и аватар человек хозяин: сохраняем, чтобы вернуть их,
        # даже если он выйдет, а потом вернётся с почищенным браузером.
        if member_uid and (renamed or avatar_touched or circle_touched):
            self.remember_user(member_uid, member.name, member.avatar,
                               member.showCircle, member.color)

        if sys_msg:
            self.broadcast({"e": "chat", "message": sys_msg})
        if changed:
            self.broadcast_state()

    def channels_name(self, cid: str) -> str:
        for c in self.channels:
            if c["id"] == cid:
                return c["name"]
        return cid

    def take_budget(self, sid: str) -> bool:
        """Возвращает False, если клиент слишком часто пишет."""
        now = time.time()
        with self.lock:
            member = self.members.get(sid)
            if member is None:
                return False
            member.budget = min(12.0, member.budget + (now - member._t))
            member._t = now
            if member.budget < 1:
                return False
            member.budget -= 1
            return True

    # -- чат --------------------------------------------------------------
    def chat(self, sid: str, text: str, to: str | None) -> dict | None:
        text = (text or "").replace("\r", "").strip()
        if not text or not self.take_budget(sid):
            return None
        with self.lock:
            member = self.members.get(sid)
            if member is None:
                return None
            message = {
                "kind": "user" if not to else "dm",
                "from": member.id,
                "uid": member.uid,
                "name": member.name,
                "color": member.color,
                "channel": member.channel,
                "to": to,
                "text": text[:MAX_TEXT],
                "ts": int(time.time()),
            }
            if not to:
                self._remember(message)
        if to:
            self.push(to, {"e": "chat", "message": message})
            self.push(sid, {"e": "chat", "message": message})
        else:
            self.broadcast({"e": "chat", "message": message})
        return message

    def file_message(self, sid: str, filename: str, size: int, file_id: str) -> dict:
        with self.lock:
            member = self.members[sid]
            message = {
                "kind": "file", "from": member.id, "uid": member.uid,
                "name": member.name,
                "color": member.color, "channel": member.channel, "to": None,
                "fileId": file_id, "filename": filename, "size": size,
                "text": "", "ts": int(time.time()),
            }
            self._remember(message)
        self.broadcast({"e": "chat", "message": message})
        return message

    def history(self, channel: str | None = None) -> list[dict]:
        """Переписка для показа при входе.

        С каналом отдаём только его — человеку в «Общем» не нужно
        получать содержимое «Работы», даже если клиент всё равно отфильтрует
        его при отрисовке.
        """
        with self.lock:
            items = list(self.log)
        if channel is None:
            return items
        return [m for m in items if not m.get("to") and m.get("channel") == channel]


HUB = Hub()


# --------------------------------------------------------------------------
# HTTP + Server-Sent Events
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NetSpeak"
    sys_version = ""

    # -- утилиты ----------------------------------------------------------
    def log_message(self, *args):  # тихий сервер
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _same_machine(self) -> bool:
        """Запрос пришёл с этого же компьютера или с чужого.

        Смотрим на адрес, с которого пришёл запрос, и сравниваем с нашими
        собственными. Если совпало — клиент запущен на серверной машине,
        и ему можно дать номер устройства. Иначе он пришёл по сети, и
        номер машины сервера ему не подходит никак.
        """
        peer = self.client_address[0] if self.client_address else ""
        if not peer or peer in ("127.0.0.1", "::1", "localhost"):
            return True
        mine = {"127.0.0.1", "::1"}
        if netinfo:
            try:
                mine |= set(netinfo.all_ips())
            except Exception:
                pass
        else:
            mine.add(local_ip())
        return peer in mine

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > MAX_FILE_SIZE * 2:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _origin_ok(self) -> bool:
        """Простая защита: запросы должны приходить из нашей же страницы."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlparse(origin).netloc
        return host in (self.headers.get("Host"), "")

    # -- профиль пользователя ---------------------------------------------
    def _save_profile(self):
        data = self._read_json()
        if not isinstance(data, dict):
            self._send_json({"ok": False, "error": "плохой запрос"}, 400)
            return

        uid = str(data.get("uid") or "")[:64]
        if uid and HUB.db is not None:
            # Человек записал своё — запоминаем по его номеру, чтобы
            # остальные видели его имя и аватар. Это его строка, и он её
            # хозяин.
            rec = HUB.profile_by_id("", uid)
            name = str(data.get("name") or "") or rec.get("name", "")
            avatar = str(data.get("avatar") or "") or rec.get("avatar", "")
            if name or avatar:
                HUB.remember_user(uid, name, avatar,
                                  data.get("showCircle") is not False,
                                  rec.get("color", ""))

        # В файл user/profile.json попадает только то, что пришло с этой
        # же машины. Файл один, а людей в сети много: раньше переименование
        # на ноутбуке молча затирало имя того, кто запустил сервер.
        if uid and not self._same_machine():
            self._send_json({"ok": True, "uid": uid,
                             "avatarUrl": avatar_url(str(data.get("avatar") or "")),
                             "name": str(data.get("name") or ""),
                             "showCircle": data.get("showCircle") is not False})
            return

        # Пустое значение не затирает уже сохранённое. Иначе клиент, у
        # которого профиль ещё не подгрузился (или открылся в другой папке),
        # молча стирал бы имя и картинку — и человек терял их без причины.
        current = profile.load_user()
        for key in ("name", "avatar"):
            if not data.get(key) and current.get(key):
                data[key] = current[key]
        data.pop("uid", None)
        saved = profile.save_user(data)
        self._send_json({**saved, "avatarUrl": avatar_url(saved.get("avatar")),
                         "ok": True})

    def _save_avatar(self):
        """Принимает картинку как data:URL прямо в JSON — так проще, чем
        отдельная загрузка файлом."""
        data = self._read_json()
        if not isinstance(data, dict):
            self._send_json({"ok": False, "error": "плохой запрос"}, 400)
            return
        raw = str(data.get("image") or "")
        if not raw.startswith("data:"):
            self._send_json({"ok": False, "error": "нужна картинка"}, 400)
            return
        head, _, blob = raw.partition(",")
        ext = ".png"
        if "jpeg" in head or "jpg" in head:
            ext = ".jpg"
        elif "webp" in head:
            ext = ".webp"
        elif "gif" in head:
            ext = ".gif"
        try:
            content = base64.b64decode(blob, validate=True)
        except Exception:
            self._send_json({"ok": False, "error": "не похоже на картинку"}, 400)
            return
        if not content or len(content) > 2 * 1024 * 1024:
            self._send_json({"ok": False, "error": "картинка больше 2 МБ"}, 400)
            return

        uid = str(data.get("uid") or "")[:64]
        old = profile.load_user().get("avatar") or ""
        # имя выводим из uid: у каждого человека оно своё, и чужие
        # аватары больше не могут затереть друг друга
        name = profile.avatar_name(uid, ext) if uid else uuid.uuid4().hex[:12] + ext
        target = profile.avatar_file(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        same_machine = self._same_machine()

        if old and old != name:
            # Прежний аватар убираем — но только свой. Раньше удаление шло
            # безусловно, и второй загрузивший стирал картинку первого:
            # на сервере profile.json один на всех.
            owner = profile.avatar_owner(old)
            mine_here = same_machine or not uid
            if (not owner and mine_here) or (owner and uid and owner == uid):
                try:
                    profile.avatar_file(old).unlink()
                except OSError:
                    pass
        if uid and HUB.db is not None:
            # сразу показываем этот аватар всем остальным
            rec = HUB.profile_by_id("", uid)
            HUB.remember_user(uid, rec.get("name") or profile.load_user().get("name") or "",
                              name, profile.load_user().get("showCircle", True),
                              rec.get("color", ""))
        # Общий файл профиля правим только с этой машины: файл один, а
        # загрузить аватар может кто угодно из сети.
        saved = (profile.save_user({"avatar": name}) if same_machine
                 else profile.load_user())
        # Новая картинка — новая версия: без неё адрес не изменится и
        # браузер продолжит показывать прежнюю.
        rev = 0
        if uid and HUB.db is not None:
            rev = HUB.db.bump_avatar(uid, name)
            rec = HUB.profile_by_id("", uid)
            if rec.get("avatar") != name:
                # картинку записали, а строка не обновилась — не молчим
                HUB.remember_user(uid, rec.get("name") or name, name,
                                  rec.get("showCircle", True), rec.get("color", ""))
        self._send_json({**saved, "avatar": name, "avatarRev": rev,
                         "avatarUrl": avatar_url(name, rev), "ok": True})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not self._origin_ok():
            self.send_error(403)
            return
        if parsed.path == "/api/avatar":
            old = profile.load_user().get("avatar") or ""
            query_uid = (parse_qs(urlparse(self.path).query).get("uid") or [""])[0]
            profile.save_user({"avatar": ""})
            # снимаем аватар только у того, кто им владеет: иначе один
            # человек обнулил бы картинку другого, просто нажав «убрать»
            owner = profile.avatar_owner(old)
            if old and (not owner or not query_uid or owner == query_uid):
                try:
                    profile.avatar_file(old).unlink()
                except OSError:
                    pass
            self._send_json({**profile.load_user(), "avatarUrl": "", "ok": True})
            return
        self.send_error(404)

    def _serve_avatar(self, name: str):
        path = profile.avatar_file(name)
        try:  # имя уже почищено, но подстрахуемся от выхода из папки
            path.resolve().relative_to(profile.paths.avatars_dir().resolve())
        except (ValueError, OSError):
            self.send_error(404)
            return
        if not path.is_file():
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, rel: str):
        path = (WEB / rel.lstrip("/")).resolve()
        try:
            path.relative_to(WEB.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not path.is_file():
            self.send_error(404)
            return
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = path.read_bytes()
        if path.suffix in (".html", ".js"):
            data = add_version(data)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # без этого браузер годами держит старые файлы и «не показывает изменения»
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, file_id: str, filename: str):
        path = (FILES / f"{file_id}__{safe_filename(filename)}").resolve()
        try:
            path.relative_to(FILES.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(safe_filename(filename))}",
        )
        self.end_headers()
        self.wfile.write(data)

    # -- маршруты ---------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            self._serve_static("index.html")
        elif route.startswith("/files/"):
            # путь вида /files/<id>/<имя файла>; имя может содержать «/»
            file_id, _, filename = route[len("/files/"):].partition("/")
            if not filename:
                self.send_error(404)
            else:
                self._serve_file(file_id, unquote(filename))
        elif route == "/api/info":
            self._send_json({
                "server": SERVER_NAME,
                "requiresPassword": bool(PASSWORD),
                # версия сервера: страница показывает её рядом с версией
                # клиента, и сразу видно, чья это сборка и не устарела ли
                "version": __version__,
                "channels": HUB.public_channels(),
                "people": HUB.known_profiles(),
                "addresses": addresses(self.server.server_address[1]),
                "version": 1,
                **HUB.occupancy(),
            })
        elif route == "/api/device":
            # Кто я и с какого устройства. Отдаём номер машины только тем,
            # кто обратился с самого этого компьютера: чужой машине наш
            # номер не нужен, а раздавать его всем — значило бы сделать
            # всех одним и тем же человеком.
            ident = nsdevice.identity() if nsdevice else {
                "machine": "", "seed": "", "bound": False, "name": ""}
            self._send_json({
                **ident,
                "sameMachine": self._same_machine(),
            })
        elif route == "/api/profile":
            # настройки пользователя этого компьютера: имя, аватар, кружок
            self._send_json({
                **profile.load_user(),
                "avatarUrl": avatar_url(profile.load_user().get("avatar")),
            })
        elif route == "/api/appsettings":
            self._send_json(profile.load_app())
        elif route.startswith("/avatars/"):
            self._serve_avatar(unquote(route[len("/avatars/"):]))
        elif route == "/api/events":
            if not self._origin_ok():
                self.send_error(403)
                return
            self._events(query)
        else:
            self._serve_static(route)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._origin_ok():
            self.send_error(403)
            return
        route = parsed.path

        # профиль и настройки не требуют входа: ими пользуется сам браузер
        # до подключения к комнате
        if route == "/api/profile":
            self._save_profile()
            return
        if route == "/api/appsettings":
            data = self._read_json() or {}
            self._send_json(profile.save_app(data if isinstance(data, dict) else {}))
            return
        if route == "/api/avatar":
            self._save_avatar()
            return

        data = self._read_json()
        if data is None:
            self._send_json({"ok": False, "error": "плохой запрос"}, 400)
            return
        sid = str(data.get("sid") or "")
        if not HUB.members.get(sid):
            self._send_json({"ok": False, "error": "сессия не найдена, войдите заново"}, 401)
            return
        HUB.update(sid, data)

        kind = data.get("type")
        if kind == "upload":
            try:
                message = upload_file(
                    sid, str(data.get("filename") or "file"),
                    str(data.get("data") or ""),
                )
                self._send_json({"ok": True, "message": message})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            return
        if kind == "chat":
            message = HUB.chat(sid, str(data.get("text", "")), data.get("to") or None)
            self._send_json({"ok": bool(message),
                             "error": "слишком много сообщений" if not message else ""})
        elif kind == "signal":
            to = str(data.get("to") or "")
            if to and to != sid and HUB.members.get(to):
                HUB.push(to, {"e": "signal", "from": sid, "data": data.get("data") or {}})
            self._send_json({"ok": True})
        elif kind == "ping":
            self._send_json({"ok": True, "e": "pong", "t": data.get("t")})
        elif kind == "leave":
            HUB.leave(sid)
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": True})

    # -- поток событий ----------------------------------------------------
    def _events(self, query):
        sid = (query.get("sid") or [""])[0][:64] or uuid.uuid4().hex[:16]
        name = clean_name((query.get("name") or [""])[0])
        channel = (query.get("channel") or [HUB.channels[0]["id"]])[0]
        password = (query.get("pw") or [""])[0]

        if PASSWORD and password != PASSWORD:
            self._send_json({"ok": False, "error": "Неверный пароль сервера"}, 403)
            return
        if channel not in HUB.channel_ids():
            channel = HUB.channels[0]["id"]

        # проверяем лимит до регистрации; вернувшийся участник место не занимает
        with HUB.lock:
            already_here = sid in HUB.members
            full = (not already_here) and len(HUB.members) >= MAX_USERS

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def write(payload: dict) -> bool:
            try:
                data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                return False

        if full:
            # отвечаем событием, а не кодом ошибки: EventSource не умеет читать тело
            write({"e": "full", **HUB.occupancy()})
            self.close_connection = True
            return

        member = HUB.join(
            sid, name, channel,
            avatar=(query.get("avatar") or [""])[0],
            show_circle=(query.get("circle") or ["1"])[0] != "0",
            uid=(query.get("uid") or [""])[0][:64],
        )

        write({
            "e": "welcome",
            "you": member.view(),
            "channels": HUB.public_channels(),
            "users": HUB.public_users(),
            # все, кто бывал на сервере, с именами и аватарами: без этого
            # чужие аватары были видны только тем, кто в сети одновременно
            "people": HUB.known_profiles(),
            "chat": HUB.history(member.channel),
            "server": {"name": SERVER_NAME, "maxUsers": MAX_USERS},
        })

        try:
            while True:
                try:
                    event = member.queue.get(timeout=12)
                except queue.Empty:
                    try:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
                    continue
                if event is None:
                    break
                if not write(event):
                    break
        finally:
            HUB.leave(sid, member)


# --------------------------------------------------------------------------
# Сеть, запуск
# --------------------------------------------------------------------------

_ASSETS = ('"/styles.css"', '"/js/app.js"', '"./voice.js"')


def web_version() -> str:
    """Отпечаток файлов интерфейса: меняется при любой правке.

    Нужен, чтобы браузер гарантированно забирал свежие файлы, даже если он
    по какой-то причине игнорирует no-store.
    """
    newest = 0
    try:
        for p in WEB.rglob("*"):
            if p.is_file():
                newest = max(newest, p.stat().st_mtime)
    except Exception:
        pass
    return str(int(newest))


def add_version(data: bytes) -> bytes:
    """Дописывает ?v=... к ссылкам на стили и скрипты (включая импорты)."""
    ver = web_version().encode()
    for asset in _ASSETS:
        data = data.replace(asset.encode(), asset[:-1].encode() + b"?v=" + ver + b'"')
    return data


def local_ip() -> str:
    return netinfo.local_ip() if netinfo else "127.0.0.1"


def addresses(port: int, secure: bool = False) -> list[str]:
    """Адреса, по которым к серверу можно обратиться.

    Показываем только реальные сетевые карты: виртуальные адреса из
    Hyper-V или VMware в журнал попадать не должны, в них никто не ходит.
    """
    scheme = "https" if secure else "http"
    if netinfo:
        ips = [ip for _, ip in netinfo.local_addresses()]
    else:
        ips = [local_ip()]
    if not ips:
        return [f"{scheme}://localhost:{port}"]
    return [f"{scheme}://{ip}:{port}" for ip in ips]


def announce_client_updates() -> None:
    """Раз в час говорить участникам, что вышел свежий клиент.

    Объявление и только объявление. Файла здесь нет и быть не должно:
    участники обновляются сами, с GitHub. Иначе сервер стал бы тем, кто
    раздаёт exe, — а на этой сети сервер может поднять кто угодно, и
    раздавать ему файлы было бы худшей идеей на свете.

    Говорим один раз на релиз: повторять то же самое раз в час незачем,
    люди от этого только закаляются. А кто уже на этой версии, тот
    получит объявление молча и ничего не сделает — сравнивать умеет сам
    клиент.
    """
    if nsupdater is None:
        return
    told = ""          # какой тег уже объявляли
    while True:
        try:
            rel = nsupdater.latest("client")
            if rel is not None and rel.tag != told:
                told = rel.tag
                HUB.broadcast({
                    "e": "update-available",
                    "version": rel.version,
                    "tag": rel.tag,
                    "url": rel.url,
                })
        except Exception:
            pass          # неудачный запрос — не повод замолчать навсегда
        time.sleep(nsupdater.CLIENT_NOTICE_SECONDS)


def cleanup_files():
    while True:
        time.sleep(3600)
        try:
            now = time.time()
            for path in FILES.glob("*"):
                if now - path.stat().st_mtime > FILE_TTL:
                    path.unlink(missing_ok=True)
        except Exception:
            pass


def reaper():
    """Убирает участников, которые замолчали (например, ноутбук уснул).

    Обычно клиент отключается сам и мы узнаём об этом по сокету, но при
    «зависшем» TCP (сон, сеть пропала) сокет молчит часами — нужен запасной
    вариант по времени последнего обращения.
    """
    while True:
        time.sleep(20)
        now = time.time()
        with HUB.lock:
            stale = [m for m in HUB.members.values()
                     if now - m.last_seen > STALE_AFTER]
        for m in stale:
            HUB.leave(m.id, m)


def upload_file(sid: str, filename: str, data_b64: str) -> dict:
    member = HUB.members.get(sid)
    if member is None:
        raise ValueError("сессия не найдена")
    raw = base64.b64decode(data_b64, validate=True)
    if len(raw) > MAX_FILE_SIZE:
        raise ValueError(f"файл больше {MAX_FILE_SIZE // (1024 * 1024)} МБ")
    if not HUB.take_budget(sid):
        raise ValueError("слишком часто")
    file_id = uuid.uuid4().hex[:12]
    FILES.mkdir(parents=True, exist_ok=True)
    path = FILES / f"{file_id}__{safe_filename(filename)}"
    path.write_bytes(raw)
    return HUB.file_message(sid, safe_filename(filename), len(raw), file_id)


def find_openssl() -> str | None:
    """Ищем openssl: без него не выпустить сертификат для HTTPS."""
    found = shutil.which("openssl")
    if found:
        return found
    for path in (
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
        r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
        r"C:\Program Files\OpenSSL\bin\openssl.exe",
        "/usr/bin/openssl",
        "/usr/local/bin/openssl",
    ):
        if os.path.isfile(path):
            return path
    return None


def ensure_cert():
    """Готовит самоподписанный сертификат.

    Он нужен потому, что браузер даёт доступ к микрофону только в защищённом
    контексте: по обычному HTTP из локальной сети функции попросту нет.
    """
    if CERT_FILE.is_file() and KEY_FILE.is_file():
        return CERT_FILE, KEY_FILE

    exe = find_openssl()
    if not exe:
        return None

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    # Сертификату нужны ВСЕ адреса, включая виртуальные: выписывается он
    # один раз, и пропущенного адреса потом не добавить.
    if netinfo:
        ips = set(netinfo.all_ips())
    else:
        ips = {local_ip(), "127.0.0.1"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.add(info[4][0])
        except OSError:
            pass
    san = ",".join("IP:" + ip for ip in sorted(ips))
    san += ",DNS:localhost"

    cmd = [
        exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
        "-days", "3650", "-subj", "/CN=NetSpeak",
        "-addext", "subjectAltName=" + san,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except Exception as exc:
        detail = getattr(exc, "stderr", None) or b""
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        print("  Не удалось выпустить сертификат:", str(detail)[:200])
        return None
    if CERT_FILE.is_file() and KEY_FILE.is_file():
        return CERT_FILE, KEY_FILE
    return None


class RedirectHandler(Handler):
    """С обычного порта отправляет на защищённый.

    Исключение — обращения к самому себе (127.0.0.1): им открывается окно
    приложения. Отдаём ему всё как есть, без TLS. Браузер считает
    127.0.0.1 защищённым контекстом, поэтому микрофон работает, а
    предупреждения о самоподписанном сертификате не появляется вовсе.
    """

    tls_port = 0

    def _is_local(self) -> bool:
        host = self.headers.get("Host", "").split(":")[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _redirect(self) -> None:
        host = self.headers.get("Host", "").split(":")[0] or "localhost"
        self.send_response(301)
        self.send_header("Location", "https://" + host + ":" + str(self.tls_port) + self.path)
        # редирект нельзя кешировать: иначе браузер застрянет на старом порту
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # Каждый метод вызывает свой же метод Handler. Если отдать все запросы
    # в do_GET, то POST /api/send ушёл бы по GET-маршрутизации и вернул 404:
    # в окне приложения молча сломались бы чат, сигналинг и пинг.
    def do_GET(self):
        if self._is_local():
            Handler.do_GET(self)
        else:
            self._redirect()

    def do_POST(self):
        if self._is_local():
            Handler.do_POST(self)
        else:
            self._redirect()

    def do_PUT(self):
        if self._is_local():
            Handler.do_PUT(self)
        else:
            self._redirect()

    def do_DELETE(self):
        if self._is_local():
            Handler.do_DELETE(self)
        else:
            self._redirect()

    def do_HEAD(self):
        if self._is_local():
            Handler.do_HEAD(self)
        else:
            self._redirect()


def start(port: int):
    FILES.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=cleanup_files, daemon=True).start()
    # объявляем участникам о новых версиях клиента; сначала не сразу,
    # чтобы не мешать запуску сервера
    threading.Thread(target=lambda: (time.sleep(20), announce_client_updates()),
                     daemon=True).start()
    threading.Thread(target=reaper, daemon=True).start()

    # Переписка из прошлых запусков. Подрезаем старое и возвращаем в журнал:
    # без этого после перезапуска канал начинался бы с пустого листа.
    cut = HUB.prune_history()
    restored = HUB.load_history()

    cert = None if os.environ.get("NETSPEAK_NO_TLS") == "1" else ensure_cert()
    # TLS_PORT — тот, по которому подключаются. Порт рядом (на единицу меньше)
    # только перенаправляет на него. Без HTTPS поднимаемся на обычном порту,
    # иначе отладка через NETSPEAK_PORT=8800 уехала бы на 8766.
    tls_port = TLS_PORT if cert else None

    if cert:
        httpd = ThreadingHTTPServer(("0.0.0.0", tls_port), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(cert[0]), str(cert[1]))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

        # обычный порт продолжает жить, чтобы старые ссылки не ломались
        RedirectHandler.tls_port = tls_port
        redirect = ThreadingHTTPServer(("0.0.0.0", port), RedirectHandler)
        threading.Thread(target=redirect.serve_forever,
                         kwargs={"poll_interval": 0.5}, daemon=True).start()
    else:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    httpd.daemon_threads = True
    urls = addresses(tls_port if tls_port else port, secure=bool(cert))

    # Цвета — только в настоящей консоли. При запуске из окна журнала вывод
    # идёт в файл, и служебные escape-последовательности там лишь мусор.
    def c(code: str, text: str) -> str:
        if not getattr(sys.stdout, "isatty", lambda: False)():
            return text
        return f"\033[{code}m{text}\033[0m"

    print("\n" + c("92", "=" * 58))
    print("  " + c("1", "NetSpeak") + " — сервер запущен")
    print(c("92", "=" * 58))
    print(f"  Сервер      : {SERVER_NAME}")
    print(f"  Порт        : {tls_port}")
    for url in urls:
        print("  " + c("96", f"Откройте в браузере:  {url}"))
    print("\n  Подключаться могут другие компьютеры и телефоны в этой же сети.")
    if PASSWORD:
        print(f"  Пароль сервера: {c('93', PASSWORD)}")
    else:
        print("  Пароль не задан, заходят все, кто видит адрес.")

    if cert:
        print(f"\n  {c('93', 'Микрофон браузеры открывают только по HTTPS, поэтому сервер поднят')}")
        print(f"  {c('93', f'на порту {tls_port}, а старый {port} перенаправляет сюда.')}")
        print("  " + c("33", "При первом входе браузер предупредит о самоподписанном сертификате:"))
        print("  " + c("33", "нажмите «Дополнительно» и «Перейти к...». Для локальной сети это безопасно."))
    else:
        print("\n  " + c("91", "ВНИМАНИЕ: голос работать не будет."))
        print("  " + c("91", "Микрофон доступен только по HTTPS, а openssl на компьютере не найден."))
        print("  " + c("91", "Установите Git для Windows и запустите NetSpeak заново."))

    stats = HUB.history_stats()
    if restored:
        print(f"\n  {c('96', f'Переписка: возвращено сообщений — {restored}')}")
        for name, count in stats.get("channels", {}).items():
            print(f"    {name:<16} {count}")
        if cut:
            print(f"  {c('90', f'старых сообщений убрано — {cut}')}")
    if stats.get("users"):
        print(f"\n  {c('96', f'В базе: человек — {stats['users']}, сообщений — {stats.get('messages', 0)}')}")
    if stats.get("dir"):
        # папкой это было раньше, теперь один файл — так и пишем
        print(f"  {c('90', str(stats['dir']))}")

    print("\n  Остановить: Ctrl+C")
    print(c("92", "=" * 58) + "\n")

    if OPEN_BROWSER:
        # по желанию: NETSPEAK_OPEN_BROWSER=1. Обычно браузер не нужен —
        # либо откроется окно приложения, либо пользователь сам нажмёт
        # кнопку в окне сервера
        threading.Timer(0.8, lambda: webbrowser.open(urls[0])).start()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n  Останавливаю NetSpeak…")
    finally:
        httpd.shutdown()
        httpd.server_close()
        print("  Пока! 👋\n")


def main(argv: list[str] | None = None) -> int:
    """Точка входа сервера.

    Вынесена отдельно от блока __main__, чтобы собранный exe мог запустить
    сервер у себя внутри, а не искать этот файл на диске.
    """
    global DEFAULT_PORT, TLS_PORT
    argv = list(sys.argv[1:] if argv is None else argv)

    def number(flag: str):
        if flag in argv:
            try:
                return int(argv[argv.index(flag) + 1])
            except (IndexError, ValueError):
                return None
        return None

    plain = number("--port")
    secure = number("--tls-port")
    if plain is None and secure is None:
        # окно запуска передаёт просто число: «NetSpeak.exe --server 8766».
        # Это порт, по которому подключаются, то есть защищённый.
        for arg in argv:
            if str(arg).isdigit():
                secure = int(arg)
                break

    if plain:
        DEFAULT_PORT = plain
    if secure:
        TLS_PORT = secure
        # рядом держим редирект, если его явно не задали
        if plain is None:
            DEFAULT_PORT = max(1, secure - 1)
    mimetypes.add_type("application/javascript", ".js")
    start(DEFAULT_PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())