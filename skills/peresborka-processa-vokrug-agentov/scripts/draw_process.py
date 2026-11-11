#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отрисовщик схем процессов по дорожкам (упрощённый BPMN) и сборщик отчёта. Версия 1.6.

Вход: JSON-спецификация — один объект или непустой список объектов (несколько схем в одной нотации).
Выход: SVG на каждую схему и одна HTML-страница со всеми схемами; по ключу --mermaid ещё и текст Mermaid.
Режим --report: из короткого описания (JSON) и схем собирает одну самодостаточную HTML-страницу.
Если в описании есть поле «review», страница — лист согласования N из 4 (вопрос владельцу, маршрут
согласований, только разделы этого шага); без него — итоговый отчёт со всеми разделами.
Только стандартная библиотека Python 3.8+, сеть не нужна, сторонних пакетов нет.

Скрипт проверяет спецификацию, сам раскладывает схему, обходит блоки стрелками, разводит подписи
и сообщает по-русски о том, что исправить не смог:
  «ошибка» — схема не нарисована (неверный JSON, поле не того типа, неизвестный блок);
  «нарушение правила» — схема нарисована, но нарушена нотация или единая сетка схем одного процесса;
  «замечание раскладки» — стрелка через блок, слившиеся стрелки, подпись на блоке, текст шире блока.
Ноль нарушений и замечаний — минимум; если среда показывает картинки, посмотри на схему глазами.

Запускай по абсолютному пути: рабочая папка агента может не совпадать с папкой навыка.
  python3 /путь/к/навыку/scripts/draw_process.py схемы.json -o /папка/результатов/имя
  … --check      проверить спецификацию и раскладку, файлов не писать
  … --strict     код возврата 1, если есть нарушения правил или замечания раскладки
  … --mermaid    ещё и текст Mermaid (имя.mmd или имя_N.mmd) для каналов, которые его показывают
  … --png        ещё и PNG, если установлен cairosvg
  python3 /путь/к/навыку/scripts/draw_process.py --example > пример.json
  python3 /путь/к/навыку/scripts/draw_process.py отчёт.json --report -o /папка/результатов/Согласование_2_из_4_имя_v1.0 --strict
  python3 /путь/к/навыку/scripts/draw_process.py отчёт.json --report -o /папка/результатов/Отчёт_пересборки_имя_v1.0 --strict
  python3 /путь/к/навыку/scripts/draw_process.py --example-report > пример_отчёта.json

