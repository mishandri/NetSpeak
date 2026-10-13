# -*- coding: utf-8 -*-
"""Рисует иконки приложения из того же знака, что и логотип в интерфейсе.

Знак взят из интерфейса буквально: src/web/index.html, символ #i-wave —
пять вертикальных штрихов, а подложка — .brand__mark в styles.css
(скруглённый квадрат с градиентом).

Запуск:
    python tools/make_icon.py            # оба файла
    python tools/make_icon.py --server   # только сервер

Результат: src/web/icon.ico и src/web/icon-server.ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# Подложка. У клиента — как в интерфейсе (--accent в синий),
# у сервера — бордовая, чтобы окна не путались в панели задач.
THEMES = {
    "client": {
        "file": ROOT / "src" / "web" / "icon.ico",
        "from": (0x3F, 0xC7, 0x9A),   # --accent
        "to": (0x2A, 0x7F, 0xD4),     # конец градиента .brand__mark
        "ink": (0x08, 0x22, 0x1A),     # --on-accent, цвет штрихов
    },
    "server": {
        "file": ROOT / "src" / "web" / "icon-server.ico",
        # Бордовый, но не чёрный: на тёмном фоне чёрные полоски мылятся
        # и знак рассыпается. Фон светлее, полоски — как у клиента,
        # почти чёрные. В среднем читается как бордовый.
        "from": (0x8A, 0x1C, 0x3E),
        "to": (0xC9, 0x40, 0x62),
        "ink": (0x18, 0x02, 0x09),
    },
}

# знак в координатах viewBox 24x24: (x, y_начала, y_конца)
BARS = [(4, 11, 13), (8, 7.5, 16.5), (12, 4, 20), (16, 7.5, 16.5), (20, 11, 13)]
STROKE = 1.9        # .ic { stroke-width: 1.9 }
RADIUS = 12 / 42    # скругление подложки относительно её размера
ICON_PART = 20 / 42 # знак занимает 20px внутри квадрата 42px (.ic = 20px)

SS = 8  # во сколько раз рисуем крупнее, потом уменьшаем — края будут гладкими


def gradient(size: int, c_from, c_to) -> Image.Image:
    """Подложка: диагональный градиент, как linear-gradient(135deg, …)."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # проекция на диагональ: 0 в левом верхнем углу, 1 в правом нижнем
            t = (x + y) / (2.0 * max(1, size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(c_from, c_to))
    return img


def draw_icon(size: int, c_from, c_to, ink) -> Image.Image:
    n = size * SS
    img = gradient(n, c_from, c_to).convert("RGBA")

    # подложка со скруглением
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, n - 1, n - 1), radius=RADIUS * n, fill=255
    )
    img.putalpha(mask)

    # сам знак: масштаб viewBox 24 → размер квадрата, по центру
    unit = (n * ICON_PART) / 24.0
    offset = (n - n * ICON_PART) / 2.0
    stroke = STROKE * unit

    draw = ImageDraw.Draw(img)
    for x, y1, y2 in BARS:
        cx = offset + x * unit
        draw.line(
            [(cx, offset + y1 * unit), (cx, offset + y2 * unit)],
            fill=ink + (255,),
            width=max(1, round(stroke)),
        )
        # .ic { stroke-linecap: round } — закругляем концы
        r = stroke / 2.0
        for cy in (offset + y1 * unit, offset + y2 * unit):
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ink + (255,))

    return img.resize((size, size), Image.LANCZOS)


def render(theme: dict) -> None:
    frames = [draw_icon(s, theme["from"], theme["to"], theme["ink"]) for s in SIZES]
    out = theme["file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(out, format="ICO",
                    sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    print(f"Готово: {out.name}  ({out.stat().st_size} байт, размеры {SIZES})")


def main() -> int:
    if "--server" in sys.argv:
        render(THEMES["server"])
    elif "--client" in sys.argv:
        render(THEMES["client"])
    else:
        render(THEMES["client"])
        render(THEMES["server"])
    return 0


if __name__ == "__main__":
    sys.exit(main())