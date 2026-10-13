# -*- coding: utf-8 -*-
"""Чтение и запись маленьких JSON-файлов с настройками.

Запись атомарная: сначала во временный файл, потом переименование.
Иначе при сбое питания (или если выключили компьютер) файл настроек
может остаться наполовину записанным и приложение не запустится.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def load_json(path: Path, default=None):
    """Читает JSON. Если файла нет или он битый — возвращает default.

    encoding utf-8-sig, а не utf-8: блокнот в Windows по умолчанию пишет
    с меткой порядка байтов, и обычный json такой файл не разбирает. Раньше
    из-за этого правка настроек в блокноте молча сбрасывала всё на умолчания
    — а выглядело это как «программа забыла настройки сама».
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return default
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # битый файл не молча затираем: отодвигаем в .bak, чтобы можно было посмотреть
        try:
            path.replace(path.with_suffix(path.suffix + ".bak"))
        except OSError:
            pass
        return default
    return data if isinstance(data, type(default)) else default


def save_json(path: Path, data) -> None:
    """Пишет JSON атомарно."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_json(path: Path, default, patch: dict):
    """Читает, применяет изменения, записывает. Возвращает итог."""
    data = load_json(path, default)
    if not isinstance(data, dict):
        data = dict(default)
    data.update(patch)
    save_json(path, data)
    return data