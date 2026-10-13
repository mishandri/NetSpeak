# -*- coding: utf-8 -*-
"""База сервера: кто бывает и что было сказано.

SQLite — потому что он уже встроен в Python. Ставить ничего не нужно,
отдельный сервер поднимать не нужно, файл один и переносится вместе с
папкой программы. (DuckDB тут не годится: он заточен под анализ данных,
а пишет он файлы не как базу, а как чужой формат.)

Что в базе
----------
users      — кто приходил: uid, имя, аватар
messages   — переписка по каналам

Про «и данные есть в базе, и локально»
--------------------------------------
Оно и не конфликтует. Разделение такое:

* ``user/profile.json`` — мои настройки на ЭТОМ компьютере. Переносятся
  вместе с папкой пользователя. Это то, что задаю я.
* таблица ``users`` — общий список на сервере. Кто подключался, с каким
  именем и аватаром.

Правило простое и поэтому предсказуемое: **свою запись человек
перезаписывает сам, чужие строки не трогает никто.** Зашёл — сервер
обновил твою строку тем, что ты прислал. Увидел чужого — прочитал из
базы и показал. Споров быть не может: автор записи один, и это человек.

Так имена и аватары перестают быть личными ярлыками в браузере. Раньше
каждый держал своего, и получалось, что чужие аватары не видно вовсе —
а ведь именно это самое интересное.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid       TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    avatar    TEXT NOT NULL DEFAULT '',
    show_circle INTEGER NOT NULL DEFAULT 1,
    avatar_rev INTEGER NOT NULL DEFAULT 0,
    color     TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen  INTEGER NOT NULL DEFAULT 0,
    visits    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    channel  TEXT NOT NULL DEFAULT 'general',
    kind     TEXT NOT NULL DEFAULT 'user',
    uid      TEXT NOT NULL DEFAULT '',
    src      TEXT NOT NULL DEFAULT '',      -- id соединения, а не человек
    name     TEXT NOT NULL DEFAULT '',
    color    TEXT NOT NULL DEFAULT '',
    dst      TEXT NOT NULL DEFAULT '',      -- личное сообщение кому
    text     TEXT NOT NULL DEFAULT '',
    file_id  TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    size     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel, id);
CREATE INDEX IF NOT EXISTS idx_messages_ts      ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_users_lastseen   ON users(last_seen);
"""


