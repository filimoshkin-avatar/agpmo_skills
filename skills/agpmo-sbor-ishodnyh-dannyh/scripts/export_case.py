#!/usr/bin/env python3
"""Выгрузка обезличенного пакета исходных данных AgPMO для исполнителя модели.

Собирает публичный пакет по перечню разрешённых полей (внутреннее состояние работы
целиком не переносится), проверяет ключи и значения на признаки персональных и
договорных данных и на строки стоп-списка, проверяет так же выжимку и сообщение
клиенту и только после общей проверки кладёт все файлы в папку выдачи.

Использование:
    <интерпретатор Python 3> export_case.py case_state.json --out <папка выдачи> \\
        --md <выжимка.md> --also <сообщение клиенту.md> [--allow исключения.json]

Файл исключений — список объектов {"fragment": "точный фрагмент", "reason": "почему это не
персональные данные"}; он снимает только перечисленные фрагменты. Адреса почты, телефоны,
номера ИНН и счетов, пути к файлам и строки стоп-списка исключениями не снимаются.
Отсутствие совпадений не доказывает полного обезличивания: выдачу нужно перечитать.
"""
import sys
sys.dont_write_bytecode = True
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
import argparse
import datetime
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_case import validate, file_names, load_json  # noqa: E402

SCHEMA = "agpmo-inputs-3.1"

# ---------- публичная схема: только разрешённые поля ----------
EVIDENCE = {"source", "locator"}
VALUE_OBJ = {"value", "unit", "basis", "scope", "range"}
UNREAD = {"part", "material", "reason"}
ALLOWED = {
    "client": {"code", "label", "name_consent", "name_consent_source"},
    "company": {"industry", "size", "projects", "pm_office", "maturity", "systems"},
    "constraint": {"kind", "text", "evidence"},
    "source": {"id", "type", "content", "status", "period", "scope", "parts_read", "parts_unread",
               "duplicate_of", "duplicate_kind", "version_of", "message_ref", "note"},
    "parameter": {"key", "value", "unit", "status", "basis", "evidence", "derivation", "assumptions",
                  "range", "period", "scope", "depends_on", "practices", "step", "cost_basis"},
    "practice": {"id", "name", "function", "description", "status", "evidence", "result_unit", "rationale",
                 "dependencies", "preconditions", "analog", "launch_order", "suitability", "alternatives"},
    "effect": {"id", "description", "evidence", "allocations", "counting", "metric", "period", "frequency",
               "cost_per_case", "amount", "amount_unit", "composition", "counted_in", "note"},
    "question": {"id", "kind", "parameter_keys", "opportunity_ids", "constraint_kinds", "reason", "material",
                 "status", "answer_source", "conflict"},
    "opportunity": {"id", "process", "problem", "evidence", "effect_kind", "status", "practice", "reason"},
    "progress": {"announced", "asked", "mode", "no_data_told", "preliminary"},
    "target_model": {"name", "version", "compatibility", "specification"},
    "history": {"date", "event", "keys", "previous", "new", "reason"},
}
SUIT = {"repeat", "acceptance", "data", "heterogeneous", "human_decision", "recipient"}


def _scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


def _plain(v):
    """Скаляр или список скаляров; всё остальное отбрасывается."""
    if _scalar(v):
        return v, True
    if isinstance(v, list) and all(_scalar(x) for x in v):
        return list(v), True
    return None, False