Коды возврата: 0 — готово; 1 — ошибки, а при --strict ещё и нарушения или замечания; 2 — неверный вызов.
Форматы описаны в references/diagram-spec.md (схемы) и references/report-spec.md (отчёт и листы) рядом с навыком.
"""
import argparse
import json
import os
import re
import sys
from html import escape as html_escape
from xml.sax.saxutils import escape

# ---------- размеры и цвета ----------
COLW, LABELW, GAP = 168, 156, 26           # колонка, столбец подписей дорожек, зазор между блоками
BOXH, ROWGAP, LPAD, LH = 90, 22, 14, 14.5  # блок, зазор рядов, поле дорожки, межстрочный интервал
HEADH, RESW, MARGIN = 44, 236, 20          # строка этапов, блок результата, поля листа

INK, INK2, LINE = "#17212B", "#4F5B69", "#5E6B7A"
RULE, RULE_STRONG = "#D3DAE1", "#A9B4BF"
LANE_A, LANE_B, BG, SURFACE = "#E9EDF1", "#F4F6F8", "#F2F4F6", "#FFFFFF"
RULE_BG = "#E1E6EC"                        # шаг, который делает правило или обычная автоматизация
AGENT, AGENT_BG, AGENT_INK = "#0C766E", "#DDF0EC", "#08524C"
WAIT, WAIT_BG = "#9C5F0E", "#FAEBD2"
RES_BG, RES_ACCENT, RES_MUTED = "#17212B", "#5FCFC1", "#AEB9C4"
FONT = "Arial, Helvetica, 'Liberation Sans', sans-serif"

# виды блоков; порядок задаёт порядок легенды
NODE_KINDS = {
    "human":    dict(fill=SURFACE,  stroke=RULE_STRONG, dash=None,  sw=1,   ink=INK,       name="Действие человека"),
    "agent":    dict(fill=AGENT_BG, stroke=AGENT,       dash=None,  sw=1.2, ink=AGENT_INK, name="Действие агента"),
    "rule":     dict(fill=RULE_BG,  stroke=LINE,        dash=None,  sw=1.2, ink=INK,       name="Правило или автоматизация"),
    "wait":     dict(fill=WAIT_BG,  stroke=WAIT,        dash="5 3", sw=1,   ink=INK,       name="Ожидание и потери"),
    "call":     dict(fill=SURFACE,  stroke=INK,         dash="2 3", sw=1.4, ink=INK,       name="Человек по вызову"),
    "decision": dict(fill=SURFACE,  stroke=INK,         dash=None,  sw=2,   ink=INK,       name="Решение человека"),
    "gateway":  dict(fill=SURFACE,  stroke=INK,         dash=None,  sw=1.4, ink=INK,       name="Развилка"),
    "option":   dict(fill=AGENT_BG, stroke=AGENT,       dash="5 3", sw=1,   ink=AGENT_INK, name="Модуль по согласию"),
    "start":    dict(fill=SURFACE,  stroke=INK,         dash=None,  sw=1.5, ink=INK,       name="Начало"),
    "end":      dict(fill=SURFACE,  stroke=INK,         dash=None,  sw=3,   ink=INK,       name="Конец"),
}
EVENTS = ("start", "end")
# виды стрелок
EDGE_KINDS = {
    "flow":     dict(color=LINE,  dash=None,  sw=1.3),   # ход работы
    "message":  dict(color=LINE,  dash="6 4", sw=1.3),   # передача сведений между дорожками
    "option":   dict(color=AGENT, dash="5 3", sw=1.3),   # по согласию, необязательная связь
    "call":     dict(color=INK,   dash="2 3", sw=1.3),   # вызов человека
    "wait":     dict(color=WAIT,  dash=None,  sw=1.3),   # ожидание, итерации
    "decision": dict(color=INK,   dash=None,  sw=2),     # передача результата, решение
}
ROUTES = ("auto", "v", "h", "hvh", "vh", "hv")
MAX_COLS, MAX_LANES, MAX_NODES = 8, 7, 24   # пределы из правил раскладки (references/diagram-spec.md)

EXAMPLE = {
    "eyebrow": "Вариант 1 из 3",
    "title": "Как есть",
    "subtitle": "Методолог сам собирает и анализирует сведения, процесс ждёт Заказчика на каждом шаге",
    "lanes": [
        {"id": "z", "label": "Заказчик"},
        {"id": "a", "label": "ИИ-агенты", "note": "агентов нет"},
        {"id": "m", "label": "Методолог"}
    ],
    "phases": ["Документы", "Интервью и анкета", "Синтез и отчёт", "Замечания"],
    "nodes": [
        {"id": "z1", "lane": "z", "col": 0, "kind": "wait", "title": "Собирает документы", "note": "ожидание, дозапросы"},
        {"id": "z2", "lane": "z", "col": 1, "kind": "wait", "title": "Интервью и анкета", "note": "ожидание: календари"},
        {"id": "z3", "lane": "z", "col": 3, "kind": "wait", "title": "Замечания к отчёту", "note": "итерации правок"},
        {"id": "m1", "lane": "m", "col": 0, "kind": "human", "title": "Анализ", "text": "документов"},
        {"id": "m2", "lane": "m", "col": 1, "kind": "human", "title": "Интервью", "text": "протоколы, анкета"},
        {"id": "m3", "lane": "m", "col": 2, "kind": "human", "title": "Синтез", "text": "отчёт и Дорожная карта"},
        {"id": "m4", "lane": "m", "col": 3, "kind": "human", "title": "Доработка", "text": "отчёта"}
    ],
    "edges": [
        {"from": "z1", "to": "m1", "label": "документы"},
        {"from": "z2", "to": "m2", "label": "беседы, ответы"},
        {"from": "m1", "to": "m2"}, {"from": "m2", "to": "m3"}, {"from": "m3", "to": "m4"},
        {"from": "z3", "to": "m4", "kind": "wait", "both": True, "label": "замечания"},
        {"from": "m4", "to": "result", "kind": "decision"}
    ],
    "result": {
        "heading": "Одинаков во всех вариантах",
        "items": ["Резюме о текущем состоянии СУПД", "Положительные практики и недостатки",
                  "Рекомендации по развитию СУПД", "Дорожная карта развития СУПД"],
        "footer": "Отвечает Исполнитель"
    },
    "metrics": ["Срок до принятого отчёта: 8–10 недель", "Трудозатраты людей: 40–55 человеко-дней",
                "Заказчик видит выводы: в конце", "оценка"]
}


# ---------- текст ----------
def wrap(text, max_chars):
    """Разбивает текст на строки: сначала по «|», затем длинные куски — по словам."""
    lines = []
    for seg in str(text or "").split("|"):
        seg = seg.strip()
        if not seg:
            continue
        cur = ""
        for word in seg.split():
            cand = (cur + " " + word).strip()
            if cur and len(cand) > max_chars:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        if cur:
            lines.append(cur)
    return lines


def text_width(s, size, bold=False):
    return len(s) * size * (0.58 if bold else 0.55) + 4


# ---------- проверка спецификации ----------
def phases_list(spec):
    out = []
    for p in spec.get("phases") or []:
        if isinstance(p, dict):
            out.append((str(p.get("label", "")), max(1, int(p.get("span", 1))), str(p.get("note", ""))))
        else:
            out.append((str(p), 1, ""))
    return out


def head_height(spec):
    """Строка этапов выше, если у этапов есть подписи ритма («раз в квартал»)."""
    return HEADH + 14 if any(n for _, _, n in phases_list(spec)) else HEADH


def columns_count(spec):
    n = int(spec.get("columns", 0) or 0)
    ph = sum(s for _, s, _ in phases_list(spec))
    nodes = spec.get("nodes") or []
    nd = max([int(x.get("col", 0)) + int(x.get("span", 1)) for x in nodes] + [0])
    return max(n, ph, nd, 1)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_text(v):
    return isinstance(v, (str, int, float)) and not isinstance(v, bool)


def check_shape(data):
    """Проверяет форму данных до раскладки: типы полей и диапазоны. Возвращает (схемы, ошибки)."""
    errors = []
    if isinstance(data, dict):
        specs = [data]
    elif isinstance(data, list):
        specs = data
        if not specs:
            errors.append("нет ни одной схемы: список пуст")
    else:
        return [], ["верхний уровень файла — объект схемы или список схем, получено: " + type(data).__name__]
    for i, sp in enumerate(specs, 1):
        where = f"схема {i}"
        if not isinstance(sp, dict):
            errors.append(f"{where}: ожидался объект с полями lanes, nodes, edges, получено: {type(sp).__name__}")
            continue

        def need_list(key, item_type, label):
            v = sp.get(key)
            if v is None:
                return []
            if not isinstance(v, list) or not all(isinstance(x, item_type) for x in v):
                errors.append(f"{where}: поле «{key}» — список {label}")
                return []
            return v
        lanes = need_list("lanes", dict, "объектов {\"id\", \"label\"}")
        for l in lanes:
            if not isinstance(l.get("id"), str) or not l.get("id"):
                errors.append(f"{where}: у дорожки нужен непустой строковый «id»")
            for k in ("label", "note"):
                if k in l and not _is_text(l[k]):
                    errors.append(f"{where}, дорожка «{l.get('id')}»: поле «{k}» — строка")
            if "height" in l and (not _is_int(l["height"]) or l["height"] < 20):
                errors.append(f"{where}, дорожка «{l.get('id')}»: поле «height» — целое число не меньше 20")
        for n in need_list("nodes", dict, "объектов-блоков"):
            name = f"{where}, блок «{n.get('id')}»"
            if not isinstance(n.get("id"), str) or not n.get("id"):
                errors.append(f"{where}: у блока нужен непустой строковый «id»")
            if not isinstance(n.get("lane"), str):
                errors.append(f"{name}: поле «lane» — id дорожки строкой")
            for k, lo in (("col", 0), ("row", 0), ("span", 1)):
                if k in n and (not _is_int(n[k]) or n[k] < lo):
                    errors.append(f"{name}: поле «{k}» — целое число не меньше {lo}, получено «{n[k]}»")
            for k in ("kind", "title", "text", "tag", "note"):
                if k in n and not _is_text(n[k]):
                    errors.append(f"{name}: поле «{k}» — строка")
        for e in need_list("edges", dict, "объектов-стрелок"):
            name = f"{where}, стрелка {e.get('from')}→{e.get('to')}"
            for k in ("from", "to"):
                if not isinstance(e.get(k), str) or not e.get(k):
                    errors.append(f"{name}: поле «{k}» — id блока строкой или «result»")
            for k in ("kind", "label", "route"):
                if k in e and not _is_text(e[k]):
                    errors.append(f"{name}: поле «{k}» — строка")
            for k in ("dx", "dy"):
                if k in e and (not isinstance(e[k], (int, float)) or isinstance(e[k], bool)):
                    errors.append(f"{name}: поле «{k}» — число")
            if "both" in e and not isinstance(e["both"], bool):
                errors.append(f"{name}: поле «both» — true или false")
        for p in need_list("phases", (str, dict), "строк или объектов {\"label\", \"span\", \"note\"}"):
            if isinstance(p, dict):
                if not _is_text(p.get("label", "")):
                    errors.append(f"{where}: у этапа поле «label» — строка")
                if "span" in p and (not _is_int(p["span"]) or p["span"] < 1):
                    errors.append(f"{where}, этап «{p.get('label')}»: поле «span» — целое число не меньше 1")
        need_list("metrics", str, "строк вида «Срок: 4–6 недель»")
        if "columns" in sp and (not _is_int(sp["columns"]) or sp["columns"] < 0):
            errors.append(f"{where}: поле «columns» — целое число не меньше 0")
        if "result" in sp:
            r = sp["result"]
            if not isinstance(r, dict):
                errors.append(f"{where}: поле «result» — объект {{\"items\": [...], \"footer\": \"...\"}}")
            else:
                if "items" in r and (not isinstance(r["items"], list) or not all(_is_text(x) for x in r["items"])):
                    errors.append(f"{where}: поле «result.items» — список строк")
                for k in ("title", "heading", "footer"):
                    if k in r and not _is_text(r[k]):
                        errors.append(f"{where}: поле «result.{k}» — строка")
        for k in ("title", "subtitle", "eyebrow", "caption", "page_title"):
            if k in sp and not _is_text(sp[k]):
                errors.append(f"{where}: поле «{k}» — строка")
    return specs, errors


def validate(spec, errors, violations, warnings):
    """Смысловые проверки одной схемы: ошибки не дают рисовать, нарушения правил рисуются, но отклоняются --strict."""
    lanes = spec.get("lanes") or []
    if not lanes:
        errors.append("нет дорожек: заполни «lanes»")
    lane_ids = [l.get("id") for l in lanes]
    if len(set(lane_ids)) != len(lane_ids):
        errors.append("у каждой дорожки нужен свой «id»")
    nodes = spec.get("nodes") or []
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        errors.append("у каждого блока нужен свой «id»")
    if "result" in ids:
        errors.append("«result» — служебный id блока результата, назови блок иначе")
    ncols = columns_count(spec)
    taken = {}
    for n in nodes:
        if n.get("lane") not in lane_ids:
            errors.append(f"блок «{n.get('id')}»: дорожка «{n.get('lane')}» не объявлена")
        kind = n.get("kind", "human")
        if kind not in NODE_KINDS:
            violations.append(f"блок «{n.get('id')}»: вид «{kind}» неизвестен, нарисован как «human»; "
                              f"допустимые виды: {', '.join(NODE_KINDS)}")
        col, span, row = int(n.get("col", 0)), int(n.get("span", 1)), int(n.get("row", 0))
        if col + span > ncols:
            errors.append(f"блок «{n.get('id')}»: колонки {col}–{col + span - 1} выходят за {ncols} колонок схемы")
        for c in range(col, col + span):
            key = (n.get("lane"), row, c)
            if key in taken:
                errors.append(f"блоки «{taken[key]}» и «{n.get('id')}» занимают одну ячейку: дорожка «{key[0]}», ряд {row}, колонка {c}")
            taken[key] = n.get("id")
        if not n.get("title") and kind not in EVENTS:
            warnings.append(f"блок «{n.get('id')}» без заголовка «title»")
    for e in spec.get("edges") or []:
        for end in ("from", "to"):
            v = e.get(end)
            if v != "result" and v not in ids:
                errors.append(f"стрелка {e.get('from')}→{e.get('to')}: блока «{v}» нет")
        if e.get("from") == "result":
            errors.append("из блока «result» стрелки не выходят: он всегда конечный")
        if e.get("to") == "result" and not spec.get("result"):
            errors.append("стрелка ведёт в «result», но блок результата не задан")
        if e.get("kind", "flow") not in EDGE_KINDS:
            violations.append(f"стрелка {e.get('from')}→{e.get('to')}: вид «{e.get('kind')}» неизвестен, нарисована как «flow»; "
                              f"допустимые виды: {', '.join(EDGE_KINDS)}")
        if e.get("route", "auto") not in ROUTES:
            violations.append(f"стрелка {e.get('from')}→{e.get('to')}: маршрут «{e.get('route')}» неизвестен, выбран автоматически")
    if ncols > MAX_COLS:
        violations.append(f"{ncols} колонок при пределе {MAX_COLS}: объедини этапы или раздели процесс на две схемы")
    if len(lanes) > MAX_LANES:
        violations.append(f"{len(lanes)} дорожек при пределе {MAX_LANES}: объедини роли, которые делают в процессе одно и то же")
    if len(nodes) > MAX_NODES:
        violations.append(f"{len(nodes)} блоков при пределе {MAX_NODES}: объедини шаги или раздели схему на две")
    for m in spec.get("metrics") or []:
        label = str(m).split(": ", 1)[0]
        if ": " in str(m) and TRUD_RE.match(label.strip()):
            violations.append(f"показатель «{label}»: пиши «Трудозатраты …» (например, «Трудозатраты людей: …»), а не «Труд …»")
    texts = [spec.get(k) for k in ("title", "subtitle", "caption")] + [n.get(k) for n in nodes for k in ("title", "text", "note")]
    for t in texts:
        for hit in trud_hits(t):
            violations.append(f"термин: «…{hit}…» — пиши «трудозатраты»")


TRUD_RE = re.compile(r"(?<![\w-])труд(?:а|у|ом|е|ы|ов|ам|ами|ах)?(?![\w-])", re.I)
TRUD_OK = ("охран", "оплат", "производительн", "разделени", "рын", "инспекц", "услови", "нормирован", "безопасност",
           "гигиен", "кодекс", "орден", "ветеран", "министерств", "минтруд")


def trud_hits(text):
    """Термин навыка — «трудозатраты»; слово «труд» допускается только в устойчивых сочетаниях («охрана труда»)."""
    out = []
    t = str(text or "")
    for m in TRUD_RE.finditer(t):
        before = t[max(0, m.start() - 24):m.start()].lower().split()
        if before and before[-1].startswith(TRUD_OK):
            continue
        out.append(t[max(0, m.start() - 30):m.end() + 30].strip())
    return out


def metric_labels(spec):
    """Подписи показателей до двоеточия: состав и порядок должны совпадать на всех схемах процесса.
    Пометки без двоеточия («оценка») в сверку не входят: на одной схеме значения могут быть замерены, на другой — гипотезы."""
    return [str(m).split(": ", 1)[0].strip() for m in spec.get("metrics") or [] if ": " in str(m)]


def validate_set(specs, violations, warnings):
    """Схемы одного процесса стоят на одной сетке: те же дорожки, тот же результат и ответственный,
    тот же состав показателей. Расхождение — нарушение правила (--strict его отклоняет)."""
    if len(specs) < 2:
        return
    s0 = specs[0]
    first = [(l.get("id"), l.get("label")) for l in s0.get("lanes") or []]
    r0 = s0.get("result") or {}
    for i, sp in enumerate(specs[1:], 2):
        lanes = [(l.get("id"), l.get("label")) for l in sp.get("lanes") or []]
        if lanes != first:
            violations.append(f"схема {i}: дорожки отличаются от схемы 1 — id, подписи и порядок должны совпадать")
        ri = sp.get("result") or {}
        if ri.get("items") != r0.get("items"):
            violations.append(f"схема {i}: состав блока результата отличается от схемы 1")
        if ri.get("footer") != r0.get("footer"):
            violations.append(f"схема {i}: ответственный в блоке результата («footer») отличается от схемы 1: "
                              f"«{ri.get('footer', '')}» против «{r0.get('footer', '')}»")
        if ri.get("heading") != r0.get("heading") or ri.get("title") != r0.get("title"):
            warnings.append(f"схема {i}: заголовок блока результата отличается от схемы 1")
        if metric_labels(sp) != metric_labels(s0):
            violations.append(f"схема {i}: показатели другого состава или порядка, чем у схемы 1: "
                              f"{metric_labels(sp)} против {metric_labels(s0)}; значения могут различаться, подписи — нет")



# ---------- раскладка ----------
class Box:
    __slots__ = ("id", "x", "y", "w", "h", "lane", "kind", "spec", "lines")

    @property
    def r(self): return self.x + self.w
    @property
    def b(self): return self.y + self.h
    @property
    def cx(self): return self.x + self.w / 2
    @property
    def cy(self): return self.y + self.h / 2


def result_lines(rs):
    head = wrap(rs.get("heading", ""), 24)
    items = [wrap(it, 24) for it in rs.get("items") or []]
    foot = wrap(rs.get("footer", ""), 30)
    need = 26 + 8 + 16 * len(head) + 14 + sum(14 * len(i) + 10 for i in items) + 14 * len(foot) + 26
    return head, items, foot, need


def layout(spec, min_cols=0):
    lanes = spec["lanes"]
    ncols = max(columns_count(spec), min_cols)
    nodes = spec.get("nodes") or []
    has_result = bool(spec.get("result"))
    notes = []
    title_h = 0
    if spec.get("title") or spec.get("eyebrow"):
        title_h = 48 + (18 if spec.get("subtitle") else 0) + (14 if spec.get("eyebrow") else 0)
    # легенда: только использованные виды блоков; если рядом с заголовком тесно — своей строкой
    present = {n.get("kind", "human") for n in nodes}
    legend = [k for k in NODE_KINDS if k in present and k not in EVENTS] if spec.get("legend", True) else []
    legend_w = sum(text_width(NODE_KINDS[k]["name"], 10.5) + 42 for k in legend)
    grid_right = LABELW + ncols * COLW
    width = grid_right + (GAP + RESW if has_result else 0) + MARGIN
    legend_y, legend_rows = MARGIN + 14, [legend] if legend else []
    if legend:
        title_w = max(text_width(str(spec.get("title", "")), 21, True), text_width(str(spec.get("subtitle", "")), 12.5))
        if MARGIN + title_w + 30 + legend_w > width:
            # своими строками под заголовком; строк столько, чтобы каждая уместилась в ширину схемы
            legend_rows, row, row_w = [], [], 0.0
            for k in legend:
                w = text_width(NODE_KINDS[k]["name"], 10.5) + 42
                if row and row_w + w > width - 2 * MARGIN:
                    legend_rows.append(row)
                    row, row_w = [], 0.0
                row.append(k)
                row_w += w
            legend_rows.append(row)
            title_h = max(title_h, 20) + 22 + 20 * (len(legend_rows) - 1)
            legend_y = MARGIN + title_h - 12 - 20 * (len(legend_rows) - 1)
    top = MARGIN + title_h
    hh = head_height(spec)
    boxes, lane_geom = {}, []
    y = top + hh
    by_lane = {l["id"]: [] for l in lanes}
    for n in nodes:
        if n.get("lane") in by_lane:
            by_lane[n["lane"]].append(n)
    for l in lanes:
        ns = by_lane[l["id"]]
        label_lines = len(wrap(l.get("label", l["id"]), 16)) + (len(wrap(l.get("note", ""), 20)) if not ns else 0)
        if not ns:
            h = max(int(l.get("height", 76)), label_lines * 15 + 20)
            lane_geom.append([y, h]); y += h
            continue
        rows = max(int(n.get("row", 0)) for n in ns) + 1
        row_h = [BOXH] * rows
        prepared = []
        for n in ns:
            kind = n.get("kind", "human") if n.get("kind", "human") in NODE_KINDS else "human"
            w = int(n.get("span", 1)) * COLW - GAP
            maxc, maxc_bold = max(8, int((w - 18) / 6.4)), max(8, int((w - 18) / 6.9))
            if kind in EVENTS:
                lines = [("bt", s) for s in wrap(n.get("title", ""), 22)]
                need = 28 + len(lines) * LH + 6
            elif kind == "gateway":
                lines = [("bt", s) for s in wrap(n.get("title", ""), max(6, int((w - 18) / 11)))]
                need = BOXH
            else:
                lines = [("bt", s) for s in wrap(n.get("title", ""), maxc_bold)]
                lines += [("bb", s) for s in wrap(n.get("text", ""), maxc)]
                lines += [("bg", s) for s in wrap(n.get("tag", ""), maxc + 4)]
                lines += [("bn", s) for s in wrap(n.get("note", ""), maxc)]
                need = len(lines) * LH + 18
                sizes = {"bt": 11.5, "bb": 11.5, "bg": 9.5, "bn": 10.5}
                for cls, s in lines:
                    if text_width(s, sizes[cls], cls in ("bt", "bn")) > w - 12:
                        notes.append(f"блок «{n['id']}»: строка «{s}» шире блока; разбей слово или сократи текст")
            r = int(n.get("row", 0))
            row_h[r] = max(row_h[r], need)
            prepared.append((n, kind, w, lines))
        lane_h = max(LPAD * 2 + sum(row_h) + ROWGAP * (rows - 1), label_lines * 15 + 20)
        lane_geom.append([y, lane_h])
        for n, kind, w, lines in prepared:
            b = Box()
            b.id, b.lane, b.kind, b.spec, b.lines = n["id"], l["id"], kind, n, lines
            r = int(n.get("row", 0))
            b.x = LABELW + int(n.get("col", 0)) * COLW + GAP / 2
            b.y = y + LPAD + sum(row_h[:r]) + ROWGAP * r
            b.w, b.h = w, row_h[r]
            if kind in EVENTS:          # событие: круг 28px по центру ячейки, подпись под ним
                b.x, b.w = b.cx - 14, 28
            boxes[n["id"]] = b
        y += lane_h
    lanes_bottom = y
    if has_result:
        # блок результата не короче своего содержимого: при нехватке высоты растёт последняя дорожка
        _, _, _, need = result_lines(spec["result"])
        have = lanes_bottom - (top + hh)
        if need > have:
            lane_geom[-1][1] += need - have
            lanes_bottom += need - have
        rb = Box()
        rb.id, rb.lane, rb.kind, rb.spec, rb.lines = "result", None, "result", spec["result"], []
        rb.x, rb.y, rb.w, rb.h = grid_right + GAP, top + hh, RESW, lanes_bottom - (top + hh)
        boxes["result"] = rb
    height = lanes_bottom + (34 if spec.get("metrics") else 0) + MARGIN
    return dict(boxes=boxes, lane_geom=[tuple(g) for g in lane_geom], top=top, hh=hh, ncols=ncols, grid_right=grid_right,
                width=width, height=height, lanes_bottom=lanes_bottom, legend=legend_rows, legend_y=legend_y,
                boundaries=[ly for ly, _ in lane_geom[1:]], notes=notes)


# ---------- маршруты стрелок ----------
def snap_y(y1, y2, boundaries):
    """Подпись вертикального отрезка ставится на ближайшую к началу стрелки границу дорожек:
    там нет блоков, а две стрелки из разных дорожек в один блок получают разные подписи.
    Если границ внутри отрезка нет — на середину."""
    lo, hi = min(y1, y2) + 6, max(y1, y2) - 6
    cands = [b for b in boundaries if lo <= b <= hi]
    return min(cands, key=lambda b: abs(b - y1)) if cands else (y1 + y2) / 2


def seg_hits_box(p, q, b, pad=2):
    (x1, y1), (x2, y2) = p, q
    if abs(x1 - x2) < 0.5:  # вертикальный отрезок
        lo, hi = min(y1, y2), max(y1, y2)
        return b.x + pad < x1 < b.r - pad and lo < b.b - pad and hi > b.y + pad
    lo, hi = min(x1, x2), max(x1, x2)
    return b.y + pad < y1 < b.b - pad and lo < b.r - pad and hi > b.x + pad


def path_hits(pts, boxes, skip):
    out = []
    for b in boxes.values():
        if b.id in skip or b.kind == "result":
            continue
        if any(seg_hits_box(pts[i], pts[i + 1], b) for i in range(len(pts) - 1)):
            out.append(b.id)
    return out


def _v(s, d, boundaries):
    if not (d.x <= s.cx <= d.r or s.x <= d.cx <= s.r) or not (s.b <= d.y or d.b <= s.y):
        return None
    x = s.cx if d.x <= s.cx <= d.r else d.cx
    y1, y2 = (s.b, d.y) if s.cy < d.cy else (s.y, d.b)
    return [(x, y1), (x, y2)], (x + 7, snap_y(y1, y2, boundaries), "start")


def _h(s, d, boundaries):
    if abs(s.cy - d.cy) > 1 or not (d.x >= s.r or d.r <= s.x):
        return None
    if d.x >= s.r:
        return [(s.r, s.cy), (d.x, s.cy)], ((s.r + d.x) / 2, s.cy - 8, "middle")
    return [(s.x, s.cy), (d.r, s.cy)], ((s.x + d.r) / 2, s.cy - 8, "middle")


def _hvh(s, d, boundaries, side):
    gx = s.r + GAP / 2 if side == "right" else s.x - GAP / 2
    xs = s.r if side == "right" else s.x
    if d.x >= gx:
        pts = [(xs, s.cy), (gx, s.cy), (gx, d.cy), (d.x, d.cy)]
    elif d.r <= gx:
        pts = [(xs, s.cy), (gx, s.cy), (gx, d.cy), (d.r, d.cy)]
    else:  # зазор приходится на ширину блока-получателя: входим сверху или снизу
        ye = d.y if s.cy < d.cy else d.b
        pts = [(xs, s.cy), (gx, s.cy), (gx, ye)]
    return pts, (gx, snap_y(s.cy, d.cy, boundaries), "middle")


def _vh(s, d, boundaries):
    down = s.cy < d.cy
    x, y1 = s.cx, (s.b if down else s.y)
    xe = d.x if d.x >= x else d.r
    return [(x, y1), (x, d.cy), (xe, d.cy)], (x + 7, snap_y(y1, d.cy, boundaries), "start")


def _hv(s, d, boundaries):
    down = s.cy < d.cy
    xs = s.r if d.cx >= s.cx else s.x
    ye = d.y if down else d.b
    return [(xs, s.cy), (d.cx, s.cy), (d.cx, ye)], (d.cx + 7, snap_y(s.cy, ye, boundaries), "start")


def route_edge(s, d, r, boundaries, boxes):
    """Возвращает (точки, (x, y, выравнивание подписи), препятствия на пути)."""
    if d.kind == "result":
        return [(s.r, s.cy), (d.x, s.cy)], ((s.r + d.x) / 2, s.cy - 8, "middle"), []
    toward = "right" if d.cx >= s.cx else "left"
    other = "left" if toward == "right" else "right"
    makers = {
        "v": lambda: _v(s, d, boundaries), "h": lambda: _h(s, d, boundaries),
        "vh": lambda: _vh(s, d, boundaries), "hv": lambda: _hv(s, d, boundaries),
        "hvh": lambda: _hvh(s, d, boundaries, toward),
    }
    if r in makers:  # маршрут задан явно
        res = makers[r]() or _hvh(s, d, boundaries, toward)
        return res[0], res[1], path_hits(res[0], boxes, {s.id, d.id})
    cands = [_v(s, d, boundaries), _h(s, d, boundaries), _hvh(s, d, boundaries, toward),
             _hvh(s, d, boundaries, other), _vh(s, d, boundaries), _hv(s, d, boundaries)]
    best = None
    for c in cands:
        if not c:
            continue
        hits = path_hits(c[0], boxes, {s.id, d.id})
        if not hits:
            return c[0], c[1], []
        if best is None or len(hits) < len(best[2]):
            best = (c[0], c[1], hits)
    return best


def _overlap(p, q, r, t, tol=2.0, min_len=10):
    """Длина общего участка двух отрезков, лежащих на одной прямой (0, если общего участка нет)."""
    if abs(p[0] - q[0]) < 0.5 and abs(r[0] - t[0]) < 0.5 and abs(p[0] - r[0]) < tol:
        lo, hi = max(min(p[1], q[1]), min(r[1], t[1])), min(max(p[1], q[1]), max(r[1], t[1]))
    elif abs(p[1] - q[1]) < 0.5 and abs(r[1] - t[1]) < 0.5 and abs(p[1] - r[1]) < tol:
        lo, hi = max(min(p[0], q[0]), min(r[0], t[0])), min(max(p[0], q[0]), max(r[0], t[0]))
    else:
        return 0
    return hi - lo if hi - lo > min_len else 0


def _may_share(a, b):
    """Общий ствол допустим у стрелок одного вида из одного блока или в один блок: это развилка или слияние."""
    return a["kind"] == b["kind"] and (a["s"].id == b["s"].id or a["d"].id == b["d"].id)


def _conflicts(rt, routes):
    out = []
    for other in routes:
        if other is rt or _may_share(rt, other):
            continue
        for i in range(len(rt["pts"]) - 1):
            for j in range(len(other["pts"]) - 1):
                if _overlap(rt["pts"][i], rt["pts"][i + 1], other["pts"][j], other["pts"][j + 1]):
                    out.append((i, other))
    return out


def _shift(rt, i, delta):
    """Сдвигает отрезок i маршрута поперёк его направления; None, если стрелка оторвётся от блока или вывернется."""
    pts = [list(p) for p in rt["pts"]]
    vertical = abs(pts[i][0] - pts[i + 1][0]) < 0.5
    k = 0 if vertical else 1
    for j in (i, i + 1):
        pts[j][k] += delta
    lo_box = lambda b: (b.x + 8, b.r - 8) if vertical else (b.y + 8, b.b - 8)
    if i == 0:
        lo, hi = lo_box(rt["s"])
        if not lo <= pts[0][k] <= hi:
            return None
    if i + 1 == len(pts) - 1:
        lo, hi = lo_box(rt["d"])
        if not lo <= pts[-1][k] <= hi:
            return None
    old = rt["pts"]
    for a, b in ((i - 1, i), (i + 1, i + 2)):  # соседние отрезки не должны схлопнуться или развернуться
        if 0 <= a and b < len(pts):
            was, now = old[b][k] - old[a][k], pts[b][k] - pts[a][k]
            if abs(now) < 6 or (was > 0) != (now > 0):
                return None
    return [tuple(p) for p in pts]


def _try_improve(rt, routes, boxes):
    """Один сдвиг участка стрелки, после которого общих участков с другими стрелками становится меньше."""
    conf = _conflicts(rt, routes)
    for i in sorted(set(i for i, _ in conf)):
        for delta in (9, -9, 16, -16, 24, -24):
            pts = _shift(rt, i, delta)
            if pts is None or set(path_hits(pts, boxes, {rt["s"].id, rt["d"].id})) - set(rt["hits"]):
                continue
            if len(_conflicts(dict(rt, pts=pts), routes)) >= len(conf):
                continue
            p, q = rt["pts"][i], rt["pts"][i + 1]
            lx, ly, _ = rt["lab"]
            if abs(p[0] - q[0]) < 0.5 and abs(lx - p[0]) <= 8 and min(p[1], q[1]) - 1 <= ly <= max(p[1], q[1]) + 1:
                rt["lab"][0] += delta
            elif abs(p[1] - q[1]) < 0.5 and abs(ly - p[1]) <= 9 and min(p[0], q[0]) - 1 <= lx <= max(p[0], q[0]) + 1:
                rt["lab"][1] += delta
            rt["pts"] = pts
            return True
    return False


def separate_overlaps(routes, boxes):
    """Если две стрелки легли на одну линию, сдвигает общий участок одной из них на несколько пикселей."""
    for rt in routes:
        for _ in range(4):
            if not _conflicts(rt, routes) or not _try_improve(rt, routes, boxes):
                break
    notes, seen = [], set()
    for rt in routes:
        for _, other in _conflicts(rt, routes):
            key = frozenset((rt["name"], other["name"]))
            if key not in seen:
                seen.add(key)
                notes.append(f"стрелки {rt['name']} и {other['name']} идут по одной линии и сливаются; "
                             f"задай route одной из них или переставь блоки")
    return notes


# ---------- подписи стрелок ----------
def label_rect(x, y, anchor, text):
    ls = wrap(text, 22)
    w = max(len(s) for s in ls) * 6.1 + 10
    h = len(ls) * 13 + 5
    rx = x - 3 if anchor == "start" else x - w + 3 if anchor == "end" else x - w / 2
    return (rx, y - h / 2, rx + w, y + h / 2), ls


def rects_overlap(a, b, pad=2):
    # pad > 0 — требуемый зазор, pad < 0 — допустимое касание (подпись может лечь на рамку блока на 3px)
    return a[0] < b[2] + pad and a[2] > b[0] - pad and a[1] < b[3] + pad and a[3] > b[1] - pad


def place_labels(raw, boxes):
    """Разводит подписи: сдвигает вдоль линии, пока подпись не перестанет задевать блоки и другие подписи."""
    placed, notes = [], []
    box_rects = [(b.x, b.y, b.r, b.b) for b in boxes.values()]
    for x, y, anchor, text, name in raw:
        chosen = None
        # сначала на своей линии со сдвигом вдоль неё, затем сбоку от вертикального участка, затем дальше вдоль линии
        side = [(x + 7, "start"), (x - 7, "end")] if anchor == "middle" else [(x - 14, "end")] if anchor == "start" else []
        cands = [(x, y + dy, anchor) for dy in (0, -16, 16, -32, 32)]
        cands += [(sx, y + dy, sa) for dy in (0, -16, 16, -32, 32) for sx, sa in side]
        cands += [(x, y + dy, anchor) for dy in (-48, 48, -64, 64)]
        for cx, cy, ca in cands:
            rect, ls = label_rect(cx, cy, ca, text)
            if any(rects_overlap(rect, r, -3) for r in box_rects) or any(rects_overlap(rect, p[0]) for p in placed):
                continue
            chosen = (rect, ls, cx, cy, ca)
            break
        if chosen is None:
            rect, ls = label_rect(x, y, anchor, text)
            chosen = (rect, ls, x, y, anchor)
            notes.append(f"подпись «{text}» стрелки {name} задевает блок или другую подпись; задай dx/dy, сократи подпись или смени route")
        placed.append(chosen)
    return placed, notes


# ---------- отрисовка ----------
def attr(v):
    """Значение для атрибута в двойных кавычках: экранирует &, <, > и саму кавычку."""
    return escape(str(v), {'"': "&quot;"})


def svg_text(x, y, s, size, fill=INK, weight="normal", anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{escape(str(s))}</text>')


def render_svg(spec, n_index=0, min_cols=0):
    L = layout(spec, min_cols)
    boxes, W, H = L["boxes"], L["width"], L["height"]
    notes = list(L["notes"])
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
         f'font-family="{FONT}" role="img" aria-label="{attr(spec.get("title", "Схема процесса"))}">']
    o.append(f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="{BG}"/>')
    o.append("<defs>")
    for k, ek in EDGE_KINDS.items():
        o.append(f'<marker id="ah-{n_index}-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                 f'orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" fill="{ek["color"]}"/></marker>')
    o.append("</defs>")
    # заголовок
    ty = MARGIN + 8
    if spec.get("eyebrow"):
        o.append(svg_text(MARGIN, ty + 8, str(spec["eyebrow"]).upper(), 10.5, AGENT, "bold", extra=' letter-spacing="1.2"'))
        ty += 14
    if spec.get("title"):
        o.append(svg_text(MARGIN, ty + 22, spec["title"], 21, INK, "bold"))
        ty += 26
    if spec.get("subtitle"):
        o.append(svg_text(MARGIN, ty + 16, spec["subtitle"], 12.5, INK2))
    # легенда
    for ri, row in enumerate(L["legend"]):
        x, gy = W - MARGIN, L["legend_y"] + 20 * ri
        for k in reversed(row):
            kd = NODE_KINDS[k]
            x -= text_width(kd["name"], 10.5)
            o.append(svg_text(x, gy, kd["name"], 10.5, INK2))
            x -= 26
            dash = f' stroke-dasharray="{kd["dash"]}"' if kd["dash"] else ""
            if k == "gateway":
                o.append(f'<polygon points="{x + 11},{gy - 10} {x + 22},{gy - 3} {x + 11},{gy + 4} {x},{gy - 3}" '
                         f'fill="{kd["fill"]}" stroke="{kd["stroke"]}" stroke-width="{kd["sw"]}"/>')
            else:
                o.append(f'<rect x="{x:.1f}" y="{gy - 9}" width="22" height="12" rx="3" fill="{kd["fill"]}" '
                         f'stroke="{kd["stroke"]}" stroke-width="{kd["sw"]}"{dash}/>')
            x -= 16
    # дорожки и их подписи (заметка пустой дорожки — под подписью, в столбце подписей)
    for i, (ly, lh) in enumerate(L["lane_geom"]):
        lane = spec["lanes"][i]
        o.append(f'<rect x="{MARGIN}" y="{ly:.0f}" width="{L["grid_right"] - MARGIN:.0f}" height="{lh:.0f}" '
                 f'fill="{LANE_A if i % 2 == 0 else LANE_B}"/>')
        o.append(f'<line x1="{MARGIN}" x2="{L["grid_right"]}" y1="{ly:.0f}" y2="{ly:.0f}" stroke="{RULE}" stroke-width="1"/>')
        ls = [("l", s) for s in wrap(lane.get("label", lane["id"]), 16)]
        if lane.get("note") and not any(b.lane == lane["id"] for b in boxes.values()):
            ls += [("n", s) for s in wrap(lane["note"], 20)]
        y0 = ly + lh / 2 - (len(ls) - 1) * 7.5 + 4
        for k, (cls, s) in enumerate(ls):
            if cls == "l":
                o.append(svg_text(MARGIN + 14, y0 + k * 15, s, 12, INK, "bold"))
            else:
                o.append(svg_text(MARGIN + 14, y0 + k * 15, s, 10.5, INK2))
    lb = L["lanes_bottom"]
    o.append(f'<line x1="{MARGIN}" x2="{L["grid_right"]}" y1="{lb:.0f}" y2="{lb:.0f}" stroke="{RULE}" stroke-width="1"/>')
    o.append(f'<line x1="{LABELW - 6}" x2="{LABELW - 6}" y1="{L["top"] + L["hh"]}" y2="{lb:.0f}" stroke="{RULE}" stroke-width="1"/>')
    # этапы (заголовки колонок) и подписи ритма под ними
    c, hy = 0, L["top"] + L["hh"]
    has_notes = L["hh"] > HEADH
    for label, span, pnote in phases_list(spec):
        x0, x1 = LABELW + c * COLW + GAP / 2, LABELW + (c + span) * COLW - GAP / 2
        o.append(svg_text((x0 + x1) / 2, hy - (34 if has_notes else 20), label.upper(), 9.5, INK2, anchor="middle", extra=' letter-spacing="0.8"'))
        if pnote:
            o.append(svg_text((x0 + x1) / 2, hy - 20, pnote, 10, WAIT, anchor="middle"))
            if text_width(pnote, 10) > x1 - x0 + GAP:
                notes.append(f"этап «{label}»: подпись ритма «{pnote}» шире колонки; сократи её")
        if text_width(label.upper(), 9.5) > x1 - x0 + GAP:
            notes.append(f"этап «{label}»: подпись шире колонки; сократи её")
        if span > 1:
            o.append(f'<path d="M{x0:.1f} {hy - 4} V{hy - 10} H{x1:.1f} V{hy - 4}" fill="none" stroke="{RULE_STRONG}" stroke-width="1"/>')
        c += span
    # стрелки: обход блоков, разведение линий, которые легли друг на друга, замечания о том, что не вышло
    routes = []
    for e in spec.get("edges") or []:
        s, d = boxes[e["from"]], boxes[e["to"]]
        kind = e.get("kind", "flow") if e.get("kind", "flow") in EDGE_KINDS else "flow"
        pts, (lx, ly_, anchor), hits = route_edge(s, d, e.get("route", "auto"), L["boundaries"], boxes)
        routes.append({"e": e, "s": s, "d": d, "kind": kind, "pts": pts, "lab": [lx, ly_, anchor], "hits": hits,
                       "name": f"{e['from']}→{e['to']}"})
    notes += separate_overlaps(routes, boxes)
    raw_labels = []
    for rt in routes:
        e, kind, pts, name = rt["e"], rt["kind"], rt["pts"], rt["name"]
        ek = EDGE_KINDS[kind]
        if rt["hits"]:
            notes.append(f"стрелка {name} проходит через блок(и) {', '.join(rt['hits'])}; переставь блоки или задай route")
        dpath = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        dash = f' stroke-dasharray="{ek["dash"]}"' if ek["dash"] else ""
        ms = f' marker-start="url(#ah-{n_index}-{kind})"' if e.get("both") else ""
        o.append(f'<path d="{dpath}" fill="none" stroke="{ek["color"]}" stroke-width="{ek["sw"]}" '
                 f'stroke-linejoin="round"{dash} marker-end="url(#ah-{n_index}-{kind})"{ms}/>')
        if e.get("label"):
            lx, ly_, anchor = rt["lab"]
            raw_labels.append((lx + float(e.get("dx", 0)), ly_ + float(e.get("dy", 0)), anchor, str(e["label"]), name))
    # блоки
    for b in boxes.values():
        if b.kind == "result":
            continue
        kd = NODE_KINDS[b.kind]
        dash = f' stroke-dasharray="{kd["dash"]}"' if kd["dash"] else ""
        if b.kind in EVENTS:
            o.append(f'<circle cx="{b.cx:.1f}" cy="{b.y + 14:.1f}" r="13" fill="{kd["fill"]}" stroke="{kd["stroke"]}" stroke-width="{kd["sw"]}"/>')
            for i, (_, s) in enumerate(b.lines):
                o.append(svg_text(b.cx, b.y + 42 + i * LH, s, 11, INK2, anchor="middle"))
            continue
        if b.kind == "gateway":
            o.append(f'<polygon points="{b.cx:.1f},{b.y:.1f} {b.r:.1f},{b.cy:.1f} {b.cx:.1f},{b.b:.1f} {b.x:.1f},{b.cy:.1f}" '
                     f'fill="{kd["fill"]}" stroke="{kd["stroke"]}" stroke-width="{kd["sw"]}"/>')
            top = b.cy - len(b.lines) * LH / 2 + LH * 0.72
            for i, (_, s) in enumerate(b.lines):
                o.append(svg_text(b.cx, top + i * LH, s, 11.5, INK, "bold", anchor="middle"))
            continue
        o.append(f'<rect x="{b.x:.1f}" y="{b.y:.1f}" width="{b.w:.1f}" height="{b.h:.1f}" rx="7" fill="{kd["fill"]}" '
                 f'stroke="{kd["stroke"]}" stroke-width="{kd["sw"]}"{dash}/>')
        top = b.cy - len(b.lines) * LH / 2 + LH * 0.72
        for i, (cls, s) in enumerate(b.lines):
            if cls == "bt":
                o.append(svg_text(b.x + 9, top + i * LH, s, 11.5, kd["ink"], "bold"))
            elif cls == "bb":
                o.append(svg_text(b.x + 9, top + i * LH, s, 11.5, INK))
            elif cls == "bg":
                o.append(svg_text(b.x + 9, top + i * LH, s, 9.5, INK2))
            else:
                o.append(svg_text(b.x + 9, top + i * LH, s, 10.5, WAIT, "bold"))
    # подписи стрелок поверх всего, разведённые
    placed, lnotes = place_labels(raw_labels, boxes)
    notes += lnotes
    for rect, ls, x, y, anchor in placed:
        tx = x + 2 if anchor == "start" else x - 2 if anchor == "end" else x
        o.append(f'<rect x="{rect[0]:.1f}" y="{rect[1]:.1f}" width="{rect[2] - rect[0]:.1f}" height="{rect[3] - rect[1]:.1f}" '
                 f'rx="3" fill="{SURFACE}" stroke="{RULE}" stroke-width="0.6"/>')
        for i, s in enumerate(ls):
            o.append(svg_text(tx, y - (len(ls) - 1) * 13 / 2 + 3.8 + i * 13, s, 10.5, INK2, anchor=anchor))
    # блок результата
    if "result" in boxes:
        rb, rs = boxes["result"], spec["result"]
        head, items, foot, _ = result_lines(rs)
        o.append(f'<rect x="{rb.x:.1f}" y="{rb.y:.1f}" width="{rb.w}" height="{rb.h:.1f}" rx="12" fill="{RES_BG}"/>')
        yy = rb.y + 26
        o.append(svg_text(rb.x + 18, yy, str(rs.get("title", "Результат")).upper(), 10, RES_ACCENT, "bold", extra=' letter-spacing="1.2"'))
        yy += 8
        for s in head:
            yy += 16
            o.append(svg_text(rb.x + 18, yy, s, 13, SURFACE, "bold"))
        yy += 14
        for ls in items:
            o.append(f'<circle cx="{rb.x + 26}" cy="{yy + 4:.1f}" r="6" fill="none" stroke="{RES_ACCENT}" stroke-width="1.3"/>')
            o.append(f'<path d="M{rb.x + 23:.1f} {yy + 4:.1f} l2.2 2.2 l4-4.2" fill="none" stroke="{RES_ACCENT}" stroke-width="1.3"/>')
            for i, s in enumerate(ls):
                o.append(svg_text(rb.x + 40, yy + 8 + i * 14, s, 11.5, SURFACE))
            yy += 14 * len(ls) + 10
        for i, s in enumerate(foot):
            o.append(svg_text(rb.x + 18, rb.b - 16 - (len(foot) - 1 - i) * 14, s, 11, RES_MUTED))
    # показатели под схемой
    if spec.get("metrics"):
        x, y = MARGIN + 4, lb + 24
        for item in spec["metrics"]:
            item = str(item)
            if ": " in item:
                k, v = item.split(": ", 1)
                o.append(svg_text(x, y, k + ": ", 12, INK2))
                x += text_width(k + ": ", 12)
                o.append(svg_text(x, y, v, 12, INK, "bold"))
                x += text_width(v, 12, True) + 22
            else:  # пометка без значения («оценка») — плашкой, чтобы не висела словом в строке
                w = text_width(item, 10.5) + 14
                o.append(f'<rect x="{x:.1f}" y="{y - 12:.1f}" width="{w:.1f}" height="17" rx="8.5" fill="{WAIT_BG}" '
                         f'stroke="{WAIT}" stroke-width="0.8"/>')
                o.append(svg_text(x + 7, y + 0.5, item, 10.5, WAIT, "bold"))
                x += w + 22
        if x > W:
            notes.append("строка показателей шире схемы; сократи формулировки")
    o.append("</svg>")
    return "\n".join(o), W, H, notes


def render_html(specs, rendered, page_title):
    max_w = max(W for _, W, _ in rendered)
    parts = [f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>{escape(str(page_title))}</title><style>'
             f'body{{margin:0;padding:24px 16px;background:{BG};color:{INK};font-family:{FONT};font-size:15px;line-height:1.5}}'
             f'main{{max-width:1680px;margin:0 auto}} h1{{font-size:24px;margin:0 0 6px}} .lead{{color:{INK2};margin:0 0 20px}}'
             f'figure{{margin:0 0 36px}} .scroll{{overflow-x:auto;border:1px solid {RULE};border-radius:10px;background:{BG}}}'
             f'svg{{display:block;height:auto}} figcaption{{margin-top:8px;color:{INK2};font-size:14px}}'
             f'</style></head><body><main><h1>{escape(str(page_title))}</h1>']
    if len(specs) > 1:
        parts.append(f'<p class="lead">{len(specs)} схемы в одной нотации и в одном масштабе: дорожки и блок результата одинаковы, '
                     f'отличия видны по тому, где лежит работа.</p>')
    for sp, (svg, W, _) in zip(specs, rendered):
        # одна шкала для всех схем страницы: узкая схема не растягивается крупнее широкой
        style = f'width:{W / max_w * 100:.2f}%;min-width:{max(960 * W / max_w, MIN_SCALE * W):.0f}px'
        svg = svg.replace("<svg ", f'<svg style="{style}" ', 1)
        parts.append('<figure><div class="scroll">' + svg + "</div>")
        if sp.get("caption"):
            parts.append(f'<figcaption>{escape(str(sp["caption"]))}</figcaption>')
        parts.append("</figure>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


# ---------- Mermaid: запасной вид схемы для каналов, которые показывают Mermaid, но не SVG ----------
MERMAID_CLASSES = {
    "human": "fill:#FFFFFF,stroke:#A9B4BF,color:#17212B",
    "agent": "fill:#DDF0EC,stroke:#0C766E,color:#08524C",
    "rule": "fill:#E1E6EC,stroke:#5E6B7A,color:#17212B",
    "wait": "fill:#FAEBD2,stroke:#9C5F0E,stroke-dasharray:5 3,color:#17212B",
    "call": "fill:#FFFFFF,stroke:#17212B,stroke-dasharray:2 3,color:#17212B",
    "decision": "fill:#FFFFFF,stroke:#17212B,stroke-width:3px,color:#17212B",
    "gateway": "fill:#FFFFFF,stroke:#17212B,color:#17212B",
    "option": "fill:#DDF0EC,stroke:#0C766E,stroke-dasharray:5 3,color:#08524C",
    "start": "fill:#FFFFFF,stroke:#17212B,color:#17212B",
    "end": "fill:#FFFFFF,stroke:#17212B,stroke-width:3px,color:#17212B",
    "result": "fill:#17212B,stroke:#17212B,color:#FFFFFF",
    "metrics": "fill:#F2F4F6,stroke:#D3DAE1,color:#4F5B69",
}
MERMAID_ARROWS = {  # (обычная, двусторонняя)
    "flow": ("-->", "<-->"), "message": ("-.->", "<-.->"), "option": ("-.->", "<-.->"),
    "call": ("-.->", "<-.->"), "wait": ("-->", "<-->"), "decision": ("==>", "<==>"),
}


def _mm(text):
    """Текст для подписи Mermaid в кавычках: без кавычек и служебных символов, «|» — перенос строки."""
    t = str(text or "").replace('"', "#quot;").replace("<", "‹").replace(">", "›")
    return "<br/>".join(x.strip() for x in t.split("|") if x.strip())


def render_mermaid(spec):
    """Блок-схема Mermaid: дорожки — подграфы, блоки — узлы с цветом по виду, стрелки — по виду.
    Колонки этапов Mermaid не передаёт: этап пишется в начале подписи блока."""
    out = ["flowchart LR"]
    if spec.get("title"):
        out.append(f"  %% {str(spec['title']).replace(chr(10), ' ')}")
    nodes = spec.get("nodes") or []
    phases = []
    for label, span, _ in phases_list(spec):
        phases += [label] * span
    safe = {n["id"]: f"n{i}" for i, n in enumerate(nodes, 1)}
    safe["result"] = "res"
    used = set()
    for li, lane in enumerate(spec.get("lanes") or [], 1):
        members = [n for n in nodes if n.get("lane") == lane.get("id")]
        out.append(f'  subgraph L{li}["{_mm(lane.get("label", lane.get("id")))}"]')
        out.append("    direction LR")
        if not members:
            out.append(f'    L{li}_empty["{_mm(lane.get("note") or "—")}"]')
            out.append(f"    class L{li}_empty v_metrics")
        for n in sorted(members, key=lambda x: (int(x.get("col", 0)), int(x.get("row", 0)))):
            kind = n.get("kind", "human") if n.get("kind", "human") in NODE_KINDS else "human"
            col = int(n.get("col", 0))
            parts = []
            if col < len(phases) and phases[col]:
                parts.append(f"[{phases[col]}]")
            parts.append(n.get("title", ""))
            for k in ("text", "note", "tag"):
                if n.get(k):
                    parts.append(str(n[k]))
            label = _mm("|".join(str(p) for p in parts if p))
            sid = safe[n["id"]]
            shape = {"gateway": ('{"', '"}'), "start": ('(("', '"))'), "end": ('(("', '"))')}.get(kind, ('["', '"]'))
            out.append(f"    {sid}{shape[0]}{label}{shape[1]}")
            out.append(f"    class {sid} v_{kind}")
            used.add(kind)
        out.append("  end")
        out.append(f"  style L{li} fill:{LANE_A if li % 2 else LANE_B},stroke:{RULE},color:{INK}")
    rs = spec.get("result")
    if rs:
        items = "|".join("• " + str(x) for x in rs.get("items") or [])
        label = _mm("|".join(x for x in [str(rs.get("title", "Результат")), items, str(rs.get("footer", ""))] if x))
        out.append(f'  res(["{label}"])')
        out.append("  class res v_result")
        used.add("result")
    links, wait_idx = [], []
    for e in spec.get("edges") or []:
        kind = e.get("kind", "flow") if e.get("kind", "flow") in EDGE_KINDS else "flow"
        arrow = MERMAID_ARROWS[kind][1 if e.get("both") else 0]
        lab = f'|"{_mm(str(e["label"]).replace("|", " "))}"|' if e.get("label") else ""
        if kind == "wait":
            wait_idx.append(len(links))
        links.append(f"  {safe[e['from']]} {arrow}{lab} {safe[e['to']]}")
    out += links
    if spec.get("metrics"):
        out.append(f'  metrics["{_mm("|".join(str(m) for m in spec["metrics"]))}"]')
        out.append("  class metrics v_metrics")
        used.add("metrics")
    for k in MERMAID_CLASSES:
        if k in used:
            out.append(f"  classDef v_{k} {MERMAID_CLASSES[k]}")  # приставка: call и end — служебные слова Mermaid
    if wait_idx:
        out.append(f"  linkStyle {','.join(str(i) for i in wait_idx)} stroke:{WAIT},stroke-width:1.5px")
    return "\n".join(out) + "\n"


# ---------- отчёт: одна HTML-страница с выводами и схемами; листы согласования ----------
SKILL_VERSION = "1.7"
# схема на странице не мельче этой доли натурального размера: текст в блоках остаётся читаемым, лишнее прокручивается вбок
MIN_SCALE = 0.9
STATUS_NAMES = {"согласовано": "agreed", "agreed": "agreed", "условный проект": "conditional", "conditional": "conditional",
                "на согласовании": "review", "review": "review"}
ROLE_NAMES = {"как есть": "as_is", "as_is": "as_is", "помощники": "helpers", "helpers": "helpers",
              "вариант": "variant", "variant": "variant", "смелый вариант": "bold", "смелый": "bold", "bold": "bold"}
TRAITS = (("work_class", "Исчез класс работы"), ("wait", "Снято ожидание"),
          ("knowledge", "Знание закреплено"), ("result", "Результат совпал"))
AGENT_FIELDS = (("why", "Почему агент и почему отдельный"), ("trigger", "Запуск"), ("io", "Входы и выходы"),
                ("authority", "Полномочия"), ("duties", "Обязательства и цель"), ("limits", "Запреты и чем обеспечены"),
                ("calls", "Вызов человека"), ("series", "Серия и участие человека"), ("untrusted", "Чужие материалы"),
                ("evidence", "Доказательства"), ("unknown", "Неизвестно и отказ"), ("knowledge", "Знание"))
# без этих граф карточка не годится как чертёж внедрения: запрет без способа обеспечения, длинная серия без проверки и
# чтение чужих материалов рядом со значимыми действиями — главные измеренные источники сбоев (references/method.md)
AGENT_REQUIRED = ("limits", "series", "untrusted")
VARIANT_TEXTS = (("essence", "суть", 220), ("text", "рассказ", 520), ("removed", "что исчезло", 520),
                 ("human", "где человек", 520), ("risks", "условия и риски", 520))
IDEA_FIELDS = (("how", "Как получаем результат"), ("removes", "Что исчезает из работы людей"),
               ("effort", "Трудозатраты людей"), ("keeps", "Что сохраняем и риски"))
CELL_LIMIT, ITEM_LIMIT = 320, 320
# маршрут согласований постоянный: четыре согласования, затем итоговый отчёт; счёт не меняется
APPROVALS = ("Как есть и результат", "Смелые идеи и границы", "Выбор варианта", "Агенты и полномочия")
APPROVAL_STATES = {"согласовано": "ok", "допущение": "assumed", "текущее": "current", "впереди": "ahead"}
POINT_STEP = {1: 1, 2: 2, 3: 3, 4: 3}
# какие разделы показывает лист согласования; итоговый отчёт (этап 5) показывает все
STAGE_SECTIONS = {
    1: {"kratko", "rezultat", "shemy", "resheniya", "prilozheniya"},
    2: {"kratko", "idei", "ubiraem", "shemy", "resheniya"},
    3: {"kratko", "ubiraem", "shemy", "sravnenie", "resheniya"},
    4: {"kratko", "shemy", "agenty", "proverka", "resheniya", "prilozheniya"},
}

EXAMPLE_VARIANT = {
    "eyebrow": "Смелый вариант",
    "title": "Агенты собирают и пишут",
    "subtitle": "Агенты читают документы, беседуют с участниками и пишут черновик; методолог судит, заказчик принимает",
    "lanes": EXAMPLE["lanes"][:1] + [{"id": "a", "label": "ИИ-агенты"}] + EXAMPLE["lanes"][2:],
    "phases": ["Документы", "Беседы", "Синтез и отчёт", "Замечания"],
    "nodes": [
        {"id": "z1", "lane": "z", "col": 0, "kind": "human", "title": "Даёт доступ", "text": "к документам|и системам, один раз"},
        {"id": "z2", "lane": "z", "col": 1, "kind": "human", "title": "Отвечают агенту", "text": "в удобное время,|20–30 минут"},
        {"id": "z3", "lane": "z", "col": 3, "kind": "decision", "title": "Приёмка", "text": "отчёта и акт"},
        {"id": "a1", "lane": "a", "col": 0, "kind": "agent", "title": "Чтец", "text": "факты, пробелы,|расхождения", "tag": "ш. 1, 2"},
        {"id": "a2", "lane": "a", "col": 1, "kind": "agent", "title": "Собеседник", "text": "беседы по пробелам|со всеми участниками",
         "tag": "ш. 3, 4"},
        {"id": "a3", "lane": "a", "col": 2, "kind": "agent", "title": "Автор", "text": "черновик отчёта|и дорожной карты", "tag": "ш. 5"},
        {"id": "m1", "lane": "m", "col": 2, "kind": "human", "title": "Суждение", "text": "выводы, состав карты,|трудные беседы"}
    ],
    "edges": [
        {"from": "z1", "to": "a1", "kind": "message", "label": "доступ"},
        {"from": "a1", "to": "a2"},
        {"from": "a2", "to": "z2", "kind": "message", "both": True, "label": "вопросы"},
        {"from": "a2", "to": "a3"},
        {"from": "a3", "to": "m1", "kind": "call", "both": True},
        {"from": "m1", "to": "z3", "route": "hvh", "kind": "message", "label": "отчёт"},
        {"from": "z3", "to": "result", "kind": "decision"}
    ],
    "result": EXAMPLE["result"],
    "metrics": ["Срок до принятого отчёта: 3–4 недели", "Трудозатраты людей: 12–18 человеко-дней",
                "Заказчик видит выводы: в первую неделю", "оценка"]
}

EXAMPLE_REPORT = {
    "title": "Обследование системы управления проектами",
    "subtitle": "Как есть и смелый вариант: агенты собирают сведения и пишут черновик, методолог судит, заказчик принимает",
    "owner": "руководитель работ исполнителя",
    "version": "1.0",
    "date": "23 октября 2026",
    "status": "условный проект",
    "approvals": [
        {"state": "допущение", "answer": "Результат принят, когда заказчик подписал акт"},
        {"state": "допущение", "answer": "Берём обе смелые идеи; трудные беседы остаются у методолога"},
        {"state": "допущение", "answer": "Рекомендован «Агенты собирают и пишут»"},
        {"state": "допущение", "answer": "Полномочия агентов предварительные"}
    ],
    "summary": "Сейчас методолог сначала собирает документы, интервью и анкеты и только потом понимает картину; процесс трижды "
               "ждёт заказчика.\n\nВ смелом варианте **агенты читают документы, беседуют со всеми участниками в их темпе и пишут "
               "черновик** отчёта и дорожной карты. Методолог судит о выводах и ведёт трудные беседы, заказчик принимает результат.",
    "recommendation": "Рекомендуем «Агенты собирают и пишут»: трудозатраты людей падают примерно втрое при том же отчёте и акте.",
    "confirm": ["Выбор варианта", "Число интервью в договоре — обязательная форма или привычный способ"],
    "passport": [["Полезный результат", "Подтверждённая картина практики, объяснение проблем, дорожная карта"],
                 ["Ответственность", "Руководитель работ исполнителя"]],
    "removed": [["Сплошной опрос о фактах из документов", "собрать сведения", "Чтец читает документы, Собеседник спрашивает о пробелах"],
                ["Согласование календарей интервью", "организовать беседы", "участники отвечают агенту в удобное время"]],
    "schemes": [EXAMPLE, EXAMPLE_VARIANT],
    "variants": [
        {"scheme": 1, "role": "как есть", "name": "Как есть", "essence": "Собрать всё, потом понять"},
        {"scheme": None, "role": "помощники", "name": "Помощники", "essence": "Те же шаги, агент расшифровывает интервью"},
        {"scheme": 2, "role": "смелый вариант", "name": "Агенты собирают и пишут", "chosen": True,
         "essence": "Агенты собирают сведения и пишут черновик, люди судят и принимают",
         "removed": "Сплошной опрос, ожидание календарей, ручная сводка анкет", "human": "Трудные беседы, суждение о выводах, приёмка",
         "risks": "Доверие участников к беседе с агентом; приватность ответов",
         "traits": {"work_class": True, "wait": True, "knowledge": "частично: библиотека типовых решений ещё не собрана",
                    "result": True}}
    ],
    "comparison": [["Трудозатраты людей", "40–55 человеко-дней", "32–45 человеко-дней", "12–18 человеко-дней"],
                   ["из них проверка и надзор за агентами", "—", "2–3", "3–5"],
                   ["Заказчик видит выводы", "в конце", "в конце", "в первую неделю"]],
    "agents": [{"name": "Собеседник", "role": "беседует с участниками по пробелам и расхождениям",
                "why": "беседа и толкование ответов, правило и вызов модели здесь не справятся; отдельный от Чтеца — другой "
                       "источник и идёт параллельно",
                "authority": "спрашивает и сводит ответы по темам",
                "duties": "до беседы сверяет список вопросов с пробелами страницы фактов; цель — закрыть пробелы, а не набрать "
                          "число бесед",
                "limits": "не раскрывает ответы шире согласованного круга — права доступа к хранилищу ответов; выводов не делает "
                          "— инструкция, вывод проверяет методолог",
                "calls": "чувствительная тема, конфликт, просьба поговорить с человеком",
                "series": "одна беседа до записи в хранилище; методолог смотрит выборку бесед каждый день пилота (H2)",
                "untrusted": "ответы участников — чужой материал; значимых действий агент не совершает, писать может только в "
                             "хранилище ответов",
                "unknown": "нет ответа — пробел для методолога, а не вывод", "preliminary": True}],
    "checks": {"steps": ["Прогон на завершённом обследовании с закрытым итогом",
                         "Пилот на одном заказчике параллельно; на тех же материалах — «помощники» для сравнения"],
               "measures": "Трудозатраты людей вместе с проверкой и надзором, срок до акта, доля выводов, исправленных "
                           "методологом, доля перехваченных подложенных ошибок.",
               "decision": "Реальную работу передаём, если выводы не хуже прошлых обследований по оценке методолога, а "
                           "трудозатраты людей ниже вдвое; иначе пилот останавливается.",
               "critical": ["Типовой вывод выдан за установленный факт"]},
    "assumptions": ["Документы заказчика доступны в первый день"],
    "questions": ["Закреплено ли число интервью в договоре"]
}

REPORT_CSS = """
:root{--ink:#17212B;--ink2:#4F5B69;--muted:#66727F;--line:#D3DAE1;--line2:#E3E8ED;--bg:#EEF1F4;--surface:#FFFFFF;
--soft:#F6F8FA;--agent:#0C766E;--agent-bg:#DDF0EC;--agent-ink:#08524C;--wait:#9C5F0E;--wait-bg:#FAEBD2;--rule:#E1E6EC;
--crit:#A23B2A;--crit-bg:#F9E6E2;--hero:#17212B;--accent:#5FCFC1;color-scheme:light}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);overflow-x:hidden;overflow-wrap:break-word;
font:16px/1.55 "Segoe UI","PT Sans",Roboto,"Helvetica Neue",Arial,"Liberation Sans",sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
p{margin:0 0 10px}p:last-child{margin-bottom:0}
strong{font-weight:700}
.hero{background:var(--hero);color:#fff;padding:44px 0 40px;position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;right:-120px;top:-160px;width:520px;height:520px;border-radius:50%;
background:radial-gradient(closest-side,rgba(95,207,193,.16),rgba(95,207,193,0));pointer-events:none}
.hero .wrap{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:36px;align-items:end;position:relative;z-index:1}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
.hero h1{font-size:clamp(28px,3.6vw,42px);line-height:1.12;margin:12px 0 12px;font-weight:700;letter-spacing:-.01em}
.hero .sub{color:#C3CDD7;font-size:18px;line-height:1.5;margin:0 0 22px;max-width:60ch}
.meta{display:flex;flex-wrap:wrap;gap:8px 0;align-items:center;font-size:14px;color:#AEB9C4}
.meta>span{margin-right:18px}
.status{display:inline-flex;align-items:center;gap:8px;padding:5px 12px 5px 10px;border-radius:999px;font-weight:700;font-size:13px}
.status:before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.status.ok{background:rgba(95,207,193,.16);color:#86E3D7}
.status.cond{background:rgba(242,194,122,.16);color:#F2C27A}
.res{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:20px 22px}
.res h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0 0 4px}
.res .rh{font-size:15px;font-weight:700;margin:0 0 12px}
.res ul{list-style:none;margin:0;padding:0;display:grid;gap:9px}
.res li{display:grid;grid-template-columns:20px 1fr;gap:8px;font-size:15px;line-height:1.4;color:#E9EEF2}
.res li svg{margin-top:2px}
.res .rf{margin-top:14px;font-size:13px;color:#AEB9C4}
.toc{position:sticky;top:0;z-index:5;background:rgba(255,255,255,.94);-webkit-backdrop-filter:saturate(1.4) blur(8px);
backdrop-filter:saturate(1.4) blur(8px);border-bottom:1px solid var(--line)}
.toc .wrap{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none}
.toc .wrap::-webkit-scrollbar{display:none}
.toc a{flex:none;padding:13px 12px 11px;font-size:14px;color:var(--ink2);text-decoration:none;border-bottom:2px solid transparent}
.toc a:hover{color:var(--ink)}
.toc a.cur{color:var(--ink);border-color:var(--agent);font-weight:600}
main{padding:0 0 32px}
section.sec{padding:44px 0 4px;scroll-margin-top:52px}
.sh{display:flex;align-items:baseline;gap:14px;margin:0 0 18px}
.sh .num{font-size:13px;font-weight:700;color:var(--agent);letter-spacing:.08em;min-width:22px}
.sh h2{margin:0;font-size:26px;line-height:1.2;letter-spacing:-.005em}
.sh .hint{margin-left:auto;font-size:13px;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line2);border-radius:16px;padding:22px 24px;box-shadow:0 1px 2px rgba(23,33,43,.04)}
.grid2{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:16px}
.lead{font-size:18px;line-height:1.6}
.lab{font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;margin:0 0 8px}
.reco{background:var(--agent-bg);border-color:#C3E5DE;color:var(--agent-ink)}
.reco .lab{color:var(--agent)}
.reco p{font-size:17px;line-height:1.5}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:16px 0 0}
.kpi{background:var(--surface);border:1px solid var(--line2);border-radius:16px;padding:16px 18px 18px}
.kpi .k{font-size:13px;color:var(--muted);font-weight:600}
.kpi .now{font-size:20px;font-weight:700;color:var(--agent-ink);line-height:1.25;margin:6px 0 8px}
.kpi .was{font-size:13.5px;color:var(--ink2);border-top:1px dashed var(--line);padding-top:8px}
.kpi .was span{color:var(--muted)}
.kcap{display:flex;align-items:center;gap:10px;margin:22px 0 0;font-size:13px;color:var(--muted)}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:3px 9px;
border-radius:999px;background:var(--wait-bg);color:var(--wait);white-space:nowrap}
.tag.t-agent{background:var(--agent-bg);color:var(--agent-ink)}
.tag.t-dark{background:var(--agent);color:#fff}
.tag.t-grey{background:#E7EBEF;color:var(--ink2)}
.callout{background:var(--wait-bg);border-color:#EED3A8;color:#5A3807;margin-top:16px}
.callout .lab{color:var(--wait)}
.callout ol,.callout ul{margin:0;padding-left:22px}
.callout li{margin:3px 0}
.pass{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 32px;margin:0}
.pass div{padding:12px 0;border-bottom:1px solid var(--line2)}
.pass dt{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:0 0 3px}
.pass dd{margin:0}
.tw{background:var(--surface);border:1px solid var(--line2);border-radius:16px;overflow-x:auto}
table.tbl{width:100%;border-collapse:collapse;font-size:15px;line-height:1.45}
.tbl th,.tbl td{padding:12px 16px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line2)}
.tbl thead th{font-size:12px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:700;background:var(--soft);
border-bottom:1px solid var(--line)}
.tbl tbody tr:last-child>*{border-bottom:none}
.tbl tbody th{font-weight:600;color:var(--ink);min-width:160px}
.tbl .pick{background:#EEF8F6}
.tbl thead th.pick{background:var(--agent-bg);color:var(--agent-ink)}
.tbl thead th.pick small{display:block;font-size:10.5px;color:var(--agent);letter-spacing:.08em}
.note{color:var(--ink2);font-size:15px;margin:12px 2px 0}
.tabs{display:none}
.js .tabs{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.tab{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:8px 15px;font:inherit;font-size:14px;
cursor:pointer;color:var(--ink2);display:inline-flex;align-items:center;gap:8px}
.tab:hover{border-color:var(--ink2);color:var(--ink)}
.tab[aria-selected=true]{background:var(--ink);border-color:var(--ink);color:#fff}
.tab .dot{width:7px;height:7px;border-radius:50%;background:var(--accent)}
.panel{margin:0 0 34px}
.js .panel{display:none;margin-bottom:8px}
.js .panel.on{display:block;margin-bottom:34px}
.ph{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 6px}
.ph h3{font-size:22px;line-height:1.25;margin:0}
.ess{font-size:17px;color:var(--ink2);margin:0 0 12px;max-width:80ch}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 6px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:13px;padding:4px 11px 4px 8px;border-radius:999px;font-weight:600}
.chip.yes{background:var(--agent-bg);color:var(--agent-ink)}
.chip.no{background:#ECEFF2;color:var(--muted)}
.chip.part{background:var(--wait-bg);color:var(--wait)}
.pnotes{list-style:none;margin:6px 0 0;padding:0;font-size:14px;color:var(--ink2)}
.figbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:16px 0 8px;font-size:13px;color:var(--muted)}
.zoom{display:none;border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:6px 12px;font:inherit;
font-size:13px;color:var(--ink2);cursor:pointer;align-items:center;gap:6px}
.js .zoom{display:inline-flex}
.zoom:hover{border-color:var(--ink2);color:var(--ink)}
.out{position:relative;left:50%;transform:translateX(-50%);width:min(1600px,calc(100vw - 48px))}
.fig{background:#F2F4F6;border:1px solid var(--line);border-radius:14px;overflow-x:auto;margin:0}
.fig svg{display:block;height:auto}
.story{margin:16px 0 0;font-size:16px;max-width:90ch}
.minis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:16px 0 0}
.mini{background:var(--surface);border:1px solid var(--line2);border-radius:14px;padding:14px 16px;font-size:15px}
.mini .lab{color:var(--muted);font-size:11px}
.helper{display:flex;gap:12px;align-items:flex-start;background:var(--soft);border:1px dashed var(--line);border-radius:14px;
padding:14px 16px;font-size:15px;color:var(--ink2);margin:0 0 18px}
.by{display:inline-block;padding:2px 9px;border-radius:7px;font-size:13.5px;font-weight:600;line-height:1.5}
.by.agent{background:var(--agent-bg);color:var(--agent-ink)}
.by.rule{background:var(--rule);color:var(--ink)}
.by.model{background:var(--surface);color:var(--agent-ink);box-shadow:inset 0 0 0 1px var(--agent)}
.by.human{background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1px var(--line)}
.agents{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px;margin-top:16px}
.acard{border-top:4px solid var(--agent)}
.acard h3{margin:0;font-size:20px;color:var(--agent-ink);display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
.acard .ar{margin:4px 0 6px;color:var(--ink2)}
.acard dl{margin:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:0 28px}
.acard dt{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:12px 0 2px}
.acard dd{margin:0;font-size:15px}
.noag{display:flex;gap:14px;align-items:flex-start;margin-top:16px;background:var(--soft)}
.steps{list-style:none;margin:0;padding:0;display:grid;gap:12px;counter-reset:st}
.steps li{counter-increment:st;display:grid;grid-template-columns:36px 1fr;gap:14px;align-items:start;background:var(--surface);
border:1px solid var(--line2);border-radius:14px;padding:14px 18px}
.steps li:before{content:counter(st);width:30px;height:30px;border-radius:50%;background:var(--ink);color:#fff;font-weight:700;
font-size:14px;display:flex;align-items:center;justify-content:center}
.crit{background:var(--crit-bg);border-color:#EDC3BA;color:#5C1F14;margin-top:16px}
.crit .lab{color:var(--crit)}
.crit ul{margin:0;padding-left:20px}
.cols3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.cols3 .card ul{margin:0;padding-left:20px}
.cols3 .card li{margin:4px 0}
.cols3 .lab{color:var(--muted)}
details.app{background:var(--surface);border:1px solid var(--line2);border-radius:14px;margin:0 0 10px}
details.app summary{cursor:pointer;padding:15px 20px;font-weight:600;list-style:none;display:flex;align-items:center;gap:10px}
details.app summary::-webkit-details-marker{display:none}
details.app summary:before{content:"";width:8px;height:8px;border-right:2px solid var(--ink2);border-bottom:2px solid var(--ink2);
transform:rotate(-45deg);transition:transform .15s}
details.app[open] summary:before{transform:rotate(45deg)}
details.app .ab{padding:0 20px 18px}
details.app .tw{margin-top:10px}
footer{border-top:1px solid var(--line);margin-top:36px;padding:22px 0 44px;color:var(--muted);font-size:13px}
footer code{overflow-wrap:anywhere;font-size:12.5px;background:#E4E9EE;padding:1px 6px;border-radius:5px;color:var(--ink2)}
.ov{position:fixed;inset:0;background:rgba(12,18,24,.88);z-index:50;display:none;overflow:auto;padding:60px 24px 24px}
.ov.on{display:block}
.ov .box{display:inline-block;background:#F2F4F6;border-radius:12px;overflow:hidden}
.ov .box svg{display:block;max-width:none}
.ovx{position:fixed;top:14px;right:18px;border:0;background:#fff;color:var(--ink);border-radius:999px;padding:8px 16px;font:inherit;
font-size:14px;font-weight:600;cursor:pointer}
.status.rev{background:rgba(255,255,255,.12);color:#fff}
.tag.t-bold{background:var(--ink);color:var(--accent)}
.route{background:var(--surface);border-bottom:1px solid var(--line)}
.rt{list-style:none;margin:0;padding:16px 0 18px;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
.rst{display:grid;grid-template-columns:30px minmax(0,1fr);gap:10px;align-items:start;border-top:4px solid var(--line2);padding-top:12px}
.rst .mk{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;
border:2px solid var(--line);color:var(--muted);background:#fff}
.rst.ok{border-top-color:var(--agent)}.rst.ok .mk{background:var(--agent);border-color:var(--agent);color:#fff}
.rst.as{border-top-color:#E2B36B}.rst.as .mk{background:var(--wait-bg);border-color:var(--wait);color:var(--wait)}
.rst.cur{border-top-color:var(--ink)}.rst.cur .mk{background:var(--ink);border-color:var(--ink);color:#fff;box-shadow:0 0 0 4px rgba(95,207,193,.35)}
.rn{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}
.rtl{font-size:14.5px;font-weight:700;line-height:1.3;margin-top:1px}
.ra{font-size:13px;color:var(--ink2);line-height:1.35;margin-top:3px}
@media (min-width:641px){.ra{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.rst.cur .ra{color:var(--agent);font-weight:700}.rst.todo .rtl{color:var(--ink2)}.rst.todo .ra{color:var(--muted)}
.ask{border:2px solid var(--agent);background:linear-gradient(180deg,#F1FAF8,#FFFFFF 70%)}
.ask .lab{color:var(--agent)}
.ask .q{font-size:20px;font-weight:700;line-height:1.4;margin:4px 0 16px;max-width:75ch}
.opts{list-style:none;margin:0;padding:0;counter-reset:o;display:grid;gap:8px}
.opts li{counter-increment:o;display:grid;grid-template-columns:30px minmax(0,1fr);gap:12px;align-items:start;background:#fff;
border:1px solid var(--line);border-radius:12px;padding:10px 14px}
.opts li:before{content:counter(o);width:28px;height:28px;border-radius:8px;background:var(--agent-bg);color:var(--agent-ink);
font-weight:700;display:flex;align-items:center;justify-content:center;font-size:14px}
.opts li span{padding-top:3px}
.clar{margin-top:16px}.clar .lab{color:var(--muted)}.clar ul{margin:0;padding-left:20px}
.how{margin:16px 0 0;font-size:14px;color:var(--ink2)}
.ideas{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.idea{border-top:4px solid var(--line)}.idea.bold{border-top-color:var(--ink)}
.idea h3{margin:10px 0 2px;font-size:19px;line-height:1.3}
.idea dl{margin:0}.idea dt{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:12px 0 2px}
.idea dd{margin:0;font-size:15px}
details.app .ideas{margin-top:6px}
.kpi .now.asis{color:var(--ink)}
.delta{display:inline-block;font-size:13px;font-weight:700;border-radius:999px;padding:2px 10px;margin:0 0 8px;
background:var(--agent-bg);color:var(--agent-ink)}
.delta.up{background:#ECEFF2;color:var(--ink2)}
@media (max-width:900px){.hero .wrap,.grid2{grid-template-columns:1fr}.minis,.cols3{grid-template-columns:1fr}
.pass{grid-template-columns:1fr}.hero{padding:32px 0 28px}.rt{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:640px){.rv .hero .res{display:none}.rt{grid-template-columns:1fr;gap:0;padding:10px 0 12px}.rst{border-top:none;border-left:4px solid var(--line2);
padding:7px 0 7px 12px}.rst.ok{border-left-color:var(--agent)}.rst.as{border-left-color:#E2B36B}.rst.cur{border-left-color:var(--ink)}
.rst .mk{width:26px;height:26px;font-size:13px}.rst{grid-template-columns:26px minmax(0,1fr)}.ask .q{font-size:18px}.ideas{grid-template-columns:1fr}
.wrap{padding:0 16px}.out{width:calc(100vw - 32px)}.sh h2{font-size:22px}.lead{font-size:17px}
.card{padding:18px}.sh .hint{display:none}.tbl th,.tbl td{padding:10px 12px}
.tbl.st thead{display:none}.tbl.st,.tbl.st tbody,.tbl.st tr,.tbl.st th,.tbl.st td{display:block;width:100%}
.tbl.st tr{padding:12px 16px;border-bottom:1px solid var(--line2)}.tbl.st tbody tr:last-child{border-bottom:none}
.tbl.st th,.tbl.st td{padding:2px 0;border:none;background:none}.tbl.st tbody th{font-size:16px;min-width:0}
.tbl.st td[data-l]:before{content:attr(data-l);display:block;font-size:11px;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);font-weight:700;margin-top:8px}.tbl.st td.pick:before{color:var(--agent)}}
@media print{@page{size:A4 landscape;margin:12mm}
body{background:#fff;font-size:10.5pt}.toc,.tabs,.zoom,.ov,.ovx{display:none!important}
.js .panel,.panel{display:block!important;break-before:page}
.hero,.tag,.chip,.by,.steps li:before,.kpi,.reco,.callout,.crit,.rst,.rst .mk,.ask,.opts li:before,.delta,.idea{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.rt{grid-template-columns:repeat(5,minmax(0,1fr))!important}.idea,.opts li{break-inside:avoid}
.hero:after{display:none}.card,.kpi,.mini,.acard,.steps li,tr,.res,.out{break-inside:avoid}
.sh,.ph,.ess,.chips,.pnotes,.figbar,.kcap,.lab{break-after:avoid;page-break-after:avoid}
.out{left:0;transform:none;width:100%}.fig{overflow:visible;text-align:center}
.fig svg{width:auto!important;max-width:100%!important;min-width:0!important;max-height:132mm;margin:0 auto}
section.sec{padding-top:18px}}
"""

REPORT_JS = """
(function(){var d=document,tabs=[].slice.call(d.querySelectorAll('.tab')),panels=[].slice.call(d.querySelectorAll('.panel'));
function show(id){tabs.forEach(function(t){t.setAttribute('aria-selected',t.getAttribute('data-tab')===id?'true':'false')});
panels.forEach(function(p){p.classList.toggle('on',id==='all'||p.getAttribute('data-panel')===id)})}
tabs.forEach(function(t){t.addEventListener('click',function(){show(t.getAttribute('data-tab'))})});
var ov=d.getElementById('ov'),box=d.getElementById('ovbox');
function close(){ov.classList.remove('on');box.innerHTML='';d.body.style.overflow=''}
[].forEach.call(d.querySelectorAll('.zoom'),function(b){b.addEventListener('click',function(){
var s=d.querySelector('#'+b.getAttribute('data-fig')+' svg');if(!s)return;var c=s.cloneNode(true);c.removeAttribute('style');
box.appendChild(c);ov.classList.add('on');d.body.style.overflow='hidden'})});
if(ov){ov.addEventListener('click',function(e){if(e.target===ov||e.target.className==='ovx')close()})}
d.addEventListener('keydown',function(e){if(e.key==='Escape')close()});
window.addEventListener('beforeprint',function(){[].forEach.call(d.querySelectorAll('details'),function(x){x.open=true})});
var links=[].slice.call(d.querySelectorAll('.toc a'));
if('IntersectionObserver' in window&&links.length){var io=new IntersectionObserver(function(es){es.forEach(function(e){
if(e.isIntersecting){links.forEach(function(a){a.classList.toggle('cur',a.getAttribute('href')==='#'+e.target.id)})}})},
{rootMargin:'-45% 0px -50% 0px'});[].forEach.call(d.querySelectorAll('section.sec'),function(s){io.observe(s)})}})();
"""

ICON_CHECK = ('<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7.2" fill="none" '
              'stroke="currentColor" stroke-width="1.3"/><path d="M4.6 8.2l2.2 2.2 4.4-4.6" fill="none" stroke="currentColor" '
              'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICON_CROSS = ('<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" '
              'stroke-width="1.8" stroke-linecap="round"/></svg>')
ICON_PART = ('<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" '
             'stroke="currentColor" stroke-width="1.6"/><path d="M8 2a6 6 0 0 1 0 12z" fill="currentColor"/></svg>')
ICON_ZOOM = ('<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5l-4.5 '
             '4.5M2.5 13.5L7 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>')


def _inline(text):
    """Текст для вставки в разметку: всё экранируется, «**…**» — полужирный, перенос строки сохраняется."""
    h = html_escape(str(text if text is not None else "").strip(), quote=True)
    h = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", h)
    return h.replace("\n", "<br>")


def _rich(text):
    """Абзацы разделяются пустой строкой."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", str(text if text is not None else "")) if p.strip()]
    return "".join(f"<p>{_inline(p)}</p>" for p in parts)


def _metric_pairs(spec):
    out = []
    for m in spec.get("metrics") or []:
        m = str(m)
        if ": " in m:
            k, v = m.split(": ", 1)
            out.append((k.strip(), v.strip()))
    return out


def _estimate(spec):
    return any(": " not in str(m) for m in spec.get("metrics") or [])


def _by_class(text):
    """Кто выполняет функцию — по началу поля: «агент …» — агент; «вызов модели» — разовый вызов языковой модели без выбора
    пути; «правило», «обычная автоматизация», «справочник», «система» — правило; иначе роль человека. Слова в середине
    («отчёт», «расчёт» в названии роли) класс не меняют."""
    t = str(text).strip().lower()
    if t.startswith("агент"):
        return "agent"
    if t.startswith(("вызов модели", "вызов языковой модели")):
        return "model"
    if t.startswith(("правил", "обычная автоматизация", "автоматизац", "справочн", "систем")):
        return "rule"
    return "human"


_SP = "[ \u00a0\u202f\u2009]"                      # пробел, неразрывный, узкий неразрывный, тонкий
_NUM = r"\d{1,3}(?:" + _SP + r"\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_VALUE_RE = re.compile(r"^\s*(?:≈|~|около|примерно)?\s*(" + _NUM + r")(?:\s*[–—-]\s*(" + _NUM + r"))?(?![\d.,])\s*(.*?)\s*$",
                       re.I)
# формы одной единицы; сравниваются единицы целиком, а не по первым буквам
UNIT_FORMS = {
    "человеко-день": ("человеко-день", "человеко-дня", "человеко-дней", "человеко-дни", "человекодень", "человекодня",
                      "человекодней", "чел.-дн.", "чел.-дн", "чел.-дня", "чел.-дней", "чел.-день", "чд"),
    "человеко-час": ("человеко-час", "человеко-часа", "человеко-часов", "человеко-часы", "человекочас", "человекочаса",
                     "человекочасов", "чел.-ч.", "чел.-ч", "чел.-час", "чел.-часа", "чел.-часов"),
    "человеко-месяц": ("человеко-месяц", "человеко-месяца", "человеко-месяцев", "человеко-месяцы", "чел.-мес.",
                       "чел.-мес"),
    "рабочий день": ("рабочий день", "рабочих дня", "рабочих дней", "раб. дн.", "раб. дня", "раб. дней"),
    "минута": ("минута", "минуты", "минут", "мин.", "мин"),
    "час": ("час", "часа", "часов", "ч.", "ч"),
    "день": ("день", "дня", "дней", "сутки", "суток", "дн.", "дн"),
    "неделя": ("неделя", "недели", "недель", "нед.", "нед"),
    "месяц": ("месяц", "месяца", "месяцев", "мес.", "мес"),
    "год": ("год", "года", "лет"),
    "процент": ("%", "процент", "процента", "процентов"),
    "рубль": ("рубль", "рубля", "рублей", "руб.", "руб", "₽"),
}
_UNIT_INDEX = sorted(((f, canon) for canon, forms in UNIT_FORMS.items() for f in forms), key=lambda x: -len(x[0]))


def _num(x):
    return float(re.sub(_SP, "", x).replace(",", "."))


_RANGE_RE = re.compile(r"^\s*(?:≈|~|около|примерно)?\s*(" + _NUM + r")(?:\s*[–—-]\s*(" + _NUM + r"))?(?![\d.,])")


def _range(v):
    """Число или диапазон в начале записи: (от, до) или None. Текст после чисел не мешает: «12–18, по согласию»."""
    m = _RANGE_RE.match(str(v or ""))
    if not m:
        return None
    a = _num(m.group(1))
    b = _num(m.group(2)) if m.group(2) else a
    return (a, b) if b >= a else None


def _same_range(a, b):
    return all(abs(x - y) <= max(0.011, 0.005 * max(abs(x), abs(y))) for x, y in zip(a, b))


PART_PREFIXES = ("из них", "в том числе", "в т. ч.", "в т.ч.")


def check_comparison(rows, variants, specs):
    """Сверяет таблицу сравнения с показателями схем и части с целым. Возвращает замечания."""
    notes = []
    labels = sorted({k for sp in specs for k, _ in _metric_pairs(sp)}, key=len, reverse=True)
    for ri, row in enumerate(rows):
        head = str(row[0]).strip()
        lab = next((m for m in labels if head.lower().startswith(m.lower())), None)
        if lab is None:
            continue
        for vi, v in enumerate(variants):
            sc = v.get("scheme")
            if not (_is_int(sc) and 1 <= sc <= len(specs)) or vi + 1 >= len(row):
                continue
            val = dict(_metric_pairs(specs[sc - 1])).get(lab)
            a, b = _range(row[vi + 1]), _range(val)
            if a and b and not _same_range(a, b):
                notes.append(f"сравнение, строка «{head}», вариант «{v.get('name')}»: в таблице «{row[vi + 1]}», на схеме "
                             f"«{lab}: {val}» — плитки показателей и таблица должны показывать одно; поправь одно из двух")
        # строки «из них …» сразу под строкой показателя не могут в сумме превышать целое
        parts = []
        for nxt in rows[ri + 1:]:
            if not str(nxt[0]).strip().lower().startswith(PART_PREFIXES):
                break
            parts.append(nxt)
        if not parts:
            continue
        for vi, v in enumerate(variants):
            whole = _range(row[vi + 1]) if vi + 1 < len(row) else None
            got = [_range(p[vi + 1]) for p in parts if vi + 1 < len(p)]
            if not whole or not got or any(g is None for g in got):
                continue
            lo, hi = sum(g[0] for g in got), sum(g[1] for g in got)
            if lo > whole[1] * 1.02 + 0.011 or (lo + hi) / 2 > (whole[0] + whole[1]) / 2 * 1.1 + 0.011:
                notes.append(f"сравнение, вариант «{v.get('name')}»: строки «из них …» под «{head}» дают в сумме "
                             f"{_fmt_range(lo, hi)}, больше целого «{row[vi + 1]}»; сверь расчёт трудозатрат по ролям")
    return notes


def _fmt_range(lo, hi):
    f = lambda x: (f"{x:.2f}".rstrip("0").rstrip(".")).replace(".", ",")
    return f(lo) if abs(hi - lo) < 1e-9 else f"{f(lo)}–{f(hi)}"


def _parse_value(v):
    """Значение показателя: (середина диапазона, единица, текст после единицы) или None, если запись неоднозначна.
    «1 200 часов» — одно число с разделителем тысяч; «8–10 недель» — диапазон; формы «недели» и «недель» — одна единица.
    Цифры после единицы, незнакомые обороты и пустая запись дают None: тогда коэффициент не считается."""
    m = _VALUE_RE.match(str(v or ""))
    if not m:
        return None
    a = _num(m.group(1))
    b = _num(m.group(2)) if m.group(2) else a
    rest = re.sub(r"\s+", " ", m.group(3).lower()).strip()
    if re.search(r"\d", rest) or b < a:
        return None
    for form, canon in _UNIT_INDEX:
        if rest == form or rest.startswith(form + " ") or (rest.startswith(form) and not form[-1].isalpha()):
            return (a + b) / 2, canon, rest[len(form):].strip()
    return (a + b) / 2, rest, ""


def _times(r):
    """«в 3 раза», «в 2,5 раза», «в 5 раз»."""
    r = round(r, 1)
    if r != int(r):
        return f"в {str(r).replace('.', ',')} раза"
    n = int(r)
    word = "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"
    return f"в {n} {word}"


def _delta(old, new):
    """Насколько изменился показатель: по середине диапазонов и только при одной и той же единице измерения.
    Если запись неоднозначна или единицы разные («человеко-дни» и «человеко-часы»), коэффициент не считается."""
    a, b = _parse_value(old), _parse_value(new)
    if not a or not b or not a[1] or a[1:] != b[1:]:
        return "", ""
    ma, mb = a[0], b[0]
    if ma <= 0 or mb <= 0:
        return "", ""
    r = ma / mb
    if r >= 1.5:
        return f"{_times(r)} меньше", "down"
    if r > 1.05:
        return f"на {round((1 - mb / ma) * 100)} % меньше", "down"
    if r < 1 / 1.5:
        return f"{_times(1 / r)} больше", "up"
    if r < 1 / 1.05:
        return f"на {round((mb / ma - 1) * 100)} % больше", "up"
    return "", ""


def _strings(obj):
    """Все строки описания отчёта, кроме схем: для проверки термина."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k != "schemes" and not str(k).startswith("_"):
                yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def check_report(data, base_dir):
    """Проверяет описание отчёта или листа согласования и подтягивает схемы. Возвращает (отчёт, схемы, ошибки, нарушения,
    замечания, предупреждения): ошибки не дают собрать страницу, нарушения и замечания отклоняет --strict."""
    errors, violations, notes, warnings = [], [], [], []
    if not isinstance(data, dict):
        return None, [], ["описание отчёта — объект JSON с полями title, summary, schemes, variants; получено: "
                          + type(data).__name__], [], [], []
    rep = dict(data)

    def text(obj, key, where, limit=None):
        if key not in obj or obj[key] is None:
            return
        if not _is_text(obj[key]):
            errors.append(f"{where}: поле «{key}» — строка")
        elif limit and len(str(obj[key])) > limit:
            notes.append(f"{where}: поле «{key}» — {len(str(obj[key]))} символов при пределе {limit}; "
                         f"сократи, подробности оставь в документе")

    def str_list(obj, key, where, limit=ITEM_LIMIT):
        v = obj.get(key)
        if v is None:
            return []
        if not isinstance(v, list) or not all(_is_text(x) for x in v):
            errors.append(f"{where}: поле «{key}» — список строк")
            return []
        for i, x in enumerate(v, 1):
            if len(str(x)) > limit:
                notes.append(f"{where}: «{key}», пункт {i} — {len(str(x))} символов при пределе {limit}; сократи")
        return v

    def rows(obj, key, where, width, what):
        v = obj.get(key)
        if v is None:
            return []
        if not isinstance(v, list) or not all(isinstance(x, list) and all(_is_text(c) for c in x) for x in v):
            errors.append(f"{where}: поле «{key}» — список строк таблицы, каждая строка — список ячеек ({what})")
            return []
        for i, row in enumerate(v, 1):
            if width and len(row) != width:
                errors.append(f"{where}: «{key}», строка {i} — {len(row)} ячеек, нужно {width} ({what})")
            for c in row:
                if len(str(c)) > CELL_LIMIT:
                    notes.append(f"{where}: «{key}», строка {i} — ячейка в {len(str(c))} символов при пределе {CELL_LIMIT}; сократи")
        return v

    for key, lim in (("title", 120), ("subtitle", 240), ("owner", 160), ("version", 20), ("date", 60), ("summary", 700),
                     ("recommendation", 450), ("kept", 400), ("comparison_note", 450), ("no_agents", 450),
                     ("human_points", 450)):
        text(rep, key, "отчёт", lim)
    if not str(rep.get("title") or "").strip():
        errors.append("отчёт: нет названия процесса — заполни «title»")
    if not str(rep.get("summary") or "").strip():
        violations.append("отчёт: нет раздела «Коротко» — заполни «summary»: что меняется, в двух-четырёх фразах")

    # лист согласования или итоговый отчёт
    review, stage = rep.get("review"), 5
    if review is not None:
        if not isinstance(review, dict):
            errors.append("лист: поле «review» — объект {\"point\": 1–4, \"question\": \"...\", \"options\": [...]}")
            review = None
        else:
            pt = review.get("point")
            if not _is_int(pt) or not 1 <= pt <= 4:
                errors.append(f"лист: «review.point» — номер согласования от 1 до 4, получено «{pt}»")
            else:
                stage = pt
            text(review, "question", "лист", 400)
            if not str(review.get("question") or "").strip():
                violations.append("лист: нет вопроса владельцу — заполни «review.question»")
            opts = str_list(review, "options", "лист", 200)
            if len(opts) > 6:
                notes.append(f"лист: вариантов ответа {len(opts)} при пределе 6; оставь главные")
            clar = str_list(review, "clarify", "лист", 200)
            if len(clar) > 2:
                violations.append(f"лист: уточнений {len(clar)} — не больше двух; остальное вынеси в допущения и открытые вопросы")
    rep["_stage"] = stage

    raw_status = str(rep.get("status") or "").strip().lower()
    status = STATUS_NAMES.get(raw_status)
    if stage < 5:
        if raw_status and status != "review":
            warnings.append(f"лист: статус «{rep.get('status')}» не нужен — на листе согласования всегда «на согласовании»")
        status = "review"
    elif status is None:
        violations.append(f"отчёт: статус «{rep.get('status', '')}» не указан или неизвестен; допустимы «согласовано» и "
                          f"«условный проект»; страница помечена как условный проект")
        status = "conditional"
    elif status == "review":
        violations.append("отчёт: статус «на согласовании» бывает только у листа согласования (поле «review»); у итогового "
                          "отчёта — «согласовано» или «условный проект»")
        status = "conditional"
    rep["_status"] = status

    # маршрут согласований: всегда четыре
    appr = rep.get("approvals")
    states = []
    if appr is not None and (not isinstance(appr, list) or not all(isinstance(a, dict) for a in appr)):
        errors.append("отчёт: поле «approvals» — список до четырёх объектов {\"state\", \"answer\"}")
        appr = []
    appr = appr or []
    if len(appr) > 4:
        violations.append(f"отчёт: согласований {len(appr)}, а маршрут постоянный — четыре; лишние не показаны")
        appr = appr[:4]
    for i, a in enumerate(appr, 1):
        st = APPROVAL_STATES.get(str(a.get("state") or "впереди").strip().lower())
        if st is None:
            violations.append(f"согласование {i}: состояние «{a.get('state')}» неизвестно; допустимы "
                              + ", ".join(f"«{k}»" for k in APPROVAL_STATES))
            st = "ahead"
        text(a, "answer", f"согласование {i}", 120)
        states.append([st, str(a.get("answer") or "").strip()])
    while len(states) < 4:
        states.append(["ahead", ""])
    if stage < 5:
        for i in range(stage - 1):
            if states[i][0] not in ("ok", "assumed"):
                violations.append(f"лист {stage}: согласование {i + 1} «{APPROVALS[i]}» не пройдено — отметь «согласовано» или "
                                  f"«допущение»")
        states[stage - 1][0] = "current"
    else:
        if not rep.get("approvals"):
            violations.append("отчёт: нет маршрута согласований — заполни «approvals»: четыре согласования с ответами владельца "
                              "или допущениями")
        else:
            for i, (st, _) in enumerate(states):
                if st not in ("ok", "assumed"):
                    violations.append(f"отчёт: согласование {i + 1} «{APPROVALS[i]}» не пройдено; итоговый отчёт собирают после "
                                      f"всех четырёх (без владельца — «допущение»)")
            if status == "agreed" and any(st == "assumed" for st, _ in states):
                violations.append("отчёт: статус «согласовано», но часть согласований закрыта допущением — поставь "
                                  "«условный проект» или получи ответ владельца")
            if status == "conditional" and all(st == "ok" for st, _ in states):
                warnings.append("отчёт: все четыре согласования пройдены — статус может быть «согласовано»")
    rep["_approvals"] = states
    rep["_owner_chose"] = states[2][0] == "ok" or (stage == 5 and status == "agreed")

    for key in ("confirm", "decisions", "assumptions", "questions", "files"):
        str_list(rep, key, "отчёт")
    rows(rep, "passport", "отчёт", 2, "графа, содержание")
    rows(rep, "removed", "отчёт", 3, "работа как есть, зачем была, чем сохранена функция")
    rows(rep, "functions", "отчёт", 3, "функция, чем выполняется, почему")

    ideas = rep.get("ideas")
    if ideas is not None:
        if not isinstance(ideas, list) or not all(isinstance(x, dict) for x in ideas):
            errors.append("отчёт: поле «ideas» — список объектов {\"name\", \"how\", \"removes\", \"effort\", \"keeps\", \"bold\"}")
            rep["ideas"] = ideas = []
        for i, x in enumerate(ideas, 1):
            where = f"идея {i}"
            if not _is_text(x.get("name")) or not str(x.get("name")).strip():
                errors.append(f"{where}: нужно название «name»")
            text(x, "name", where, 120)
            for k, _ in IDEA_FIELDS:
                text(x, k, where, 300)
            if "bold" in x and not isinstance(x["bold"], bool):
                errors.append(f"{where}: поле «bold» — true или false")
    ideas = ideas or []

    # схемы: путь к файлу схем (относительно файла отчёта) или список схем прямо в отчёте
    specs, sch = [], rep.get("schemes")
    if isinstance(sch, str):
        path = sch if os.path.isabs(sch) else os.path.join(base_dir, sch)
        loaded, err = read_spec(path)
        if err:
            errors.append("схемы: " + err)
        sch = loaded
    if "schemes" not in rep or rep.get("schemes") is None:
        violations.append("отчёт без схем: укажи в «schemes» файл схем процесса или список схем")
    elif sch is None:
        pass
    elif isinstance(sch, (list, dict)):
        specs, shape_err = check_shape(sch)
        errors += ["схемы: " + x for x in shape_err]
        if not shape_err:
            for i, sp in enumerate(specs, 1):
                e, v, w = [], [], []
                validate(sp, e, v, w)
                errors += [f"схема {i}: {x}" for x in e]
                violations += [f"схема {i}: {x}" for x in v]
                warnings += [f"схема {i}: {x}" for x in w]
            validate_set(specs, violations, warnings)
    else:
        errors.append("отчёт: поле «schemes» — путь к файлу схем или список схем")

    variants = rep.get("variants")
    if not isinstance(variants, list) or not variants or not all(isinstance(v, dict) for v in variants):
        errors.append("отчёт: поле «variants» — непустой список объектов: «как есть», варианты, при желании «помощники»")
        variants = []
    rep["variants"] = variants = [dict(v) for v in variants]
    chosen, seen_schemes = [], {}
    for i, v in enumerate(variants, 1):
        where = f"вариант {i}" + (f" «{v.get('name')}»" if _is_text(v.get("name")) else "")
        if not _is_text(v.get("name")) or not str(v.get("name")).strip():
            errors.append(f"{where}: нужно название «name»")
        role = ROLE_NAMES.get(str(v.get("role") or "вариант").strip().lower())
        if role is None:
            violations.append(f"{where}: роль «{v.get('role')}» неизвестна; допустимы «как есть», «помощники», «вариант», "
                              f"«смелый вариант»")
            role = "variant"
        v["_role"] = role
        s = v.get("scheme")
        if s is not None:
            if not _is_int(s):
                errors.append(f"{where}: «scheme» — номер схемы (целое число) или null, получено «{s}»")
            elif not specs:
                errors.append(f"{where}: «scheme» = {s}, но схемы в отчёт не загружены")
            elif not 1 <= s <= len(specs):
                errors.append(f"{where}: «scheme» — номер схемы от 1 до {len(specs)} в порядке файла схем, получено {s}")
            elif s in seen_schemes:
                errors.append(f"{where}: схема {s} уже привязана к варианту «{seen_schemes[s]}»; у каждой схемы один вариант")
            else:
                seen_schemes[s] = v.get("name")
        for key, _, lim in VARIANT_TEXTS:
            text(v, key, where, lim)
        tr = v.get("traits")
        if tr is not None:
            if not isinstance(tr, dict):
                errors.append(f"{where}: поле «traits» — объект с ключами " + ", ".join(k for k, _ in TRAITS))
            else:
                for k, val in tr.items():
                    if k not in dict(TRAITS):
                        violations.append(f"{where}: черта «{k}» неизвестна; допустимы " + ", ".join(k for k, _ in TRAITS))
                    elif not (val is None or isinstance(val, bool) or _is_text(val)):
                        errors.append(f"{where}: черта «{k}» — true, false, null или строка с пояснением")
        if "chosen" in v and not isinstance(v["chosen"], bool):
            errors.append(f"{where}: поле «chosen» — true или false")
        if v.get("chosen") is True:
            chosen.append(i)
            if role == "as_is":
                violations.append(f"{where}: «как есть» не бывает выбранным вариантом")
            if v.get("scheme") is None:
                violations.append(f"{where}: у выбранного варианта нет схемы")
    if variants and not any(v["_role"] == "as_is" for v in variants):
        violations.append("отчёт: среди вариантов нет «как есть» — без него не с чем сравнивать")
    if len(chosen) > 1:
        violations.append("отчёт: «chosen» отмечен у нескольких вариантов, выбор один")
    if stage == 5 and status == "agreed" and not chosen:
        violations.append("отчёт: статус «согласовано», но выбор владельца не отмечен; отметь «chosen» или поставь "
                          "статус «условный проект»")
    if specs and variants and stage >= 3:
        used = {v.get("scheme") for v in variants}
        for i in range(1, len(specs) + 1):
            if i not in used:
                warnings.append(f"схема {i} не привязана ни к одному варианту и в отчёт не попадёт")
    rows(rep, "comparison", "отчёт", 1 + len(variants), "строка и по ячейке на каждый вариант в порядке «variants»")

    agents = rep.get("agents")
    if agents is not None:
        if not isinstance(agents, list) or not all(isinstance(a, dict) for a in agents):
            errors.append("отчёт: поле «agents» — список объектов-карточек")
        else:
            for i, a in enumerate(agents, 1):
                where = f"агент {i}"
                if not _is_text(a.get("name")) or not str(a.get("name")).strip():
                    errors.append(f"{where}: нужно имя «name»")
                text(a, "role", where, 240)
                for key, _ in AGENT_FIELDS:
                    text(a, key, where, 300)
                if "preliminary" in a and not isinstance(a["preliminary"], bool):
                    errors.append(f"{where}: поле «preliminary» — true или false")
    checks = rep.get("checks")
    if checks is not None:
        if not isinstance(checks, dict):
            errors.append("отчёт: поле «checks» — объект {\"steps\": [...], \"measures\": \"...\", \"decision\": \"...\", "
                          "\"critical\": [...]}")
        else:
            str_list(checks, "steps", "проверка")
            str_list(checks, "critical", "проверка")
            text(checks, "measures", "проверка", 450)
            text(checks, "decision", "проверка", 450)
    apps = rep.get("appendices")
    if apps is not None:
        if not isinstance(apps, list) or not all(isinstance(a, dict) for a in apps):
            errors.append("отчёт: поле «appendices» — список объектов {\"title\", \"text\", \"table\"}")
        else:
            for i, a in enumerate(apps, 1):
                if not _is_text(a.get("title")):
                    errors.append(f"приложение {i}: нужен заголовок «title»")
                text(a, "text", f"приложение {i}", 1500)
                if a.get("table") is not None:
                    rows(a, "table", f"приложение {i}", 0, "первая строка — заголовки")

    # что обязано быть на этом этапе
    where = f"лист {stage}" if stage < 5 else "отчёт"
    as_is = next((v for v in variants if v.get("_role") == "as_is" and _is_int(v.get("scheme"))
                  and 1 <= v["scheme"] <= len(specs)), None)
    if as_is is None and specs:
        violations.append(f"{where}: у «как есть» нет схемы — укажи её номер в «scheme»")
    if as_is is not None:
        labels = [x.lower() for x in metric_labels(specs[as_is["scheme"] - 1])]
        if not any(x.startswith("трудозатраты") for x in labels):
            violations.append(f"{where}: на схеме «как есть» нет показателя трудозатрат людей — добавь в «metrics» строку "
                              f"«Трудозатраты людей: …» (оценка годится) и ту же подпись на всех схемах")
        if len(chosen) == 1:
            ch = variants[chosen[0] - 1]
            if _is_int(ch.get("scheme")) and 1 <= ch["scheme"] <= len(specs):
                old = dict(_metric_pairs(specs[as_is["scheme"] - 1]))
                for k, val in _metric_pairs(specs[ch["scheme"] - 1]):
                    if not k.lower().startswith("трудозатраты") or k not in old:
                        continue
                    a, b = _parse_value(old[k]), _parse_value(val)
                    if a and b and a[1] and b[1] and a[1] != b[1]:
                        notes.append(f"показатель «{k}»: «{old[k]}» и «{val}» в разных единицах — изменение не посчитано; "
                                     f"приведи оба значения к одной единице, например к человеко-дням")
                    elif a and b and a[1] and a[2] != b[2]:
                        notes.append(f"показатель «{k}»: после единицы разный текст («{old[k]}» и «{val}») — изменение не "
                                     f"посчитано; оставь в значении число и единицу, пояснение перенеси в текст варианта")
                    elif (a is None or b is None) and re.search(r"\d", old[k]) and re.search(r"\d", val):
                        notes.append(f"показатель «{k}»: «{old[k] if a is None else val}» не читается как число с единицей — "
                                     f"изменение не посчитано; запиши значение как «45–55 человеко-дней»")
    comp = rep.get("comparison")
    if specs and isinstance(comp, list) and comp and all(isinstance(r, list) and len(r) == 1 + len(variants) for r in comp):
        notes.extend(check_comparison(comp, variants, specs))
    effort = [r for r in comp if isinstance(r, list) and r and str(r[0]).strip().lower().startswith(
        ("трудозатраты",) + PART_PREFIXES)] if isinstance(comp, list) else []
    if stage in (3, 5) and isinstance(comp, list) and comp and not (
            any(isinstance(r, list) and r and "надзор" in str(r[0]).lower() for r in comp)
            or any("надзор" in str(c).lower() for r in effort for c in r[1:])):
        notes.append("сравнение: не видно трудозатрат на проверку работы агентов и надзор за ними — добавь строку «из них "
                     "проверка и надзор за агентами» (тогда в строках ролей их нет) или пометку «в т. ч. надзор …» в "
                     "ячейках строк ролей; проверка часто съедает выигрыш смелого варианта")
    if stage == 1 and not rep.get("passport"):
        violations.append("лист 1: нет паспорта результата — заполни «passport»")
    if stage == 2:
        if len(ideas) < 2:
            violations.append("лист 2: идей меньше двух — заполни «ideas»: как получить тот же результат без этой работы")
        if not any(x.get("bold") is True for x in ideas):
            violations.append("лист 2: нет смелой идеи — хотя бы одна идея с «bold»: true, где основную работу делают агенты, "
                              "а трудозатраты людей падают в разы")
    if stage >= 3:
        if not any(v.get("_role") == "bold" and v.get("scheme") for v in variants):
            violations.append(f"{where}: нет смелого варианта со схемой — процесс не построен вокруг агентов; добавь вариант с "
                              f"ролью «смелый вариант»")
        if stage in (3, 5) and not rep.get("comparison"):
            violations.append(f"{where}: нет сравнения вариантов — заполни «comparison»")
    if stage >= 4:
        if not chosen:
            violations.append(f"{where}: не отмечен выбранный вариант («chosen»: true)")
        if not rep.get("agents") and not rep.get("no_agents"):
            violations.append(f"{where}: нет агентов выбранного варианта — заполни «agents» (или «no_agents» с причиной)")
        labels = dict(AGENT_FIELDS)
        for i, a in enumerate(rep.get("agents") if isinstance(rep.get("agents"), list) else [], 1):
            if not isinstance(a, dict):
                continue
            miss = [f"«{k}» ({labels[k].lower()})" for k in AGENT_REQUIRED if not str(a.get(k) or "").strip()]
            if miss:
                violations.append(f"{where}: в карточке агента «{a.get('name') or i}» не заполнено: {', '.join(miss)} — "
                                  f"запреты с указанием, чем они обеспечены (проверкой вне модели или только инструкцией), "
                                  f"длина серии до проверки человеком и работа с чужими материалами (references/method.md)")
        ch = rep.get("checks")
        if not isinstance(ch, dict) or not ch.get("steps"):
            violations.append(f"{where}: нет проверки до внедрения — заполни «checks»: шаги, меры и решение о переходе")
        elif not str(ch.get("decision") or "").strip():
            violations.append(f"{where}: в проверке до внедрения нет решения о переходе — заполни «checks.decision»: по каким "
                              f"мерам пилот передаёт агентам реальную работу и когда останавливается")
    for t in _strings(rep):
        for hit in trud_hits(t):
            violations.append(f"термин: «…{hit}…» — пиши «трудозатраты»")
    return rep, specs, errors, violations, notes, warnings


def _tags_for(v, rep):
    role, status = v["_role"], rep["_status"]
    tags = []
    if role == "bold":
        tags.append('<span class="tag t-bold">смелый вариант</span>')
    if v.get("chosen"):
        if rep["_owner_chose"]:
            tags.append('<span class="tag t-dark">выбран владельцем</span>')
        elif status == "review":
            tags.append('<span class="tag">рекомендован</span>')
        else:
            tags.append('<span class="tag">рекомендован · не согласован</span>')
    if not tags:
        tags.append({"as_is": '<span class="tag t-grey">как есть</span>', "helpers": '<span class="tag t-grey">помощники</span>'}
                    .get(role, '<span class="tag t-agent">вариант</span>'))
    return "".join(tags)


def _route(rep):
    """Маршрут согласований: четыре согласования и итоговый отчёт; счёт постоянный."""
    E = lambda v: html_escape(str(v), quote=True)
    stage, out = rep["_stage"], []
    for i, (st, ans) in enumerate(rep["_approvals"], 1):
        mark = {"ok": "✓", "assumed": "≈"}.get(st, str(i))
        sub = {"ok": ans or "согласовано", "assumed": "допущение" + (f": {ans}" if ans else ""),
               "current": "ждёт вашего ответа", "ahead": "впереди"}[st]
        cls = {"ok": "ok", "assumed": "as", "current": "cur", "ahead": "todo"}[st]
        out.append(f'<li class="rst {cls}"><span class="mk">{mark}</span><div><div class="rn">{i} из 4 · шаг {POINT_STEP[i]}</div>'
                   f'<div class="rtl">{E(APPROVALS[i - 1])}</div><div class="ra" title="{E(sub)}">{E(sub)}</div></div></li>')
    fin = stage == 5
    out.append(f'<li class="rst {"ok" if fin else "todo"}"><span class="mk">{"✓" if fin else "★"}</span><div><div class="rn">Итог</div>'
               f'<div class="rtl">Итоговый отчёт</div><div class="ra">{"собран" if fin else "после согласования 4 из 4"}</div>'
               f'</div></li>')
    return f'<div class="route" aria-label="Маршрут согласований"><div class="wrap"><ol class="rt">{"".join(out)}</ol></div></div>'


def _idea_cards(ideas):
    E = lambda v: html_escape(str(v), quote=True)
    cards = []
    for x in ideas:
        tag = ('<span class="tag t-bold">смелая идея</span>' if x.get("bold") else '<span class="tag t-grey">идея</span>')
        dl = "".join(f"<div><dt>{lab}</dt><dd>{_inline(x[k])}</dd></div>" for k, lab in IDEA_FIELDS if x.get(k))
        cards.append(f'<div class="card idea{" bold" if x.get("bold") else ""}">{tag}<h3>{E(x.get("name", ""))}</h3>'
                     f"<dl>{dl}</dl></div>")
    return '<div class="ideas">' + "".join(cards) + "</div>"


def render_report(rep, specs, rendered):
    """Одна самодостаточная HTML-страница. Лист согласования показывает вопрос владельцу и разделы своего шага,
    итоговый отчёт — всё: выводы коротко, результат, что убираем, схемы, сравнение, агенты, проверку, решения."""
    E = lambda v: html_escape(str(v), quote=True)
    status, stage = rep["_status"], rep["_stage"]
    review = stage < 5
    variants = rep.get("variants") or []
    chosen = next((v for v in variants if v.get("chosen") and v.get("scheme")), None)
    as_is = next((v for v in variants if v["_role"] == "as_is" and v.get("scheme")), None)
    shown = [v for v in variants if v.get("scheme")]
    max_w = max([rendered[v["scheme"] - 1][1] for v in shown] or [1])
    title = str(rep.get("title", ""))
    sections, nav = [], []
    want = (lambda sid: True) if not review else (lambda sid: sid in STAGE_SECTIONS[stage])

    def section(sid, name, body, hint="", short=None):
        n = len(sections) + 1
        h = f'<span class="hint">{E(hint)}</span>' if hint else ""
        sections.append(f'<section class="sec" id="{sid}"><div class="sh"><span class="num">{n:02d}</span><h2>{E(name)}</h2>{h}'
                        f'</div>{body}</section>')
        nav.append(f'<a href="#{sid}">{E(short or name)}</a>')

    # 00 Что нужно от вас — только на листе согласования
    if review:
        rv = rep.get("review") or {}
        body = [f'<div class="card ask"><div class="lab">Согласование {stage} из 4 · {E(APPROVALS[stage - 1])}</div>',
                f'<p class="q">{_inline(rv.get("question", ""))}</p>']
        if rv.get("options"):
            body.append('<ol class="opts">' + "".join(f"<li><span>{_inline(x)}</span></li>" for x in rv["options"]) + "</ol>")
        if rv.get("clarify"):
            body.append('<div class="clar"><div class="lab">Если знаете, уточните</div><ul>'
                        + "".join(f"<li>{_inline(x)}</li>" for x in rv["clarify"]) + "</ul></div>")
        body.append('<p class="how">Ответьте в переписке: номер, своими словами или «не знаю». Молчание согласием не считается.</p>'
                    "</div>")
        section("otvet", "Что нужно от вас", "".join(body), "ответ — в переписке", short="Ваш ответ")

    # 01 Коротко
    if want("kratko"):
        owner_pick = rep["_owner_chose"] and chosen
        body = ['<div class="grid2">' if rep.get("recommendation") else "<div>",
                f'<div class="card lead">{_rich(rep.get("summary", ""))}</div>']
        if rep.get("recommendation"):
            head = "Выбор владельца" if owner_pick else "Рекомендация"
            body.append(f'<div class="card reco"><div class="lab">{head}</div>{_rich(rep["recommendation"])}</div>')
        body.append("</div>")
        tiles, cap, est = [], "", False
        if chosen and as_is:
            old = dict(_metric_pairs(specs[as_is["scheme"] - 1]))
            new_spec = specs[chosen["scheme"] - 1]
            for k, val in _metric_pairs(new_spec):
                d, cls = _delta(old.get(k, ""), val) if k in old else ("", "")
                was = (f'<div class="was"><span>{E(as_is.get("name", "Как есть"))}:</span> {E(old[k])}</div>' if k in old else "")
                dl = f'<div class="delta {cls}">{E(d)}</div>' if d else ""
                tiles.append(f'<div class="kpi"><div class="k">{E(k)}</div><div class="now">{E(val)}</div>{dl}{was}</div>')
            est = _estimate(new_spec) or _estimate(specs[as_is["scheme"] - 1])
            cap = f'Показатели «{E(chosen.get("name", ""))}» в сравнении с «{E(as_is.get("name", "как есть"))}»'
        elif as_is:
            sp = specs[as_is["scheme"] - 1]
            for k, val in _metric_pairs(sp):
                tiles.append(f'<div class="kpi"><div class="k">{E(k)}</div><div class="now asis">{E(val)}</div></div>')
            est = _estimate(sp)
            cap = f'Показатели «{E(as_is.get("name", "как есть"))}»'
        if tiles:
            body.append(f'<div class="kcap">{cap}' + (' <span class="tag">оценка, не замер</span>' if est else "") + "</div>"
                        + '<div class="kpis">' + "".join(tiles) + "</div>")
        if rep.get("confirm"):
            lab = ("Решить позже" if review else "Владельцу подтвердить" if status == "agreed" else "Нужно решение владельца")
            body.append(f'<div class="card callout"><div class="lab">{lab}</div><ol>'
                        + "".join(f"<li>{_inline(x)}</li>" for x in rep["confirm"]) + "</ol></div>")
        section("kratko", "Коротко", "".join(body))

    # 02 Результат процесса
    if want("rezultat") and rep.get("passport"):
        items = "".join(f"<div><dt>{E(a)}</dt><dd>{_inline(b)}</dd></div>" for a, b in rep["passport"])
        section("rezultat", "Результат процесса", f'<div class="card"><dl class="pass">{items}</dl></div>',
                "одинаков во всех вариантах", short="Результат")

    # 03 Смелые идеи — лист 2
    if review and want("idei") and rep.get("ideas"):
        section("idei", "Смелые идеи", _idea_cards(rep["ideas"]), "тот же результат без этой работы", short="Идеи")

    # 04 Что убираем
    if want("ubiraem") and rep.get("removed"):
        trs = "".join(f"<tr><th scope=\"row\">{_inline(a)}</th><td data-l=\"Зачем была\">{_inline(b)}</td>"
                      f"<td data-l=\"Чем сохранена функция\">{_inline(c)}</td></tr>" for a, b, c in rep["removed"])
        body = ('<div class="tw"><table class="tbl st"><thead><tr><th>Работа «как есть»</th><th>Зачем была</th>'
                f'<th>Чем сохранена функция</th></tr></thead><tbody>{trs}</tbody></table></div>')
        if rep.get("kept"):
            body += f'<div class="note">{_inline(rep["kept"])}</div>'
        section("ubiraem", "Что убираем", body)

    # 05 Схемы; лист 4 показывает только схему выбранного варианта
    if review and stage == 4 and chosen:
        shown = [chosen]
        max_w = rendered[chosen["scheme"] - 1][1]
    if want("shemy") and shown:
        start = chosen or next((v for v in shown if v["_role"] == "bold"), None) or shown[0]
        tabs = []
        for v in shown:
            sid = str(v["scheme"])
            dot = '<span class="dot"></span>' if v is chosen else ""
            tabs.append(f'<button class="tab" type="button" role="tab" data-tab="{sid}" '
                        f'aria-selected="{"true" if v is start else "false"}">{dot}{E(v.get("name", ""))}</button>')
        if len(shown) > 1:
            tabs.append('<button class="tab" type="button" role="tab" data-tab="all" aria-selected="false">Все схемы подряд</button>')
        parts = [f'<div class="tabs" role="tablist">{"".join(tabs)}</div>'] if len(shown) > 1 else []
        for v in variants if not (review and stage == 4) else []:
            if v["_role"] == "helpers" and not v.get("scheme"):
                parts.append(f'<div class="helper"><span class="tag t-grey">помощники</span><div><strong>{E(v.get("name", ""))}</strong>'
                             f' — {_inline(v.get("essence", "")) or "те же шаги, ускоренные агентами"}. Схемы нет: '
                             f'устройство процесса прежнее, вариант идёт строкой сравнения.</div></div>')
        for v in shown:
            i = v["scheme"]
            svg, W, _ = rendered[i - 1]
            style = f'width:{W / max_w * 100:.2f}%;min-width:{max(1100 * W / max_w, MIN_SCALE * W):.0f}px'
            svg = svg.replace("<svg ", f'<svg style="{style}" ', 1)
            on = " on" if v is start or len(shown) == 1 else ""
            p = [f'<div class="panel{on}" data-panel="{i}" role="tabpanel">',
                 f'<div class="ph">{_tags_for(v, rep)}<h3>{E(v.get("name", ""))}</h3></div>']
            if v.get("essence"):
                p.append(f'<p class="ess">{_inline(v["essence"])}</p>')
            tr = v.get("traits") or {}
            if isinstance(tr, dict) and tr:
                chips, pn = [], []
                for k, label in TRAITS:
                    if k not in tr:
                        continue
                    val = tr[k]
                    if val is True:
                        chips.append(f'<span class="chip yes">{ICON_CHECK}{label}</span>')
                    elif val is False or val is None:
                        chips.append(f'<span class="chip no">{ICON_CROSS}{label}</span>')
                    else:
                        chips.append(f'<span class="chip part" title="{E(val)}">{ICON_PART}{label}</span>')
                        pn.append(f"<li><strong>{label}</strong> — {_inline(val)}</li>")
                p.append('<div class="chips">' + "".join(chips) + "</div>")
                if pn:
                    p.append('<ul class="pnotes">' + "".join(pn) + "</ul>")
            same = " · дорожки и блок результата те же, что на других схемах" if len(shown) > 1 else ""
            p.append(f'<div class="figbar"><span>Схема {i} из {len(specs)}{same}</span>'
                     f'<button class="zoom" type="button" data-fig="fig{i}">{ICON_ZOOM}Открыть крупно</button></div>')
            p.append(f'<div class="out"><figure class="fig" id="fig{i}">{svg}</figure></div>')
            if v.get("text"):
                p.append(f'<div class="story">{_rich(v["text"])}</div>')
            minis = [(lab, v.get(k)) for k, lab in (("removed", "Что исчезло"), ("human", "Где человек"),
                                                       ("risks", "Условия и риски")) if v.get(k)]
            if minis:
                p.append('<div class="minis">' + "".join(f'<div class="mini"><div class="lab">{lab}</div>{_rich(t)}</div>'
                                                        for lab, t in minis) + "</div>")
            p.append("</div>")
            parts.append("".join(p))
        hint = "переключайте вкладки; схему можно открыть крупно" if len(shown) > 1 else "схему можно открыть крупно"
        section("shemy", "Схемы процесса" if len(shown) > 1 else "Схема процесса", "".join(parts), hint,
                short="Схемы" if len(shown) > 1 else "Схема")

    # 06 Сравнение
    if want("sravnenie") and rep.get("comparison"):
        heads = []
        for v in variants:
            pick = v.get("chosen") is True
            subs = (["смелый"] if v["_role"] == "bold" else []) + (
                ["выбран" if rep["_owner_chose"] else "рекомендован"] if pick else [])
            sub = f"<small>{' · '.join(subs)}</small>" if subs else ""
            cls = ' class="pick"' if pick else ""
            heads.append(f'<th{cls}>{E(v.get("name", ""))}{sub}</th>')
        trs = []
        for row in rep["comparison"]:
            cells = "".join(("<td class=\"pick\"" if variants[j].get("chosen") is True else "<td")
                            + f' data-l="{E(variants[j].get("name", ""))}">' + _inline(c) + "</td>"
                            for j, c in enumerate(row[1:len(variants) + 1]))
            trs.append(f'<tr><th scope="row">{_inline(row[0])}</th>{cells}</tr>')
        body = (f'<div class="tw"><table class="tbl st"><thead><tr><th>Строка</th>{"".join(heads)}</tr></thead>'
                f'<tbody>{"".join(trs)}</tbody></table></div>')
        if rep.get("comparison_note"):
            body += f'<div class="note">{_inline(rep["comparison_note"])}</div>'
        section("sravnenie", "Сравнение вариантов", body, "числа без замеров — гипотезы", short="Сравнение")

    # 07 Агенты
    agents = rep.get("agents") or []
    if want("agenty") and (rep.get("functions") or agents or rep.get("no_agents")):
        body = []
        if rep.get("functions"):
            trs = "".join(f'<tr><th scope="row">{_inline(a)}</th><td data-l="Чем выполняется"><span class="by {_by_class(b)}">'
                          f'{_inline(b)}</span></td><td data-l="Почему">{_inline(c)}</td></tr>' for a, b, c in rep["functions"])
            body.append('<div class="tw"><table class="tbl st"><thead><tr><th>Функция нового процесса</th><th>Чем выполняется</th>'
                        f"<th>Почему</th></tr></thead><tbody>{trs}</tbody></table></div>")
        if rep.get("no_agents"):
            body.append(f'<div class="card noag"><span class="tag t-grey">без агентов</span><div>{_rich(rep["no_agents"])}</div></div>')
        if agents:
            confirmed = rep["_approvals"][3][0] == "ok" and status != "conditional"
            cards = []
            for a in agents:
                pre = a.get("preliminary") or not confirmed
                dl = "".join(f"<div><dt>{lab}</dt><dd>{_inline(a[k])}</dd></div>" for k, lab in AGENT_FIELDS if a.get(k))
                cards.append(f'<div class="card acard"><h3>{E(a.get("name", ""))}'
                             + ('<span class="tag">предварительно</span>' if pre else "") + "</h3>"
                             + (f'<p class="ar">{_inline(a["role"])}</p>' if a.get("role") else "") + f"<dl>{dl}</dl></div>")
            body.append('<div class="agents">' + "".join(cards) + "</div>")
        if rep.get("human_points"):
            body.append(f'<div class="note"><strong>Точки человека.</strong> {_inline(rep["human_points"])}</div>')
        name = f"Агенты варианта «{chosen.get('name')}»" if chosen else "Агенты"
        section("agenty", name, "".join(body), short="Агенты")

    # 08 Проверка до внедрения
    ch = rep.get("checks") or {}
    if want("proverka") and (ch.get("steps") or ch.get("critical") or ch.get("measures") or ch.get("decision")):
        body = []
        if ch.get("steps"):
            body.append('<ol class="steps">' + "".join(f"<li><div>{_inline(s)}</div></li>" for s in ch["steps"]) + "</ol>")
        if ch.get("measures"):
            body.append(f'<div class="note"><strong>Меры.</strong> {_inline(ch["measures"])}</div>')
        if ch.get("decision"):
            body.append(f'<div class="note"><strong>Решение о переходе.</strong> {_inline(ch["decision"])}</div>')
        if ch.get("critical"):
            body.append('<div class="card crit"><div class="lab">Провал любой из проверок останавливает внедрение</div><ul>'
                        + "".join(f"<li>{_inline(x)}</li>" for x in ch["critical"]) + "</ul></div>")
        section("proverka", "Проверка до внедрения", "".join(body), short="Проверка")

    # 09 Решения, допущения, вопросы
    cols = [(lab, rep.get(k)) for k, lab in (("decisions", "Решения владельца"), ("assumptions", "Допущения"),
                                              ("questions", "Открытые вопросы")) if rep.get(k)]
    if want("resheniya") and cols:
        body = '<div class="cols3">' + "".join(f'<div class="card"><div class="lab">{lab}</div><ul>'
                                                + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul></div>"
                                                for lab, items in cols) + "</div>"
        section("resheniya", "Решения и открытые вопросы", body, short="Решения и вопросы")

    # 10 Приложения; в итоговом отчёте сюда же уходят идеи шага 2
    apps = list(rep.get("appendices") or [])
    if want("prilozheniya") and (apps or (not review and rep.get("ideas"))):
        body = []
        if not review and rep.get("ideas"):
            body.append(f'<details class="app"><summary>Смелые идеи шага 2</summary><div class="ab">{_idea_cards(rep["ideas"])}'
                        "</div></details>")
        for a in apps:
            inner = _rich(a.get("text", ""))
            t = a.get("table")
            if t:
                head = "".join(f"<th>{_inline(c)}</th>" for c in t[0])
                labels = [E(c) for c in t[0]]
                trs = "".join("<tr>" + "".join(f'<td data-l="{labels[j] if j < len(labels) else ""}">{_inline(c)}</td>'
                                               for j, c in enumerate(r)) + "</tr>" for r in t[1:])
                inner += f'<div class="tw"><table class="tbl st"><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table></div>'
            body.append(f'<details class="app"><summary>{E(a.get("title", ""))}</summary><div class="ab">{inner}</div></details>')
        section("prilozheniya", "Приложения", "".join(body))

    # шапка: название, статус, результат процесса (одинаков во всех вариантах)
    if review:
        st_html = '<span class="status rev">Ждёт вашего ответа</span>'
        eyebrow = f"Лист согласования {stage} из 4 · шаг {POINT_STEP[stage]} из 3"
    else:
        st_html = ('<span class="status ok">Согласовано с владельцем</span>' if status == "agreed"
                   else '<span class="status cond">Условный проект · не согласован с владельцем</span>')
        eyebrow = "Пересборка процесса вокруг агентов"
    meta = [st_html] + [E(x) for x in (f"Владелец процесса: {rep['owner']}" if rep.get("owner") else "",
                                       f"Версия {rep['version']}" if rep.get("version") else "", rep.get("date") or "") if x]
    res_html = ""
    rs = next((sp.get("result") for sp in specs if sp.get("result")), None)
    if rs:
        lis = "".join(f'<li>{ICON_CHECK.replace("currentColor", "#5FCFC1")}<span>{E(x)}</span></li>' for x in rs.get("items") or [])
        res_html = (f'<aside class="res"><h2>{E(rs.get("title", "Результат"))}</h2>'
                    f'<div class="rh">{E(rs.get("heading") or "Одинаков во всех вариантах")}</div><ul>{lis}</ul>'
                    + (f'<div class="rf">{E(rs["footer"])}</div>' if rs.get("footer") else "") + "</aside>")
    hero = (f'<header class="hero"><div class="wrap"><div><div class="eyebrow">{E(eyebrow)}</div>'
            f'<h1>{E(title)}</h1>' + (f'<p class="sub">{_inline(rep["subtitle"])}</p>' if rep.get("subtitle") else "")
            + f'<div class="meta">{"".join(f"<span>{m}</span>" if not m.startswith("<span") else m for m in meta)}</div></div>'
            f"{res_html}</div></header>")
    files = rep.get("files") or []
    date = f" · {E(rep['date'])}" if rep.get("date") else ""
    if review:
        foot = (f'<footer><div class="wrap">Лист согласования {stage} из 4 собран навыком «Пересборка процесса вокруг агентов» '
                f'{SKILL_VERSION}{date}. Итоговый отчёт — после согласования 4 из 4. Числа без замеров — гипотезы.</div></footer>')
        page_title = f"Согласование {stage} из 4: {title}"
    else:
        foot = (f'<footer><div class="wrap">Отчёт собран навыком «Пересборка процесса вокруг агентов» {SKILL_VERSION}{date}.'
                " Числа без замеров — гипотезы."
                + (" Файлы: " + ", ".join(f"<code>{E(f)}</code>" for f in files) + "." if files else "") + "</div></footer>")
        page_title = f"Пересборка процесса: {title}"
    body_tag = '<body class="rv">' if review else "<body>"
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="generator" content="draw_process.py {SKILL_VERSION}">'
            f"<title>{E(page_title)}</title>"
            "<script>document.documentElement.className+=' js'</script>"
            f"<style>{REPORT_CSS}</style></head>{body_tag}{hero}{_route(rep)}"
            f'<nav class="toc" aria-label="Разделы"><div class="wrap">{"".join(nav)}</div></nav>'
            f'<main><div class="wrap">{"".join(sections)}</div></main>{foot}'
            '<div class="ov" id="ov" role="dialog" aria-label="Схема крупно"><button class="ovx" type="button">Закрыть ✕</button>'
            '<div class="box" id="ovbox"></div></div>'
            f"<script>{REPORT_JS}</script></body></html>")


def build_report(path, out, strict, check=False):
    """Режим --report: проверяет описание отчёта, рисует схемы и пишет одну HTML-страницу."""
    data, err = read_spec(path)
    if err:
        print("ошибка:", err, file=sys.stderr)
        return 1
    rep, specs, errors, violations, notes, warnings = check_report(data, os.path.dirname(os.path.abspath(path)))
    for w in warnings:
        print("предупреждение:", w, file=sys.stderr)
    for v in violations:
        print("нарушение правила:", v, file=sys.stderr)
    if errors:
        for e in errors:
            print("ошибка:", e, file=sys.stderr)
        print(f"отчёт не собран: ошибок {len(errors)}", file=sys.stderr)
        return 1
    rendered = []
    for i, sp in enumerate(specs, 1):
        svg, W, H, n = render_svg(sp, i, 0)
        rendered.append((svg, W, H))
        notes += [f"схема {i}: {x}" for x in n]
    for n in notes:
        print("замечание раскладки:", n, file=sys.stderr)
    summary = f"нарушений правил: {len(violations)}; замечаний раскладки: {len(notes)}"
    failed = strict and (violations or notes)
    if check:
        what = f"лист согласования {rep['_stage']} из 4" if rep["_stage"] < 5 else "итоговый отчёт"
        print(f"{what} проверен, схем: {len(specs)}; {summary}")
        return 1 if failed else 0
    page = render_report(rep, specs, rendered)
    what = f"лист согласования {rep['_stage']} из 4" if rep["_stage"] < 5 else "итоговый отчёт"
    base = os.path.abspath(out or os.path.splitext(path)[0])
    if base.lower().endswith(".html"):
        base = base[:-5]
    os.makedirs(os.path.dirname(base), exist_ok=True)
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(page)
    print(f"готово ({what}): {base}.html | {summary}")
    return 1 if failed else 0


def read_spec(path):
    """Читает JSON с понятными сообщениями вместо трассировки."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, f"файл «{path}» не найден; проверь путь (лучше абсолютный)"
    except UnicodeDecodeError:
        return None, f"файл «{path}» не в кодировке UTF-8; сохрани его в UTF-8"
    except json.JSONDecodeError as e:
        return None, f"файл «{path}» — неверный JSON: строка {e.lineno}, столбец {e.colno}: {e.msg}"


def main():
    ap = argparse.ArgumentParser(description="Схемы процессов по дорожкам: JSON → SVG, HTML и Mermaid; листы согласования и отчёт — HTML-страницы")
    ap.add_argument("spec", nargs="?", help="файл спецификации JSON (объект или список объектов)")
    ap.add_argument("-o", "--out", help="путь и имя выходных файлов без расширения; лучше абсолютный")
    ap.add_argument("--png", action="store_true", help="дополнительно PNG через cairosvg, если он установлен")
    ap.add_argument("--mermaid", action="store_true", help="дополнительно текст Mermaid (.mmd) на каждую схему")
    ap.add_argument("--check", action="store_true", help="проверить спецификацию и раскладку, файлов не писать")
    ap.add_argument("--strict", action="store_true", help="код 1, если есть нарушения правил или замечания раскладки")
    ap.add_argument("--example", action="store_true", help="вывести образец спецификации")
    ap.add_argument("--report", action="store_true", help="файл — описание отчёта: собрать HTML-страницу; с полем «review» — лист согласования")
    ap.add_argument("--example-report", action="store_true", help="вывести образец описания отчёта")
    a = ap.parse_args()
    if a.example:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    if a.example_report:
        print(json.dumps(EXAMPLE_REPORT, ensure_ascii=False, indent=2))
        return 0
    if not a.spec:
        ap.print_help()
        return 2
    if a.report:
        return build_report(a.spec, a.out, a.strict, a.check)
    data, err = read_spec(a.spec)
    if err:
        print("ошибка:", err, file=sys.stderr)
        return 1
    specs, errors = check_shape(data)
    violations, warnings = [], []
    if not errors:
        for i, sp in enumerate(specs, 1):
            e, v, w = [], [], []
            validate(sp, e, v, w)
            errors += [f"схема {i}: {x}" for x in e]
            violations += [f"схема {i}: {x}" for x in v]
            warnings += [f"схема {i}: {x}" for x in w]
        validate_set(specs, violations, warnings)
    for w in warnings:
        print("предупреждение:", w, file=sys.stderr)
    for v in violations:
        print("нарушение правила:", v, file=sys.stderr)
    if errors:
        for e in errors:
            print("ошибка:", e, file=sys.stderr)
        print(f"схемы не нарисованы: ошибок {len(errors)}", file=sys.stderr)
        return 1
    # колонки не выравниваются принудительно: вариант со слитыми этапами честно уже;
    # одинаковое число колонок задаёт поле «columns», одинаковый масштаб на странице даёт HTML
    rendered, notes = [], []
    for i, sp in enumerate(specs, 1):
        svg, W, H, n = render_svg(sp, i, 0)
        rendered.append((svg, W, H))
        notes += [f"схема {i}: {x}" for x in n]
    for n in notes:
        print("замечание раскладки:", n, file=sys.stderr)
    summary = f"нарушений правил: {len(violations)}; замечаний раскладки: {len(notes)}"
    failed = a.strict and (violations or notes)
    if a.check:
        print(f"проверено схем: {len(specs)}; {summary}")
        return 1 if failed else 0
    base = os.path.abspath(a.out or os.path.splitext(a.spec)[0])
    os.makedirs(os.path.dirname(base), exist_ok=True)
    written = []
    for i, (svg, W, H) in enumerate(rendered, 1):
        path = f"{base}.svg" if len(specs) == 1 else f"{base}_{i}.svg"
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        written.append(path)
        if a.png:
            try:
                import cairosvg
                cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=path[:-4] + ".png", output_width=int(W * 2))
                written.append(path[:-4] + ".png")
            except ImportError:
                print("PNG пропущен: библиотека cairosvg не установлена; SVG, HTML и Mermaid достаточно", file=sys.stderr)
        if a.mermaid:
            mpath = path[:-4] + ".mmd"
            with open(mpath, "w", encoding="utf-8") as f:
                f.write(render_mermaid(specs[i - 1]))
            written.append(mpath)
    title = specs[0].get("page_title") or (specs[0].get("title") if len(specs) == 1 else "Схемы процесса")
    html_path = f"{base}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(specs, rendered, str(title)))
    written.append(html_path)
    print("готово:", ", ".join(written), f"| {summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