class Store:
    """Тонкая обёртка над SQLite: соединение одно, доступ из потоков."""

    def __init__(self, path: Path, keep: int = 2000):
        self.path = Path(path)
        self.keep = int(keep)
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None

    # ── открытие ──────────────────────────────────────────────────────────
    def open(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")      # не блокируем запись
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(SCHEMA)
            self._migrate(db)
            db.commit()
            self._db = db
            return True
        except sqlite3.Error:
            self._db = None
            return False

    @staticmethod
    def _migrate(db) -> None:
        """Добавить недостающие колонки в базу, созданную прошлой версией.

        CREATE TABLE IF NOT EXISTS новых колонок не добавляет, поэтому
        приходится смотреть, что уже есть, и дополнять по одному.
        """
        have = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        for name, spec in (
            ("avatar_rev", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in have:
                try:
                    db.execute(f"ALTER TABLE users ADD COLUMN {name} {spec}")
                except sqlite3.Error:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
                self._db = None

    @property
    def ok(self) -> bool:
        return self._db is not None

    def _write(self, sql: str, args: tuple = ()) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                self._db.execute(sql, args)
                self._db.commit()
            except sqlite3.Error:
                pass          # база — не повод ронять разговор

    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        if self._db is None:
            return []
        with self._lock:
            try:
                return list(self._db.execute(sql, args))
            except sqlite3.Error:
                return []

    # ── люди ──────────────────────────────────────────────────────────────
    def upsert_user(self, uid: str, name: str = "", avatar: str = "",
                    show_circle: bool = True, color: str = "") -> None:
        """Записать человека. Свою строку он перезаписывает сам.

        uid пустой — записывать нечего: без него строка не адресуется
        и завтра другой человек с тем же именем занял бы её место.
        """
        if not uid:
            return
        now = int(time.time())
        self._write(
            """
            INSERT INTO users(uid, name, avatar, show_circle, color,
                              first_seen, last_seen, visits)
            VALUES(?,?,?,?,?,?,?,1)
            ON CONFLICT(uid) DO UPDATE SET
                name=excluded.name,
                -- пустым значением картинку не затираем: им приходит
                -- «свой аватар не менялся», а стирание выглядело бы как
                -- «аватар пропал» у всех, кто зашёл
                avatar=CASE WHEN excluded.avatar<>'' THEN excluded.avatar
                           ELSE users.avatar END,
                show_circle=excluded.show_circle,
                color=CASE WHEN excluded.color<>'' THEN excluded.color
                           ELSE users.color END,
                last_seen=excluded.last_seen,
                visits=users.visits+1
            """,
            (uid, name or "", avatar or "", 1 if show_circle else 0,
             color or "", now, now),
        )

    def bump_avatar(self, uid: str, avatar: str = "") -> int:
        """Запомнить новую картинку и вернуть её номер версии.

        Номер растёт при каждой загрузке, и в ссылку на картинку он
        попадает как ?v=... Без этого адрес не менялся бы: файл перезаписывается
        под тем же именем, браузер показывал бы старую картинку, и человек
        видел бы чужой аватар до перезагрузки страницы.
        """
        if not uid:
            return 0
        self._write(
            "UPDATE users SET avatar=?, avatar_rev=avatar_rev+1, last_seen=? "
            "WHERE uid=?",
            (avatar or "", int(time.time()), uid),
        )
        row = self.user(uid)
        # поле наружу отдаётся как avatarRev — так его и читаем
        return int(row.get("avatarRev", 0)) if row else 0

    def touch_user(self, uid: str) -> None:
        if not uid:
            return
        self._write("UPDATE users SET last_seen=? WHERE uid=?",
                    (int(time.time()), uid))

    def user(self, uid: str) -> dict | None:
        rows = self._read("SELECT * FROM users WHERE uid=?", (uid,))
        return _user_row(rows[0]) if rows else None

    def users(self) -> dict[str, dict]:
        """uid → профиль. То, что показывается чужим людям."""
        return {r["uid"]: _user_row(r) for r in self._read("SELECT * FROM users")}

    def known_users(self, uids) -> list[dict]:
        """Профили по списку, без лишних строк."""
        out = []
        for uid in uids:
            u = self.user(uid)
            if u:
                out.append(u)
        return out

    def forget_users(self, older_than_days: int = 180) -> int:
        """Забыть тех, кого не было дольше полугода."""
        cutoff = int(time.time()) - older_than_days * 86400
        rows = self._read("SELECT uid FROM users WHERE last_seen < ?", (cutoff,))
        for r in rows:
            self._write("DELETE FROM users WHERE uid=?", (r["uid"],))
        return len(rows)

    # ── переписка ─────────────────────────────────────────────────────────
    def add_message(self, m: dict) -> None:
        self._write(
            """
            INSERT INTO messages(ts, channel, kind, uid, src, name, color,
                                 dst, text, file_id, filename, size)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (int(m.get("ts") or time.time()),
             m.get("channel") or "general",
             m.get("kind") or "user",
             m.get("uid") or "",
             m.get("from") or "",
             m.get("name") or "",
             m.get("color") or "",
             m.get("to") or "",
             m.get("text") or "",
             m.get("fileId") or "",
             m.get("filename") or "",
             int(m.get("size") or 0)),
        )

    def messages(self, channel: str | None = None, limit: int = 150) -> list[dict]:
        """Последние сообщения — по каналу или по всем."""
        if channel:
            rows = self._read(
                "SELECT * FROM messages WHERE channel=? AND dst='' "
                "ORDER BY id DESC LIMIT ?", (channel, int(limit)))
        else:
            rows = self._read(
                "SELECT * FROM messages WHERE dst='' "
                "ORDER BY id DESC LIMIT ?", (int(limit),))
        return [_message_row(r) for r in reversed(rows)]

    def prune(self) -> int:
        """Оставить в каждом канале последние keep сообщений."""
        removed = 0
        for row in self._read("SELECT DISTINCT channel FROM messages"):
            channel = row["channel"]
            old = self._read(
                "SELECT id FROM messages WHERE channel=? ORDER BY id DESC "
                "LIMIT -1 OFFSET ?", (channel, self.keep))
            for r in old:
                self._write("DELETE FROM messages WHERE id=?", (r["id"],))
                removed += 1
        return removed

    def counts(self) -> dict:
        users = self._read("SELECT COUNT(*) AS n FROM users")
        msgs = self._read("SELECT COUNT(*) AS n FROM messages")
        return {
            "users": users[0]["n"] if users else 0,
            "messages": msgs[0]["n"] if msgs else 0,
            "path": str(self.path),
        }

    # ── перенос из прежних файлов ─────────────────────────────────────────
    def import_jsonl(self, directory: Path) -> int:
        """Забрать переписку из history/*.jsonl. Один раз, при переходе.

        Файлы не трогаем: вдруг пригодится откатиться. Повторный запуск
        ничего не добавит — у сообщений уже есть свои номера, а те
        отличаются текстом и временем.
        """
        if not directory.is_dir():
            return 0
        imported = 0
        for path in sorted(directory.glob("*.jsonl")):
            channel = path.stem
            known = {(m["ts"], m.get("text", ""), m.get("name", ""))
                     for m in self.messages(limit=100000)}
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                key = (int(m.get("ts") or 0), m.get("text", ""),
                       m.get("name", ""))
                if key in known:
                    continue
                m.setdefault("channel", channel)
                self.add_message(m)
                imported += 1
        return imported


def _user_row(row) -> dict:
    return {
        "uid": row["uid"],
        "name": row["name"],
        "avatar": row["avatar"],
        "avatarRev": int(row["avatar_rev"] or 0),
        "showCircle": bool(row["show_circle"]),
        "color": row["color"],
        "firstSeen": int(row["first_seen"]),
        "lastSeen": int(row["last_seen"]),
        "visits": int(row["visits"]),
    }


def _message_row(row) -> dict:
    """Сообщение в том виде, в каком его ждёт интерфейс."""
    m = {
        "kind": row["kind"],
        "from": row["src"],
        "uid": row["uid"],
        "name": row["name"],
        "color": row["color"],
        "channel": row["channel"],
        "to": row["dst"] or None,
        "text": row["text"],
        "ts": int(row["ts"]),
    }
    if row["file_id"]:
        m["fileId"] = row["file_id"]
        m["filename"] = row["filename"]
        m["size"] = int(row["size"])
    return m