def public_view(case):
    """Публичный пакет из внутреннего состояния. Возвращает (пакет, список отброшенных полей)."""
    dropped = []

    def pick(obj, allowed, where, special=None):
        if not isinstance(obj, dict):
            dropped.append(where)
            return None
        res = {}
        for k, v in obj.items():
            if k not in allowed:
                dropped.append(f"{where}.{k}")
                continue
            if special and k in special:
                res[k] = special[k](v, f"{where}.{k}")
                continue
            pv, ok = _plain(v)
            if ok:
                res[k] = pv
            else:
                dropped.append(f"{where}.{k}")
        return res

    def evid(v, where):
        if not isinstance(v, list):
            dropped.append(where)
            return []
        return [x for x in (pick(i, EVIDENCE, f"{where}[{n}]") for n, i in enumerate(v)) if x is not None]

    def unread(v, where):
        if not isinstance(v, list):
            dropped.append(where)
            return []
        out = []
        for n, i in enumerate(v):
            if isinstance(i, str):
                out.append(i)
            else:
                x = pick(i, UNREAD, f"{where}[{n}]")
                if x is not None:
                    out.append(x)
        return out

    def valobj(v, where):
        if v is None:
            return None
        return pick(v, VALUE_OBJ, where)

    def mapping(v, where):
        if not isinstance(v, dict):
            dropped.append(where)
            return {}
        res = {}
        for k, x in v.items():
            if isinstance(k, str) and _scalar(x):
                res[k] = x
            else:
                dropped.append(f"{where}.{k}")
        return res

    def suit(v, where):
        if not isinstance(v, dict):
            dropped.append(where)
            return {}
        return {k: x for k, x in v.items() if k in SUIT and (isinstance(x, bool) or x == "pilot")}

    def rows(name, kind, special=None):
        items = case.get(name)
        if not isinstance(items, list):
            return []
        return [x for x in (pick(r, ALLOWED[kind], f"{name}[{n}]", special) for n, r in enumerate(items)) if x is not None]

    out = {"schema_version": SCHEMA, "revision": case.get("revision", 1)}
    out["client"] = pick(case.get("client") or {}, ALLOWED["client"], "client") or {}
    out["company"] = pick(case.get("company") or {}, ALLOWED["company"], "company") or {}
    out["constraints"] = rows("constraints", "constraint", {"evidence": evid})
    out["sources"] = rows("sources", "source", {"parts_unread": unread})
    out["parameters"] = rows("parameters", "parameter", {"evidence": evid})
    out["practices"] = rows("practices", "practice", {"evidence": evid, "suitability": suit})
    out["effects"] = rows("effects", "effect", {"evidence": evid, "allocations": mapping,
                                                "frequency": valobj, "cost_per_case": valobj})
    out["questions"] = rows("questions", "question")
    if isinstance(case.get("opportunities"), list):
        out["opportunities"] = rows("opportunities", "opportunity", {"evidence": evid})
    out["history"] = rows("history", "history")
    out["progress"] = pick(case.get("progress") or {}, ALLOWED["progress"], "progress") or {}
    out["target_model"] = pick(case.get("target_model") or {}, ALLOWED["target_model"], "target_model") or {}
    for k in case:
        if k not in out and k not in ("schema_version", "revision") and not k.startswith("_"):
            dropped.append(k)
    return out, dropped


# ---------- поиск признаков персональных и договорных данных ----------
FIRST_NAMES = ("Александр|Алексей|Андрей|Антон|Артём|Борис|Вадим|Валентин|Валерий|Василий|Виктор|Виталий|Владимир|Владислав|"
               "Вячеслав|Геннадий|Георгий|Глеб|Григорий|Даниил|Денис|Дмитрий|Евгений|Егор|Иван|Игорь|Илья|Кирилл|Константин|Леонид|"
               "Максим|Михаил|Никита|Николай|Олег|Павел|Пётр|Петр|Роман|Руслан|Сергей|Станислав|Степан|Тимур|Фёдор|Федор|Юрий|Ярослав|"
               "Александра|Алина|Алла|Анастасия|Анна|Валентина|Валерия|Вера|Виктория|Галина|Дарья|Евгения|Екатерина|Елена|Елизавета|"
               "Жанна|Ирина|Карина|Ксения|Лариса|Любовь|Людмила|Маргарита|Марина|Мария|Надежда|Наталья|Нина|Оксана|Ольга|Полина|"
               "Светлана|Софья|Татьяна|Юлия|Тихон|Семён|Семен|Захар|Арсений|Марк|Лев|Матвей|Эдуард|Аркадий|Ульяна|Василиса|Зоя|Таисия|Эльвира|Регина")
