# -*- coding: utf-8 -*-
"""История переписки: лежит рядом с программой в папке history/.

Зачем: сервер запускают и забывают, а переписка может понадобиться завтра.
Раньше при перезапуске всё начиналось с чистого листа.

Формат — по файлу на канал, history/<канал>.jsonl, по строке на сообщение.
Выбран не случайно:

  * дописать строку можно в любой момент, и если света выключат посреди
    разговора, уже написанное останется целым — в отличие от общего файла,
    который пришлось бы переписывать целиком;
  * читать и дописывать можно одновременно, поэтому сервер не встаёт
    колом даже на секунду;
  * под файл можно подглядеть глазами, не разбирая формат.

Один файл на канал — потому что спрашивают «что тут было в Общем», а не
«что было вообще». Заодно чинить один канал не задевает остальные.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

#: Сколько сообщений держим в канале. Старые подрезаются при запуске.
KEEP_PER_CHANNEL = 2000

#: Сколько последних сообщений держит сервер в памяти для показа при входе.
CHAT_HISTORY_TAIL = 150

#: Имя канала в имя файла: только буквы, цифры, дефис и подчёркивание.
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


def safe_id(raw: str) -> str:
    """Имя канала → безопасное имя файла без расширения."""
    return _SAFE_ID.sub("_", (raw or "").strip())[:40] or "unknown"


class ChatHistory:
    """Чтение и дописание истории каналов.

    Методы безопасны для вызова из потоков: сервер шлёт сообщения из потока
    обработки запросов, а читать историю может кто угодно.
    """

    def __init__(self, directory: Path, keep: int = KEEP_PER_CHANNEL):
        self.dir = Path(directory)
        self.keep = int(keep)
        self._lock = threading.Lock()
        self._sizes: dict[str, int] = {}

    # -- пути ---------------------------------------------------------------
    def path_for(self, channel: str) -> Path:
        return self.dir / (safe_id(channel) + ".jsonl")

    # -- запись -------------------------------------------------------------
    def append(self, channel: str, message: dict) -> None:
        """Дописать сообщение в историю канала.

        Ошибки молча проглатываются: потеря строчки в журнале — не повод
        ронять переписку. Если каталога нет (например, папка переименована),
        сообщение просто не сохранится.
        """
        try:
            line = json.dumps(message, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                # дописываем, а не переписываем: прерывание на середине
                # оставит предыдущие строки целыми
                with open(self.path_for(channel), "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    # на случай отключения питания
                    if os.name == "nt":
                        os.fsync(fh.fileno())
                self._sizes[safe_id(channel)] = self._sizes.get(safe_id(channel), 0) + 1
        except OSError:
            return

    # -- чтение -------------------------------------------------------------
    def read_channel(self, channel: str) -> list[dict]:
        """Сообщения одного канала, от старых к новым."""
        return self._read_file(self.path_for(channel))

    def read_all(self) -> list[dict]:
        """Все сообщения всех каналов по времени — для показа при входе."""
        messages: list[dict] = []
        if not self.dir.is_dir():
            return messages
        for path in sorted(self.dir.glob("*.jsonl")):
            messages.extend(self._read_file(path))
        messages.sort(key=lambda m: m.get("ts", 0))
        return messages

    def _read_file(self, path: Path) -> list[dict]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                # строка могла оборваться посреди записи — пропускаем
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    # -- уборка -------------------------------------------------------------
    def prune(self) -> int:
        """Подрезать каждый файл до последних keep сообщений.

        Вызывается при запуске сервера: за месяцы работы чат мог вырасти
        на десятки мегабайт, а держать его вечно незачем.
        Возвращает, сколько строк удалено.
        """
        if not self.dir.is_dir():
            return 0
        removed = 0
        for path in sorted(self.dir.glob("*.jsonl")):
            lines = self._raw_lines(path)
            if len(lines) <= self.keep:
                continue
            keep = lines[-self.keep:]
            removed += len(lines) - len(keep)
            try:
                path.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
            except OSError:
                continue
        return removed

    @staticmethod
    def _raw_lines(path: Path) -> list[str]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return [l for l in raw.splitlines() if l.strip()]

    # -- удобное ------------------------------------------------------------
    def clear(self) -> None:
        """Удалить всю историю. Нужно, когда переписку сносят специально."""
        if not self.dir.is_dir():
            return
        for path in self.dir.glob("*.jsonl"):
            try:
                path.unlink()
            except OSError:
                pass

    def stats(self) -> dict:
        """Сколько чего накопилось — показывается в окне сервера."""
        out = {"dir": str(self.dir), "channels": {}, "messages": 0}
        if not self.dir.is_dir():
            return out
        for path in sorted(self.dir.glob("*.jsonl")):
            n = len(self._raw_lines(path))
            out["channels"][path.stem] = n
            out["messages"] += n
        return out


def load_into(hub, directory: Path, keep: int = KEEP_PER_CHANNEL) -> int:
    """Наполнить журнал сервера сохранённой историей.

    hub — объект Hub из server.py: у него есть log и lock.
    Возвращает, сколько сообщений вернулось.
    """
    store = ChatHistory(directory, keep=keep)
    store.prune()
    messages = store.read_all()
    if not messages:
        return 0
    with hub.lock:
        # прошлое не должно вытеснять то, что уже в памяти
        hub.log[:] = (messages + hub.log)[-CHAT_HISTORY_TAIL:]
    return len(messages)