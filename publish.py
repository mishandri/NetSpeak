# -*- coding: utf-8 -*-
"""Переносит в рабочий репозиторий только файлы, нужные для сборки.

Зачем: в D:\\NetSpeak после запусков остаются рабочие папки (settings,
user, logs, data, history) и собранные exe. В репозитории им не место —
они попадут в историю и раздуют её, а собранные exe ещё и за-binary-ятся.

Что переносится:
    src/          исходники целиком
    tools/        генератор значков
    docs/         инструкция по релизу
    build.py      сборка под Windows
    build_linux.py сборка под Linux
    requirements-linux.txt  зависимости для сборки под Linux
    readme.md     описание
    LICENSE
    .gitignore

Чего быть не должно:
    bin/          результат сборки, каждый раз новый
    build/        временные файлы PyInstaller
    settings/ user/ logs/ data/ history/ update/   рабочие данные

Запуск:
    python publish.py                       # в D:\\Documents\\Git\\NetSpeak
    python publish.py C:\\путь\\к\\репозиторию
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: Куда переносить по умолчанию.
DEFAULT_TARGET = Path(r"D:\Documents\Git\NetSpeak")

#: Папки, которые переносятся целиком.
COPY_DIRS = ("src", "tools", "docs")

#: Отдельные файлы. .gitattributes нужен не меньше, чем .gitignore: без него
#: git на Windows переписывает переносы строк и в списке изменений мелькают
#: файлы, которых никто не трогал.
COPY_FILES = ("build.py", "build_linux.py", "readme.md", "LICENSE",
              ".gitignore", ".gitattributes", "publish.py",
              "requirements-linux.txt")

#: Папки, которые не должны попасть в репозиторий ни при каких условиях.
FORBIDDEN = ("bin", "build", "dist", "__pycache__", ".venv", "venv",
             "settings", "user", "logs", "data", "history", "update",
             ".pytest_cache", ".idea", ".vscode")


def wanted_files() -> list[Path]:
    out: list[Path] = []
    for name in COPY_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if any(part in FORBIDDEN for part in rel.parts):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            out.append(path)
    for name in COPY_FILES:
        if (ROOT / name).is_file():
            out.append(ROOT / name)
    return out


def copy_all(target: Path) -> int:
    copied = 0
    for src in wanted_files():
        rel = src.relative_to(ROOT)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # сравниваем содержимое: незачем трогать файл, который не менялся
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dst)
        copied += 1
    return copied


def clean(target: Path) -> list[str]:
    """Убрать то, что раньше скопировали, а теперь переносить не нужно."""
    removed: list[str] = []
    for name in FORBIDDEN:
        path = target / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name + "/")
    # мусор внутри переносимых папок
    for name in COPY_DIRS:
        base = target / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), reverse=True):
            rel = path.relative_to(target)
            if any(part in FORBIDDEN for part in rel.parts):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.is_file():
                    path.unlink(missing_ok=True)
                    removed.append(str(rel).replace("\\", "/"))
    return removed


def main(argv: list[str]) -> int:
    target = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_TARGET
    if not target.is_dir():
        print(f"Папки нет: {target}")
        return 1
    if not (target / ".git").is_dir():
        print(f"Это не git-репозиторий: {target}")
        return 1

    files = wanted_files()
    copied = copy_all(target)
    removed = clean(target)

    print(f"Куда: {target}")
    print(f"Файлов к переносу: {len(files)}, из них обновлено: {copied}")
    if removed:
        print("Убрано лишнее:")
        for name in removed:
            print(f"    {name}")
    print("\nГотово. Дальше:")
    print(f'    cd "{target}"')
    print("    git add -A")
    print('    git commit -m "NetSpeak 1.0.0"')
    print("    git push")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))