LATIN_NAMES = ("John|James|Robert|Michael|William|David|Richard|Joseph|Thomas|Charles|Daniel|Matthew|Anthony|Mark|Paul|Steven|Andrew|"
               "Peter|George|Kevin|Brian|Edward|Mary|Patricia|Jennifer|Linda|Elizabeth|Susan|Jessica|Sarah|Karen|Nancy|Lisa|Emily|"
               "Alexander|Alexandr|Alexey|Aleksey|Aleksei|Andrey|Andrei|Anton|Boris|Dmitry|Dmitriy|Dmitrii|Evgeny|Evgeniy|Gennady|Gennadiy|"
               "Georgy|Grigory|Igor|Ivan|Ilya|Kirill|Konstantin|Leonid|Maxim|Mikhail|Nikita|Nikolay|Nikolai|Oleg|Pavel|Petr|Pyotr|Roman|"
               "Ruslan|Semen|Sergey|Sergei|Stanislav|Stepan|Timur|Fedor|Vadim|Valery|Vasily|Viktor|Victor|Vitaly|Vladimir|Vladislav|"
               "Vyacheslav|Yaroslav|Yuri|Yury|Alina|Alla|Anna|Anastasia|Daria|Darya|Ekaterina|Elena|Elizaveta|Evgenia|Galina|Irina|"
               "Karina|Ksenia|Larisa|Lyudmila|Margarita|Maria|Marina|Nadezhda|Natalia|Natalya|Nina|Oksana|Olga|Polina|Sofia|Svetlana|"
               "Tatiana|Tatyana|Valentina|Valeria|Vera|Victoria|Viktoria|Yulia|Julia")
SURNAME = r"(?:ов|ев|ёв|ин|ын|ский|цкий|ская|цкая|ова|ева|ёва|ина|ына|енко|ук|юк|ян|дзе|швили|их|ых)"
LAT_SURNAME = r"(?:ov|ev|in|yn|sky|skiy|skii|skaya|ova|eva|ina|yna|enko|uk|yuk|yan|dze|shvili)"
ENDINGS = r"(?:[аяыиеуюйь]|ой|ей|ою|ем|ом|ём)?"
OPEN_Q = r"[«\"„“‘']"
CLOSE_Q = r"[»\"“”’']"


def _name_alt():
    out = []
    for n in sorted(set(FIRST_NAMES.split("|")), key=len, reverse=True):
        stem = n[:-1] if n[-1] in "аяйь" and len(n) > 3 else n
        out.append(re.escape(stem))
    return "(?:" + "|".join(out) + ")" + ENDINGS


