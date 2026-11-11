#!/usr/bin/env python3
"""Проверка пакета исходных данных AgPMO (agpmo-inputs-3.1, навык версии 5.0; 3.0 — в режиме совместимости).

Проверяет структуру, единицы, ссылки на источники и редакции, происхождение значений,
сценарные ключи, распределение и правдоподобие эффектов, очередь уточнений и условия
пригодности практик. Истинность источников, причинность эффектов и полноту чтения
не проверяет. Для пакета 3.0 правила, появившиеся в 3.1, выдаются предупреждениями.

Использование: <интерпретатор Python 3> validate_case.py case_state.json [--ds-threshold 0.2]
"""
import argparse
import json
import math
import re
from pathlib import Path

import sys
sys.dont_write_bytecode = True
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

SCHEMA = "agpmo-inputs-3.1"
LEGACY = {"agpmo-inputs-3.0"}
BASIS = {"document", "regulation", "client", "typical", "method"}
CONTENT = {"data", "regulation", "template", "example", "other"}
SRC_STATUS = {"received", "read", "partial", "unreadable", "duplicate", "superseded"}
SCEN_KEYS = {"e0", "L", "ds", "v", "real"}
EFFECT_KEYS = {"e0", "ds", "v", "real"}
DEC_PER_PROJECT_YEAR = 0.2
FILE_EXT = ("xlsx|xlsm|xls|ods|csv|docx|doc|odt|rtf|pdf|pptx|ppt|odp|vsdx|vsd|odg|mpp|zip|7z|rar|msg|eml|md|txt|json|dwg|jpg|jpeg|png")
FILE_RX = re.compile(r"(?i)[\w\-]+\.(?:" + FILE_EXT + r")\b")
OWN_FILE_RX = re.compile(r"(?i)^(?:Исходные_данные_финмодели_агентизации|Сообщение_клиенту|Пульт_экономики_агентизации)_[\w\-]+_v\d+\.(?:md|json|html)$")


def _reject_constant(name):
    raise ValueError(f"в JSON недопустимо значение {name}")


