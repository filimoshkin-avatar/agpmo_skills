#!/usr/bin/env python3
"""Реестр файлов клиента без распаковки и запуска содержимого.

Использование:
    <интерпретатор Python 3> inventory_sources.py <папки или файлы> --output <путь>/inventory.json [--force]

Пишет {"schema_version": "agpmo-file-inventory-1", "note": …, "files": [...]}; у каждой записи
id (F1, F2…), path, status (received, duplicate, unreadable, partial), sha256 и size_bytes;
у повторов duplicate_of, у вложений архива container, у отказов reason. Коды Д# агент
присваивает сам и хранит соответствие F# → Д# в _file_map состояния работы.

Защита исходных материалов: файл реестра не может совпадать с входным файлом или лежать
внутри входной папки; существующий файл не перезаписывается без --force; читаются только
обычные файлы; символьные ссылки внутри материалов не разворачиваются; предел числа
записей проверяется во время обхода, а не после.
"""
import sys
sys.dont_write_bytecode = True
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 2000
ARCHIVES = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}


class Limit(Exception):
    pass


def iter_files(root):
    """Обычные файлы под root без перехода по ссылкам; ссылки и особые файлы отдаются отдельно."""
    if root.is_symlink():
        yield root, "link"
        return
    try:
        mode = os.stat(root).st_mode
    except OSError:
        yield root, "error"
        return
    if stat.S_ISREG(mode):
        yield root, "file"
        return
    if not stat.S_ISDIR(mode):
        yield root, "special"
        return
    errors = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=errors.append):
        while errors:
            yield Path(getattr(errors.pop(), "filename", dirpath) or dirpath), "error"
        dirnames.sort()
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                yield Path(dirpath, d), "link"
                dirnames.remove(d)
        for name in sorted(filenames):
            p = Path(dirpath, name)
            try:
                mode = os.lstat(p).st_mode
            except OSError:
                yield p, "error"
                continue
            if stat.S_ISLNK(mode):
                yield p, "link"
            elif stat.S_ISREG(mode):
                yield p, "file"
            else:
                yield p, "special"


def inventory(paths, max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES):
    records, seen = [], {}

    def add(path, data=None, **fields):
        if len(records) >= max_entries:
            raise Limit("Достигнут предел записей; разделите материалы на пачки")
        row = {"id": f"F{len(records) + 1}", "path": str(path), "status": "received", **fields}
        if data is not None:
            digest = hashlib.sha256(data).hexdigest()
            row.update(sha256=digest, size_bytes=len(data))
            if digest in seen:
                row.update(status="duplicate", duplicate_of=seen[digest])
            else:
                seen[digest] = row["id"]
        records.append(row)
        return row

    physical = set()
    for input_path in paths:
        root = Path(input_path)
        if not root.exists() and not root.is_symlink():
            add(root, status="unreadable", reason="Путь не найден")
            continue
        # папку или файл, названные агентом, разворачиваем; ссылки внутри материалов — нет
        root = root.resolve()
        for f, kind in iter_files(root):
            if kind == "link":
                add(f, status="unreadable", reason="Символьная ссылка не читается")
                continue
            if kind == "special":
                add(f, status="unreadable", reason="Не обычный файл")
                continue
            if kind == "error":
                add(f, status="unreadable", reason="Нет доступа")
                continue
            real = str(f.resolve())
            if real in physical:
                continue
            physical.add(real)
            try:
                if f.stat().st_size > max_bytes:
                    add(f, status="unreadable", reason="Превышен предел размера файла")
                    continue
                data = f.read_bytes()
            except OSError as exc:
                add(f, status="unreadable", reason=type(exc).__name__)
                continue
            outer = add(f, data)
            if f.suffix.lower() != ".zip" or outer["status"] == "duplicate":
                continue
            try:
                with zipfile.ZipFile(f) as z:
                    entries = [e for e in z.infolist() if not e.is_dir()]
                    if len(entries) + len(records) > max_entries or sum(e.file_size for e in entries) > max_bytes:
                        outer.update(status="partial", reason="Состав архива превышает предел; содержимое не прочитано")
                        continue
                    for entry in entries:
                        name = entry.filename.replace("\\", "/")
                        path = PurePosixPath(name)
                        label = str(f) + "::" + name
                        mode = entry.external_attr >> 16
                        unsafe = path.is_absolute() or ".." in path.parts or ":" in name or stat.S_ISLNK(mode)
                        if unsafe or entry.flag_bits & 1:
                            add(label, status="unreadable", reason="Небезопасный путь, ссылка или зашифрованная запись")
                            outer["status"] = "partial"
                            continue
                        if path.suffix.lower() in ARCHIVES:
                            add(label, status="unreadable", reason="Вложенный архив требует отдельного рассмотрения")
                            outer["status"] = "partial"
                            continue
                        try:
                            add(label, z.read(entry), container=outer["id"])
                        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                            add(label, status="unreadable", reason=type(exc).__name__)
                            outer["status"] = "partial"
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                outer.update(status="unreadable", reason=type(exc).__name__)
    return {"schema_version": "agpmo-file-inventory-1",
            "note": "Инвентаризация не означает смысловое чтение; сохраните коды источников существующего дела при объединении",
            "files": records}


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def check_output(out, inputs, force):
    """Реестр пишется только в отдельную рабочую папку: не на место входного файла, не в папку
    с материалами (ни во вложенную, ни рядом с переданным файлом), не поверх чужого файла."""
    target = Path(out).resolve()
    ancestors = [target.parent] + list(target.parent.parents)
    for i in inputs:
        src = Path(i).resolve()
        if _same(target, src) or target == src:
            return "файл реестра совпадает с входным файлом"
        folder = src if src.is_dir() else src.parent
        if any(_same(folder, a) or folder == a for a in ancestors if a.exists()):
            return "файл реестра попал бы в папку с материалами клиента; укажите отдельную рабочую папку"
    if target.exists():
        if not target.is_file():
            return "по этому пути не обычный файл"
        if not force:
            return "файл уже существует; укажите другой путь или --force, если это прежний реестр"
        try:
            if os.stat(target).st_nlink > 1:
                return "у файла есть другие имена (жёсткие ссылки); перезапись запрещена"
            old = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if not (isinstance(old, dict) and old.get("schema_version") == "agpmo-file-inventory-1"):
            return "--force перезаписывает только прежний реестр файлов, а этот файл им не является"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--output", required=True, help="файл реестра вне папок с материалами")
    parser.add_argument("--force", action="store_true", help="перезаписать прежний файл реестра")
    args = parser.parse_args()
    problem = check_output(args.output, args.paths, args.force)
    if problem:
        parser.exit(2, f"Ошибка: {problem}\n")
    try:
        result = inventory(args.paths)
    except Limit as exc:
        parser.exit(2, str(exc) + "\n")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": len(result["files"]), "output": args.output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