NAME_ALT = _name_alt()
ORG_FORMS = r"(?:ООО|АО|ПАО|ЗАО|ОАО|ИП|ГУП|МУП|ФГУП|НКО|(?i:ооо|зао|пао|оао)|LLC|Ltd|GmbH|Inc|JSC|LLP)"
ORG_AFTER = r"(?:ООО|ЗАО|ПАО|ОАО|LLC|Ltd|GmbH|Inc|JSC|LLP)"
PATTERNS = [
    ("адрес почты", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("телефон", re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b|\+\d{1,3}[\s(-]*\d{1,4}[\s)-]*\d{2,4}[\s-]*\d{3,4}(?:[\s-]*\d{2,4})?\b")),
    ("фамилия с инициалами", re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]+|\b[А-ЯЁ]{3,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")),
    ("фамилия с одним инициалом", re.compile(r"\b[А-ЯЁ][а-яё]+" + SURNAME + r"(?:ым|им|ой|ому|ого|у|а|е)?\s+[А-ЯЁ]\.(?!\s?[А-ЯЁ]\.)|\b[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]+" + SURNAME + r"\b")),
    ("фамилия рядом с правками", re.compile(r"\b(?:[Кк]орр\.?|[Пп]равк\w*|[Зз]амечани\w*|[Рр]ед\.?)\s+[А-ЯЁ][а-яё]+(?:" + SURNAME[3:-1] + r"|овой|евой|иной|ской|цкой|ского|цкого)\b")),
    ("имя и отчество", re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:ович|евич|ьич|овна|евна|ична|инична)\b")),
    ("имя и фамилия", re.compile(r"\b" + NAME_ALT + r"\s+[А-ЯЁ][а-яё]{2,}" + SURNAME + r"[а-яё]{0,3}\b|\b[А-ЯЁ][а-яё]+" + SURNAME + r"[а-яё]{0,3}\s+" + NAME_ALT + r"\b")),
    ("имя и фамилия латиницей", re.compile(
        r"\b(?:" + LATIN_NAMES + r")\s+[A-Z][a-z]{2,}\b|\b[A-Z][a-z]{2,}\s+(?:" + LATIN_NAMES + r")\b"
        r"|\b[A-Z][a-z]+\s+[A-Z]\.\s?(?:[A-Z]\.)?|\b[A-Z]\.\s?(?:[A-Z]\.\s?)?[A-Z][a-z]{2,}"
        r"|\b[A-Z]{3,}\s+[A-Z]\.\s?[A-Z]\.|\b[A-Z][a-z]+" + LAT_SURNAME + r"a?\s+[A-Z][a-z]{2,}\b")),
    ("ИНН, СНИЛС или счёт", re.compile(r"(?:ИНН|СНИЛС|КПП|ОГРН|[Рр]/[Сс]|[Сс]ч[её]т\w*)\D{0,12}\d{9,20}|\b\d{3}-\d{3}-\d{3}\s?\d{2}\b|\b\d{20}\b")),
    ("возможный ИНН", re.compile(r"(?<![\d.,])[1-9]\d{9}(?:\d\d)?(?![\d.,])")),
    ("местный телефон", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{2}(?![\d-])")),
    ("номер договора", re.compile(r"(?:[Дд]оговор\w*|[Кк]онтракт\w*|[Сс]оглашени\w*|[Пп]исьм\w*|[Ии]сх\.?|[Вв]х\.?)\s*(?:№|N|No)\s*[\w/-]+|(?:№|N)\s*[А-ЯA-Z]{2,5}-\d+|\b[А-ЯA-Z]{2,5}-\d{4}/\d+|(?:[Дд]оговор\w*|[Кк]онтракт\w*)\s+[А-ЯA-Z]{1,5}-?\d+/\d+")),
    ("название организации", re.compile(r"\b" + ORG_FORMS + r"\s*" + OPEN_Q + r"[^»\"“”’']+" + CLOSE_Q + r"|\b" + ORG_FORMS + r"\s+[А-ЯЁA-Zа-яё][\w-]{2,}|\b[А-ЯЁA-Z][\w-]{2,}\s+" + ORG_AFTER + r"\b")),
    ("название объекта", re.compile(r"\b(?:ЖК|БЦ|ТЦ|ТРЦ)\s*" + OPEN_Q + r"[^»\"“”’']+" + CLOSE_Q)),
    ("название проекта", re.compile(r"(?i)\bпроект(?:а|у|ом|е)?\s+" + OPEN_Q + r"[^»\"“”’']{2,}" + CLOSE_Q)),
    ("оклад", re.compile(r"\b(?:[Оо]клад|[Зз]арплат)\w*[^.\n]{0,40}?\d[\d\s ]*(?:000|тыс|млн)")),
    ("путь к файлу", re.compile(
        r"\b[A-Za-z]:[\\/]|\\\\[\w.-]+\\|~/|\bfile://|%[A-Z_]+%[\\/]|\$(?:HOME|USER|TMPDIR)\b|\$\{HOME\}"
        r"|(?<![\w/])/(?:home|Users|root|mnt|opt|tmp|var|srv|workspace|data|media|private|etc|usr|Volumes|sdcard|app|content)(?:/|\b)")),
    ("имя файла", None),
]
NEVER_ALLOWED = {"адрес почты", "телефон", "ИНН, СНИЛС или счёт", "путь к файлу", "строка стоп-списка"}
ADJ_END = ("ого", "его", "ому", "ему", "ыми", "ими", "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю")
SALARY_OK = re.compile(r"(?i)(?:типов\w*|ориентир\w*|рыночн\w*)\s+(?:\w+\s+){0,2}(?:оклад|зарплат)")
SALARY_OK_AFTER = re.compile(r"^(?:[^.;\n]|\.(?=\s*[₽а-яё]))*?(?:по типовой практике|типов\w*|ориентир\w* навыка)")
OWN_LABELS = {"название организации", "строка стоп-списка"}   # согласованное название снимает только эти находки


def scan_text(s):
    """Все совпадения всех шаблонов в строке: (вид, фрагмент)."""
    found = []
    for label, rx in PATTERNS:
        if rx is None:
            found += [(label, n) for n in file_names(s)]
            continue
        for m in rx.finditer(s):
            if label == "оклад" and (SALARY_OK.search(s[max(0, m.start() - 40):m.end()]) or SALARY_OK_AFTER.search(s[m.end():])):
                continue  # «типовой оклад», «ориентир навыка» — не сведения о людях
            if label == "возможный ИНН" and m.group(0).endswith("000"):
                continue  # круглая сумма, а не номер
            found.append((label, m.group(0)))
    return found


LB = r"(?<![0-9A-Za-zА-Яа-яЁё])"   # граница слова: подчёркивание и дефис считаются разделителями
RB = r"(?![0-9A-Za-zА-Яа-яЁё])"
SEP = r"[\s_\-]+"


def stop_patterns(s):
    """Строка стоп-списка: точное совпадение без учёта регистра (с «_» и «-» вместо пробелов)
    и, для слов со строчными, падежные формы по основе — с прописной буквы или прописными."""
    words = [w for w in re.split(r"[\s_]+", s) if w]
    res = [re.compile(LB + SEP.join(re.escape(w) for w in words) + (RB if s[-1].isalnum() else ""), re.I)]
    if s[-1].isalnum() and any(ch.islower() for ch in s):
        parts, upper = [], []
        for w in words:
            if any(ch.islower() for ch in w) and w[-1].isalpha() and len(w) > 3:
                lw = w.lower()
                adj = next((e for e in ADJ_END if lw.endswith(e) and len(w) - len(e) >= 4), None)
                if adj:
                    base = w[:-len(adj)]                      # «Железнодорожный» → «Железнодорожн»
                else:
                    base = w[:-1] if len(w) > 4 and lw[-1] in "аяоеиыуюйь" else w
                    base = base[:-1] if len(base) > 4 and base[-1].lower() in "ий" else base
                parts.append(re.escape(base) + r"[а-яёa-z]{0,4}")
                upper.append(re.escape(base.upper()) + r"[А-ЯЁA-Z]{0,4}")
            else:
                parts.append(re.escape(w))
                upper.append(re.escape(w.upper()))
        res.append(re.compile(LB + SEP.join(parts) + RB))    # «Аверину», «Сигмы»
        res.append(re.compile(LB + SEP.join(upper) + RB))    # «АВЕРИНУ», «ЖЕЛЕЗНОДОРОЖНОМ ТУПИКЕ»
    if len(words) >= 3 and all(len(w) >= 4 for w in words[:2]):
        res += stop_patterns(" ".join(words[:2]))           # длинное название узнаётся и по началу
    return res


def walk_strings(node, path="$", depth=0):
    """Все строки структуры вместе с путями, включая ключи словарей."""
    if depth > 50:
        raise ValueError("слишком глубокая вложенность структуры")
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                yield f"{path}.<ключ>", k
            yield from walk_strings(v, f"{path}.{k}", depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, f"{path}[{i}]", depth + 1)
    elif isinstance(node, str):
        yield path, node


def find_all(items, stop_rx, own):
    found = _find_all(items, stop_rx, own)
    seen, res = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            res.append(f)
    return res


def _find_all(items, stop_rx, own):
    found = []
    for path, s in items:
        for label, frag in scan_text(s):
            if own and label in OWN_LABELS and frag.lower() in own.lower():
                continue
            found.append((path, label, frag))
        for word, rxs in stop_rx:
            for rx in rxs:
                for m in rx.finditer(s):
                    if own and m.group(0).lower() in own.lower():
                        continue
                    found.append((path, "строка стоп-списка", word))
                    break
    return found


def load_allow(path):
    if not path:
        return []
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError("файл исключений — список объектов {fragment, reason}")
    res = []
    for i, x in enumerate(data):
        if not (isinstance(x, dict) and isinstance(x.get("fragment"), str) and x["fragment"].strip()
                and isinstance(x.get("reason"), str) and len(x["reason"].strip()) >= 10):
            raise ValueError(f"исключение {i}: нужны fragment и reason (не короче 10 знаков)")
        res.append(x["fragment"])
    return res


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


OWN_OUTPUT = re.compile(r"^(?:Исходные_данные_финмодели_агентизации|Сообщение_клиенту|Пульт_экономики_агентизации)_[\w\-]+_v\d+\.(?:md|json|html)$")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="состояние работы case_state.json")
    ap.add_argument("--out", required=True, help="папка выдачи; файлы появляются в ней только после общей проверки")
    ap.add_argument("--code", help="код клиента для имени файла; по умолчанию client.code, при расхождении — ошибка")
    ap.add_argument("--md", help="выжимка .md: проверяется и после проверки копируется в папку выдачи")
    ap.add_argument("--also", action="append", default=[], help="другой файл для выдачи (сообщение клиенту): проверяется и копируется; можно несколько раз")
    ap.add_argument("--allow", help="файл точечных исключений: [{\"fragment\": …, \"reason\": …}]")
    ap.add_argument("--replace", action="store_true", help="разрешить замену в папке выдачи файлов с теми же именами, если это не прежняя выдача навыка")
    a = ap.parse_args()

    try:
        case = load_json(a.path)
    except (OSError, ValueError) as exc:
        fail(f"Ошибка чтения состояния работы: {exc}", 2)
    if not isinstance(case, dict) or case.get("schema_version") != SCHEMA:
        fail(f"Ошибка: выгружается только пакет {SCHEMA}; переведите состояние работы на эту схему (references/model-and-output.md)", 2)
    cl = case.get("client") if isinstance(case.get("client"), dict) else {}
    cl_code = cl.get("code") if isinstance(cl.get("code"), str) else ""
    if a.code and a.code != cl_code:
        fail(f"Ошибка: --code «{a.code}» не совпадает с client.code «{cl_code}»", 2)
    code = a.code or cl_code
    if not re.fullmatch(r"[\w\-]{1,40}", code or ""):
        fail(f"Ошибка: код «{code}» нельзя использовать в имени файла как есть; исправьте client.code (буквы, цифры, «_», «-», до 40 знаков)", 2)

    errors, warnings = validate(case)
    for w in warnings:
        print("Предупреждение: " + w)
    if errors:
        print("Пакет не прошёл проверку, ничего не записано. Исправьте состояние работы:")
        for e in errors:
            print("  " + e)
        sys.exit(1)

    try:
        allow = load_allow(a.allow)
    except (OSError, ValueError) as exc:
        fail(f"Ошибка в файле исключений: {exc}", 2)

    out, dropped = public_view(case)
    if dropped:
        print("Не вошли в публичный пакет (нет в перечне разрешённых полей): " + ", ".join(dropped[:20]) + (f" и ещё {len(dropped) - 20}" if len(dropped) > 20 else ""))

    own = cl.get("label", "") if cl.get("name_consent") is True and isinstance(cl.get("label"), str) else ""
    raw_stop = [s.strip() for s in (case.get("_stoplist") or []) if isinstance(s, str) and s.strip()]
    short = [s for s in raw_stop if len(s) < 3]
    if short:
        print("Предупреждение: строки стоп-списка короче трёх знаков не проверяются: " + ", ".join(short[:10]))
    stop = [s for s in raw_stop if len(s) >= 3]
    if not stop:
        print("Предупреждение: стоп-список пуст — поиск опирается только на шаблоны и находит не всё; перечитайте выдачу глазами")
    stop_rx = [(s, stop_patterns(s)) for s in stop]

    name = f"Исходные_данные_финмодели_агентизации_{code}_v{out.get('revision', 1)}.json"
    texts, names = {}, {name}
    for f in ([a.md] if a.md else []) + a.also:
        p = Path(f)
        if p.name in names:
            fail(f"Ошибка: в выдаче два файла с именем «{p.name}»", 2)
        names.add(p.name)
        try:
            texts[p] = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            fail(f"Ошибка чтения {p.name}: {exc}", 2)

    def all_findings(pkg):
        items = list(walk_strings(pkg))
        items += [("имя файла выдачи", n.rsplit(".", 1)[0].replace("_", " ")) for n in sorted(names)]
        for p, t in texts.items():
            items += [(f"{p.name}:{i}", line) for i, line in enumerate(t.splitlines(), 1)]
        return find_all(items, stop_rx, own)

    found = all_findings(out)
    blocking = [f for f in found if f[1] in NEVER_ALLOWED or f[2] not in allow]
    if blocking:
        print("Найдены признаки персональных или договорных данных, ничего не записано:")
        for p, label, frag in blocking:
            tail = " (исключением не снимается)" if label in NEVER_ALLOWED else ""
            print(f"  {p}: {label} — «{frag}»{tail}")
        sys.exit(1)
    excepted = len(found)
    out["export"] = {
        "date": datetime.date.today().isoformat(),
        "auto_check": "автоматическая проверка совпадений не нашла" if not excepted else f"совпадения сняты точечными исключениями: {excepted}",
        "note": "отсутствие совпадений не доказывает полного обезличивания; перед передачей выдачу нужно перечитать",
    }
    if [f for f in all_findings(out) if f[1] in NEVER_ALLOWED or f[2] not in allow]:
        fail("Ошибка: после добавления служебных записей появились совпадения; ничего не записано", 1)
    try:
        payload = json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError:
        fail("Ошибка: в пакете есть NaN или бесконечность; ничего не записано", 1)

    dest = Path(a.out)
    dest.mkdir(parents=True, exist_ok=True)
    state = Path(a.path).resolve()
    for n in names:
        target = dest / n
        if target.exists():
            if target.resolve() == state:
                fail("Ошибка: файл выдачи совпал бы с состоянием работы; выберите другую папку выдачи", 2)
            if not OWN_OUTPUT.match(n) and not a.replace and not any(target.resolve() == p.resolve() for p in texts):
                fail(f"Ошибка: в папке выдачи уже есть «{n}», это не прежняя выдача навыка; укажите другую папку или --replace", 2)
    stage = Path(tempfile.mkdtemp(prefix=".vydacha-", dir=dest))
    written = []
    try:
        (stage / name).write_text(payload, encoding="utf-8")
        for p, t in texts.items():
            (stage / p.name).write_text(t, encoding="utf-8")
        for f in sorted(stage.iterdir()):
            os.replace(f, dest / f.name)
            written.append(f.name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    print("Записано в папку выдачи: " + ", ".join(written))
    print("Автоматическая проверка не доказывает полного обезличивания: перечитайте выдачу перед передачей.")


if __name__ == "__main__":
    main()