def load_json(path):
    """Чтение JSON: UTF-8 (с меткой порядка байтов или без), без NaN и бесконечностей."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("файл не в кодировке UTF-8")
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise ValueError("слишком глубокая вложенность JSON")
    except json.JSONDecodeError as exc:
        raise ValueError(f"неверный JSON: {exc}")


def file_names(text):
    """Имена файлов в тексте, кроме выдачи самого навыка."""
    return [m.group(0) for m in FILE_RX.finditer(text) if not OWN_FILE_RX.match(m.group(0))]
STATUSES = {"measured", "derived", "estimate", "assumption", "decision", "unknown", "not_applicable"}
FILLED = {"measured", "derived", "estimate", "decision"}
PERCENT = {"ca", "cov", "sv", "e0", "ds", "real", "p", "f", "e", "d"}
UNITS = {"P": "шт.", "r": "₽/ч", "H": "мес.", "platOps": "тыс. ₽/мес.", "platImpl": "тыс. ₽", "fte": "ставок",
         "gap": "мес.", "joint": "тыс. ₽/мес.", "N": "задач/проект/мес.", "Q": "результатов/портфель/мес.",
         "th": "ч/задача", "tv": "мин/результат", "L": "тыс. ₽/случай", "v": "тыс. ₽/решение", "ct": "₽/задача",
         "R": "прогонов/задача", "c": "₽/прогон", "impl": "тыс. ₽", "ops": "тыс. ₽/мес.", "ramp": "мес.",
         "real_cap": "ставок", "real_r": "₽/ч"}
UNITS.update({k: "%" for k in PERCENT})
CLIENT_KEYS = ["th", "cov", "sv", "e0", "real", "ds", "v"]   # без N/Q: проверяются отдельно; L — только при e0 > 0
AGENT_KEYS = {"p", "f", "e", "d", "R", "c", "ct", "tv", "ramp", "ca"}
COST_KEYS = {"impl", "ops", "platOps", "platImpl"}
CATALOG = {"briefer", "dispatcher", "sentinel", "chronicler", "expert"}
MODES = {"data": 8, "regulation_only": 10, "no_documents": 10}
AGENT_ROWS = {"p", "f", "e", "d", "R", "c", "ct", "tv", "ramp", "impl", "ops"}
SUIT = ["repeat", "acceptance", "data", "heterogeneous", "human_decision", "recipient"]
SUIT_TXT = {"repeat": "повторяемость", "acceptance": "единица результата и критерии приёмки", "data": "данные вовремя",
            "heterogeneous": "работа с неоднородными сведениями", "human_decision": "решение за человеком", "recipient": "получатель результата"}
ACTIVE = {"proposed", "pilot", "selected"}


LIMIT = 1e12   # числа больше — ошибка ввода, а не данные


def _s(v):
    """Строка или None: защита проверок членства от списков и словарей на месте строк."""
    return v if isinstance(v, str) else None


def numeric(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except OverflowError:
        return False
    return math.isfinite(f) and abs(f) < LIMIT


STR_FIELDS = ("status", "kind", "counting", "content", "basis", "key", "id", "unit", "analog", "source")


def _normalize(case):
    """Копия пакета, где служебные строковые поля неверного типа заменены на None; о каждой замене — ошибка."""
    import copy
    errs = []
    if not isinstance(case, dict):
        return case, errs
    c = copy.deepcopy(case)
    for sec in ("sources", "parameters", "practices", "effects", "questions", "constraints", "opportunities"):
        rows = c.get(sec)
        if not isinstance(rows, list):
            continue
        for n, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for f in STR_FIELDS:
                if f in row and row[f] is not None and not isinstance(row[f], str):
                    errs.append(f"{sec}[{n}].{f}: нужна строка")
                    row[f] = None
    return c, errs


def validate(case, ds_threshold=DEC_PER_PROJECT_YEAR):
    """Обёртка: неверный тип поля даёт ошибку проверки, а не падение скрипта."""
    try:
        case, type_errs = _normalize(case)
        errors, warnings = _validate(case, ds_threshold)
        return type_errs + errors, warnings
    except OverflowError:
        return ["в пакете есть число, которое нельзя обработать (слишком большое); проверьте значения параметров"], []
    except RecursionError:
        return ["слишком глубокая вложенность структуры пакета"], []
    except (TypeError, AttributeError, KeyError, ValueError, ZeroDivisionError) as exc:
        return [f"неверный тип или структура поля (ожидалась строка, число, список или объект по схеме пакета): {type(exc).__name__}: {exc}"], []


def _validate(case, ds_threshold=DEC_PER_PROJECT_YEAR):
    errors, warnings = [], []
    if not isinstance(case, dict):
        return ["Корень должен быть объектом"], []
    legacy = case.get("schema_version") in LEGACY
    if legacy:
        warnings.append(f"пакет схемы {case['schema_version']}: правила {SCHEMA} проверены как предупреждения; переведите состояние на {SCHEMA}")
    elif case.get("schema_version") != SCHEMA:
        errors.append(f"schema_version должна быть {SCHEMA}")

    def rule(msg):
        """Правило схемы 3.1: ошибка для нового пакета, предупреждение для 3.0."""
        (warnings if legacy else errors).append(msg)
    rev = case.get("revision")
    if not isinstance(rev, int) or isinstance(rev, bool) or not 1 <= rev <= 9999:
        errors.append("revision должен быть целым числом от 1 до 9999")
    for field in ("sources", "parameters", "practices", "effects", "questions", "history", "constraints"):
        if not isinstance(case.get(field), list):
            errors.append(f"{field}: нужен список")
    for field in ("client", "company", "progress", "target_model"):
        if not isinstance(case.get(field), dict):
            errors.append(f"{field}: нужен объект")
    if errors:
        return errors, warnings

    cl = case["client"]
    if isinstance(cl.get("code"), str) and cl["code"] and not re.fullmatch(r"[\w\-]{1,40}", cl["code"]):
        errors.append("client.code — до 40 знаков: буквы, цифры, «_» и «-» (без пробелов и длинных тире), иначе имя файла пакета исказится")
    if not cl.get("code") or not cl.get("label") or not isinstance(cl.get("name_consent"), bool):
        errors.append("client: нужны code, label и name_consent (true или false)")
    for f in ("code", "label"):
        v = cl.get(f)
        if isinstance(v, str) and (re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", v) or re.search(r"\+?\d[\d\s()-]{8,}\d", v)
                                   or re.search(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]?\.?", v)):
            errors.append(f"client.{f}: только название или описание компании — без почты, телефонов и имён людей")
    pr = case["progress"]
    if not all(isinstance(pr.get(k), int) and pr.get(k) >= 0 for k in ("announced", "asked")):
        errors.append("progress: нужны целые announced и asked")
    elif pr["asked"] > pr["announced"]:
        warnings.append("задано больше уточнений, чем объявлено: назовите клиенту новое число и объясните причину")
    if pr.get("mode") is not None and pr.get("mode") not in MODES:
        errors.append("progress.mode — data, regulation_only или no_documents")
    for f in ("no_data_told", "preliminary"):
        if f in pr and not isinstance(pr[f], bool):
            errors.append(f"progress.{f} — true или false")

    def keyed(field, key="id"):
        result = {}
        for row in case[field]:
            if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
                errors.append(f"{field}: запись без {key}")
                continue
            if row[key] in result:
                errors.append(f"{field}: повтор {row[key]}")
            result[row[key]] = row
        return result

    sources = keyed("sources")
    params = keyed("parameters", "key")
    practices = keyed("practices")
    effects = keyed("effects")
    questions = keyed("questions")

    def evidence(row, label, required=False):
        items = row.get("evidence", [])
        if not isinstance(items, list) or (required and not items):
            errors.append(f"{label}: нужны источники")
            return []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("source"), str) or item["source"] not in sources or not item.get("locator"):
                errors.append(f"{label}: неверная ссылка на источник или место")
        ids = [i["source"] for i in items if isinstance(i, dict) and isinstance(i.get("source"), str)]
        if ids and all(s in superseded for s in ids):
            warnings.append(f"{label}: опирается только на прежние редакции; сошлитесь на действующую")
        return ids

    superseded, no_content, no_basis = set(), [], []

    def listed(ids):
        return ", ".join(ids[:8]) + (f" и ещё {len(ids) - 8}" if len(ids) > 8 else "")

    for sid, row in sources.items():
        st = row.get("status")
        if st not in SRC_STATUS:
            errors.append(f"{sid}: неверный статус чтения")
        unread = row.get("parts_unread") or []
        if not isinstance(unread, list):
            errors.append(f"{sid}: parts_unread должен быть списком")
            unread = []
        material = [u for u in unread if not (isinstance(u, dict) and u.get("material") is False)]
        for u in unread:
            if not isinstance(u, dict) or not u.get("part") or not isinstance(u.get("material"), bool) or not u.get("reason"):
                rule(f"{sid}: непрочитанная часть — объект с part, material (true/false) и reason")
                break
        if st == "partial" and unread and not material:
            warnings.append(f"{sid}: все непрочитанные части несущественны — поставьте статус read")
        if st == "read" and material:
            errors.append(f"{sid}: при статусе read все непрочитанные части должны быть несущественными (material: false)")
        if st == "duplicate" and row.get("duplicate_of") not in sources:
            errors.append(f"{sid}: нет исходника дубликата")
        if st == "superseded":
            superseded.add(sid)
            vo = row.get("version_of")
            if vo not in sources or vo == sid:
                errors.append(f"{sid}: у прежней редакции нужен version_of — код действующей")
            elif sources[vo].get("status") == "superseded":
                errors.append(f"{sid}: version_of указывает на прежнюю редакцию {vo}; укажите действующую")
        if sid.startswith("Д") and st not in {"duplicate", "superseded"} and row.get("content") is None:
            no_content.append(sid)
        if row.get("content") is not None and _s(row.get("content")) not in CONTENT:
            errors.append(f"{sid}: content — data, regulation, template, example или other")
        for f in ("message_ref", "type", "scope"):
            names = file_names(row[f]) if isinstance(row.get(f), str) else []
            if names:
                rule(f"{sid}: в поле {f} имя файла «{names[0]}»; ссылайтесь на сообщение («ответ на уточнение 3») или тип документа")
    if no_content:
        rule(f"не указано content (data, regulation, template, example, other) у источников: {listed(no_content)}")
    live_docs = [s for s in sources.values() if s["id"].startswith("Д") and s.get("status") in {"read", "partial"}]
    data_ids = {k for k, s in sources.items() if k.startswith("Д") and s.get("content") == "data"}

    if cl.get("name_consent") is True:
        src = cl.get("name_consent_source")
        if not (isinstance(src, str) and src.startswith("О") and src in sources):
            rule("client.name_consent: true требует name_consent_source — кода ответа клиента О#, где он разрешил название; без ответа — описательный код и false")
    stop = case.get("_stoplist")
    if stop is not None and (not isinstance(stop, list) or not all(isinstance(x, str) and x.strip() for x in stop)):
        errors.append("_stoplist — список непустых строк")
    opps = case.get("opportunities")
    opp_ids = set()
    if opps is not None:
        if not isinstance(opps, list):
            errors.append("opportunities: нужен список")
        else:
            for o in opps:
                if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not o["id"]:
                    errors.append("opportunities: у возможности нужен id")
                    continue
                opp_ids.add(o["id"])
                if o.get("practice") is not None and not (isinstance(o["practice"], str) and o["practice"] in practices):
                    errors.append(f"{o['id']}: practice ссылается на несуществующую практику")

    for i, c in enumerate(case["constraints"]):
        if not isinstance(c, dict) or _s(c.get("kind")) not in {"hosting", "integration", "security", "regulation", "other"} or not c.get("text"):
            errors.append(f"constraints[{i}]: нужны kind (hosting, integration, security, regulation, other) и text")
        else:
            evidence(c, f"constraints[{i}]")

    for key, row in params.items():
        status, value = row.get("status"), row.get("value")
        if status not in STATUSES:
            errors.append(f"{key}: неверный статус параметра")
        for field in ("value", "unit", "evidence", "derivation", "assumptions"):
            if field not in row:
                errors.append(f"{key}: отсутствует {field}")
        if status in {"unknown", "not_applicable"}:
            if value is not None:
                errors.append(f"{key}: неизвестное или неприменимое значение должно быть null")
            if not row.get("derivation"):
                errors.append(f"{key}: нужна причина отсутствия значения")
        elif value is None:
            errors.append(f"{key}: пустое известное значение")
        suffix = key.split(".")[-1]
        if suffix in UNITS:
            if row.get("unit") != UNITS[suffix]:
                errors.append(f"{key}: ожидалась единица «{UNITS[suffix]}»")
            if value is not None and not numeric(value):
                errors.append(f"{key}: нужно конечное число по модулю меньше 10^12")
            elif numeric(value) and (value < 0 or (suffix in PERCENT and value > 100)):
                errors.append(f"{key}: значение вне допустимого диапазона")
        if status == "derived" and not row.get("derivation"):
            errors.append(f"{key}: нет расчёта")
        if not isinstance(row.get("assumptions"), list):
            errors.append(f"{key}: assumptions должен быть списком")
        if status == "assumption" and not row.get("assumptions"):
            errors.append(f"{key}: не обосновано допущение")
        basis = row.get("basis")
        if value is not None and status in FILLED | {"assumption"}:
            if basis is None:
                no_basis.append(key)
            elif basis not in BASIS:
                errors.append(f"{key}: basis — document, regulation, client, typical или method")
            else:
                if status == "measured" and basis != "document":
                    errors.append(f"{key}: measured — только по документам (basis: document)")
                if basis in {"typical", "method"} and status != "assumption":
                    errors.append(f"{key}: значение по {'типовой практике' if basis == 'typical' else 'нормативу метода'} может иметь только статус assumption")
                if basis == "regulation" and not row.get("assumptions"):
                    rule(f"{key}: норматив регламента — добавьте в assumptions оговорку, что исполнение не проверено")
                if basis in {"typical", "method"} and (suffix in EFFECT_KEYS or key == "office.joint") and not key.startswith("scenario.") and numeric(value) and value > 0:
                    rule(f"{key}: эффект по {'типовой практике' if basis == 'typical' else 'нормативу метода'} не входит в базу; база — 0"
                         + (f", значение перенесите в scenario.{key}" if key != "office.joint" else ", совместный эффект без доказательства равен нулю"))
        srcs = evidence(row, key, status in FILLED)
        if basis == "document" and value is not None and status != "decision" and srcs and not any(s in data_ids for s in srcs):
            rule(f"{key}: basis document требует ссылки на источник с фактическими данными (content: data); значение из регламента — regulation, из образца или примера — typical")
        from_client = any(s.startswith("О") for s in srcs)
        if suffix in COST_KEYS and from_client and status in FILLED:
            warnings.append(f"{key}: затраты взяты из ответа клиента; их оценивает исполнитель модели")
        if suffix in AGENT_KEYS and from_client and status in FILLED and not key.startswith("scenario."):
            warnings.append(f"{key}: характеристику будущего агента спросили у клиента; её выясняют на пилоте")
        if "range" in row:
            b = row["range"]
            if not isinstance(b, list) or len(b) != 2 or not all(numeric(x) for x in b) or b[0] > b[1]:
                errors.append(f"{key}: неверный диапазон")
            elif numeric(value) and not b[0] <= value <= b[1]:
                errors.append(f"{key}: значение вне диапазона")

    if no_basis:
        rule(f"не указано basis (document, regulation, client, typical, method) у параметров: {listed(no_basis)}")

    orders = []
    for pid, row in practices.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", pid):
            errors.append(f"{pid}: код практики — латиница в нижнем регистре, цифры, подчёркивание")
        if _s(row.get("status")) not in {"proposed", "pilot", "selected", "deferred", "rejected"}:
            errors.append(f"{pid}: неверный статус практики")
        for field in ("name", "function", "description", "result_unit", "rationale"):
            if not row.get(field):
                errors.append(f"{pid}: отсутствует {field}")
            elif not isinstance(row[field], str):
                errors.append(f"{pid}.{field}: нужна строка")
                row = {**row, field: str(row[field])}
        if len(row.get("result_unit") or "") > 60:
            warnings.append(f"{pid}: единица результата длиннее 60 знаков, подробности перенесите в rationale")
        if len(row.get("name") or "") > 14:
            warnings.append(f"{pid}: название длиннее 14 знаков, подробности перенесите в function")
        if pid == "expert":
            errors.append("expert: код образца, своей практике дайте собственный код")
        an = row.get("analog")
        if not (an is None or (isinstance(an, str) and an in CATALOG)):
            errors.append(f"{pid}: analog — код образца из каталога или null")
        evidence(row, pid, True)
        deps = row.get("dependencies", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) or d not in practices or d == pid for d in deps):
            errors.append(f"{pid}: неверные зависимости")
        if row.get("status") in ACTIVE:
            su = row.get("suitability")
            if not isinstance(su, dict) or any(k not in su for k in SUIT):
                warnings.append(f"{pid}: не записана проверка пригодности (suitability)")
            else:
                bad = [SUIT_TXT[k] for k in SUIT if su[k] is False]
                if bad:
                    errors.append(f"{pid}: практика предложена, хотя не выполнено: {', '.join(bad)}")
                pil = [SUIT_TXT[k] for k in SUIT if su[k] == "pilot"]
                if pil and row.get("status") == "proposed":
                    warnings.append(f"{pid}: условия «{', '.join(pil)}» выполнены только на пилоте — статус практики pilot")
            lo = row.get("launch_order")
            if not isinstance(lo, int):
                warnings.append(f"{pid}: не указан порядок запуска (launch_order)")
            else:
                orders.append(lo)
            vol = [params.get(f"{pid}.{k}", {}) for k in ("N", "Q")]
            if not any(v.get("status") in FILLED | {"assumption"} for v in vol):
                warnings.append(f"{pid}: объём (N или Q) неизвестен — эффект практики оценить нельзя")
            L, e0 = params.get(f"{pid}.L", {}), params.get(f"{pid}.e0", {})
            if L.get("status") in FILLED and numeric(L.get("value")) and L["value"] > 0 and e0.get("status") == "assumption" and e0.get("value") == 0:
                warnings.append(f"{pid}: цена ошибки известна, а ошибки текущего процесса приняты нулём — спросите, сколько таких случаев в год")
            missing = [k for k in CLIENT_KEYS if f"{pid}.{k}" not in params]
            if missing:
                warnings.append(f"{pid}: нет записей о параметрах {', '.join(missing)} (неизвестное записывайте со статусом unknown)")
        vals = [params.get(f"{pid}.{k}", {}).get("value") for k in ("ct", "R", "c")]
        if all(numeric(v) for v in vals) and not math.isclose(vals[0], vals[1] * vals[2], rel_tol=0.01, abs_tol=0.01):
            if not params.get(f"{pid}.ct", {}).get("cost_basis"):
                errors.append(f"{pid}: ct не равен R × c; нужно объяснение cost_basis")
    if len(orders) != len(set(orders)):
        warnings.append("у нескольких практик одинаковый порядок запуска")

    def known(key):
        row = params.get(key) or {}
        v = row.get("value")
        return v if numeric(v) and row.get("status") in FILLED | {"assumption"} else None

    for key in params:
        if not key.startswith("scenario."):
            continue
        parts = key.split(".")
        if len(parts) != 3 or parts[1] not in practices:
            rule(f"{key}: сценарный ключ — scenario.<существующая практика>.<параметр>")
            continue
        pid, suf = parts[1], parts[2]
        if suf not in SCEN_KEYS:
            warnings.append(f"{key}: исполнитель читает сценарные ключи только для {', '.join(sorted(SCEN_KEYS))}")
        if suf in {"ds", "v"} and known(f"scenario.{pid}.{'v' if suf == 'ds' else 'ds'}") is None:
            rule(f"{key}: ds и v сценария задаются парой — нет scenario.{pid}.{'v' if suf == 'ds' else 'ds'}")
        if suf == "e0" and known(f"scenario.{pid}.L") is None and known(f"{pid}.L") is None:
            rule(f"{key}: к сценарной доле ошибок нужна цена случая ({pid}.L или scenario.{pid}.L)")
        if suf == "L" and known(f"scenario.{pid}.e0") is None and not known(f"{pid}.e0"):
            warnings.append(f"{key}: сценарная цена случая без доли ошибок (scenario.{pid}.e0 или базовой e0 > 0) в расчёт не попадёт")

    P = known("g.P")
    has_active = any(r.get("status") in ACTIVE for r in practices.values())
    if P is None or P <= 0:
        if has_active:
            rule("g.P: число проектов в контуре должно быть известно и больше нуля — без него объёмы на портфель не пересчитать")
        P = None
    for pid, row in practices.items():
        n, q = known(f"{pid}.N"), known(f"{pid}.Q")
        if n is not None and q is not None:
            rule(f"{pid}: заданы и N, и Q; оставьте один объём, второй опишите в derivation (references/opportunities.md)")
        if _s(row.get("status")) not in ACTIVE:
            continue
        Pp = known(f"{pid}.P")
        if Pp is not None and P is not None and Pp > P:
            errors.append(f"{pid}.P = {Pp:g} больше g.P = {P:g}: применимых проектов не может быть больше контура")
        Pp = Pp or P
        per_proj = n if n is not None else (q / Pp if q is not None and Pp else None)
        e0, L = known(f"{pid}.e0"), params.get(f"{pid}.L") or {}
        if e0 and not (numeric(L.get("value")) and L.get("basis") in {"document", "client"}):
            rule(f"{pid}: базовая доля ошибок e0 > 0 требует известной цены случая {pid}.L из документов или со слов клиента")
        for k in ("e0", "L", "ds", "v"):
            row_k = params.get(f"{pid}.{k}") or {}
            if row_k.get("status") == "estimate" and numeric(row_k.get("value")) and row_k["value"] > 0 and "range" not in row_k:
                rule(f"{pid}.{k}: базовый эффект из оценки клиента «по памяти» — укажите диапазон range и замер на пилоте")
        if per_proj is None:
            if any(known(f"{pref}{pid}.ds") for pref in ("", "scenario.")) or e0:
                warnings.append(f"{pid}: проверка частоты изменённых решений и ошибок пропущена — объём или число проектов неизвестны")
            continue
        if e0 and e0 / 100 * per_proj * 12 > 1:
            warnings.append(f"{pid}: e0 {e0:g} % даёт {e0 / 100 * per_proj * 12:.1f} дорогих ошибки в год на проект — сверьте с ответом о частоте дорогих потерь")
        for pref, label in (("", "база"), ("scenario.", "сценарий")):
            ds = known(f"{pref}{pid}.ds")
            if ds:
                per_year = ds / 100 * per_proj * 12
                if per_year > ds_threshold:
                    warnings.append(f"{pid} ({label}): ds {ds:g} % даёт {per_year:.2f} изменённого решения в год на проект — больше диагностического ориентира {ds_threshold:g}; подкрепите случаями или поясните в derivation, значение ради ориентира не снижайте")
        if known(f"{pid}.th") is None and row.get("status") == "proposed":
            warnings.append(f"{pid}: трудоёмкость неизвестна, а практика к запуску; переведите в pilot с замером или уточните часы")
        if known(f"{pid}.r") is not None and known("g.r") is None:
            warnings.append(f"{pid}: есть ставка практики, а g.r не посчитана; посчитайте её средневзвешенной по часам")

    vol = [params[f"{pid}.{k}"] for pid, row in practices.items() if row.get("status") in ACTIVE
           for k in ("N", "Q", "th") if f"{pid}.{k}" in params and params[f"{pid}.{k}"].get("value") is not None]
    by_basis = {b: sum(1 for x in vol if x.get("basis") == b) for b in BASIS}
    if not by_basis["document"] and not by_basis["client"] and vol and not pr.get("preliminary"):
        warnings.append("объёмы и часы активных практик не подтверждены ни документами, ни клиентом — поставьте progress.preliminary: true")
    if pr.get("preliminary") and (by_basis["document"] or by_basis["client"]):
        warnings.append(f"progress.preliminary: true, хотя {by_basis['document']} значений объёма и часов из документов и {by_basis['client']} от клиента — признак ставится, только если подтверждённых нет ни одного")
    if live_docs and not any(s.get("content") == "data" for s in live_docs) and not by_basis["document"] and not pr.get("no_data_told"):
        warnings.append("в документах нет фактических данных о работе (content: data): объёмы и часы — "
                        + (f"{by_basis['client']} со слов клиента, " if by_basis["client"] else "")
                        + f"{by_basis['regulation']} по регламенту, {by_basis['typical']} по типовой практике; скажите это клиенту, попросите реестр проектов или выгрузку и поставьте progress.no_data_told: true")
    agent_rows = [k for k, r in params.items() if not k.startswith("scenario.") and k.split(".")[-1] in AGENT_ROWS and r.get("status") == "unknown"]
    if agent_rows:
        warnings.append(f"строки unknown по характеристикам агента и затратам ({len(agent_rows)}) не нужны: их заполнит исполнитель")
    caprow = params.get("office.real_cap") or {}
    if caprow:
        grp = caprow.get("practices")
        if grp is not None and (not isinstance(grp, list) or any(not isinstance(k, str) or k not in practices for k in grp)):
            errors.append("office.real_cap: practices — список кодов существующих практик")
    for k, r in params.items():
        if k.endswith(".real_cap") and "step" in r and not isinstance(r["step"], bool):
            errors.append(f"{k}: step — true или false")
    cap_total = known("office.real_cap")
    caps = [known(f"{pid}.real_cap") for pid in practices if known(f"{pid}.real_cap")]
    if cap_total is not None and caps and sum(caps) > cap_total + 1e-9:
        warnings.append(f"сумма пределов практик ({sum(caps):g}) больше общего office.real_cap ({cap_total:g})")

    team_effect = False
    for eid, row in effects.items():
        evidence(row, eid, True)
        alloc = row.get("allocations")
        if not isinstance(alloc, dict) or any(k not in practices or not numeric(v) or not 0 <= v <= 1 for k, v in (alloc or {}).items()):
            errors.append(f"{eid}: неверное распределение эффекта")
        elif sum(alloc.values()) > 1 + 1e-9:
            errors.append(f"{eid}: эффект распределён больше одного раза")
        elif not alloc and row.get("counting") == "confirmed":
            team_effect = True
        if _s(row.get("counting")) not in {"historical_only", "scenario", "confirmed"}:
            errors.append(f"{eid}: неверный вид учёта эффекта")
        amt = row.get("amount")
        if amt is not None and not numeric(amt):
            rule(f"{eid}: amount — число или null; расчёт перенесите в composition")
        if numeric(amt) and not row.get("amount_unit"):
            rule(f"{eid}: у суммы нужна единица amount_unit")
        for f in ("frequency", "cost_per_case"):
            x = row.get(f)
            if x is None:
                continue
            if not (isinstance(x, dict) and (x.get("value") is None or numeric(x.get("value"))) and x.get("unit") and x.get("basis") in BASIS):
                rule(f"{eid}: {f} — объект с value (число или null), unit и basis (document, regulation, client, typical, method)")
            elif row.get("counting") == "confirmed" and x.get("basis") in {"typical", "method"}:
                rule(f"{eid}: подтверждённый эффект не может держаться на {f} по типовой практике; переведите его в scenario")
        ci = row.get("counted_in")
        if ci is None:
            rule(f"{eid}: укажите counted_in — ключи параметров, где эффект уже учтён, или пустой список, если нигде")
        elif not isinstance(ci, list) or any(not isinstance(k, str) or k not in params for k in ci):
            rule(f"{eid}: counted_in — список существующих ключей параметров")
        elif not ci and row.get("counting") == "historical_only" and not row.get("note"):
            warnings.append(f"{eid}: исторический эффект нигде не учтён — объясните в note, почему")
        fr = row.get("frequency") if isinstance(row.get("frequency"), dict) else {}
        unit_txt = str(fr.get("unit", "")).lower()
        yearly = "год" in unit_txt and "полугод" not in unit_txt and "квартал" not in unit_txt
        if isinstance(alloc, dict) and len(alloc) == 1 and numeric(next(iter(alloc.values()))) and next(iter(alloc.values())) > 0 and numeric(fr.get("value")) and yearly:
            pid = next(iter(alloc))
            e0p = known(f"{pid}.e0") or known(f"scenario.{pid}.e0")
            n, q = known(f"{pid}.N"), known(f"{pid}.Q")
            Pp = known(f"{pid}.P") or P
            flow = n * Pp * 12 if n is not None and Pp else (q * 12 if q is not None else None)
            if e0p and flow and fr["value"] > 0:
                implied = e0p / 100 * flow
                if not 0.75 <= implied / (fr["value"] * alloc[pid]) <= 1.25:
                    warnings.append(f"{eid}: e0 {pid} даёт {implied:.1f} случая в год, а частота эффекта — {fr['value'] * alloc[pid]:g}; пересчитайте e0 = случаев в год / (N × P × 12) × 100")
        if isinstance(alloc, dict) and alloc and all(practices.get(k, {}).get("status") not in ACTIVE for k in alloc) and row.get("counting") in {"scenario", "confirmed"}:
            warnings.append(f"{eid}: эффект отнесён только к отложенным или отклонённым практикам и в расчёт не войдёт; назовите это в выжимке")
    joint = params.get("office.joint", {}).get("value")
    if team_effect and numeric(joint) and joint > 0:
        warnings.append("подтверждённый командный эффект и office.joint одновременно: проверьте двойной счёт")

    material = 0
    for qid, row in questions.items():
        kind = row.get("kind")
        if kind not in {"discovery", "parameter", "decision", "constraint"}:
            errors.append(f"{qid}: kind — discovery, parameter, decision или constraint")
            continue
        if _s(row.get("status")) not in {"pending", "asked", "resolved", "deferred"}:
            errors.append(f"{qid}: неверный статус вопроса")
        if not isinstance(row.get("material"), bool):
            errors.append(f"{qid}: material должен быть true или false")
        elif row["material"] and (row.get("status") == "asked" or (row.get("status") == "resolved" and str(row.get("answer_source", "")).startswith("О"))):
            material += 1
        if not row.get("reason"):
            errors.append(f"{qid}: нет причины вопроса")
        if row.get("status") == "resolved" and not (isinstance(row.get("answer_source"), str) and row["answer_source"] in sources):
            errors.append(f"{qid}: нет источника закрытия вопроса")
        if kind == "discovery":
            ops = row.get("opportunity_ids", [])
            if not isinstance(ops, list):
                errors.append(f"{qid}: opportunity_ids должен быть списком")
            elif ops and any(not isinstance(o, str) or o not in opp_ids for o in ops):
                rule(f"{qid}: opportunity_ids ссылаются на возможности, которых нет в opportunities")
            continue
        if kind == "constraint":
            ck = row.get("constraint_kinds")
            if not isinstance(ck, list) or not ck or any(k not in {"hosting", "integration", "security", "regulation", "other"} for k in ck):
                errors.append(f"{qid}: constraint_kinds — непустой список из hosting, integration, security, regulation, other")
            continue
        keys = row.get("parameter_keys")
        if not isinstance(keys, list) or not keys or any(k not in params for k in keys):
            errors.append(f"{qid}: вопрос о параметре должен ссылаться на существующие параметры")
            continue
        filled = all(params[k].get("status") in FILLED for k in keys)
        if filled and row.get("status") in {"pending", "asked"} and not row.get("conflict"):
            errors.append(f"{qid}: повторный вопрос по заполненным параметрам")
        if any(k.split(".")[-1] in COST_KEYS | (AGENT_KEYS - {"tv"}) for k in keys) and row.get("status") in {"pending", "asked"}:
            warnings.append(f"{qid}: вопрос клиенту о затратах или характеристиках агента; их оценивает исполнитель")
    mode = pr.get("mode") or ("data" if any(s.get("content") == "data" for s in live_docs) else ("regulation_only" if live_docs else "no_documents"))
    limit = MODES.get(mode, 10)
    if isinstance(pr.get("announced"), int) and pr["announced"] > limit:
        warnings.append(f"объявлено {pr['announced']} уточнений при пределе {limit} для режима {mode}: объедините темы или перенесите второстепенное в план замеров пилота")
    if isinstance(pr.get("asked"), int) and material > pr["asked"]:
        warnings.append(f"заданных существенных уточнений ({material}) больше, чем в progress.asked ({pr['asked']})")

    target = case["target_model"]
    if target.get("compatibility") not in {"unverified", "verified", "incompatible"}:
        errors.append("target_model: неверный статус совместимости")
    elif target["compatibility"] == "verified" and not (target.get("version") and target.get("specification")):
        errors.append("target_model: нет версии или основания проверки совместимости")
    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path")
    parser.add_argument("--ds-threshold", type=float, default=DEC_PER_PROJECT_YEAR,
                        help="диагностический ориентир: изменённых решений в год на проект, выше которого выдаётся предупреждение")
    args = parser.parse_args()
    try:
        if not (math.isfinite(args.ds_threshold) and args.ds_threshold > 0):
            raise ValueError("--ds-threshold — конечное положительное число")
        case = load_json(args.path)
        errors, warnings = validate(case, args.ds_threshold)
    except (OSError, ValueError) as exc:
        errors, warnings = [str(exc)], []
    print(json.dumps({"valid": not errors, "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
