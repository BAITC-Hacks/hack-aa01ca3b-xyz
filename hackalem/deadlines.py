"""Deterministic deadline resolver — the agent's date tool.

The LLM copies the deadline phrase as spoken («за две недели», «к среде»,
«к пятнадцатому октября», «жұмаға дейін»); this module turns it into a calendar
date counted from the meeting date. Small local models are unreliable at date
arithmetic, so dates are never left to the model when a rule applies.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    "қаңтар": 1, "ақпан": 2, "наурыз": 3, "сәуір": 4, "мамыр": 5, "маусым": 6, "шілде": 7, "тамыз": 8,
    "қыркүйек": 9, "қазан": 10, "қараша": 11, "желтоқсан": 12,
}
WEEKDAYS = {
    r"понедельник\w*": 0, r"вторник\w*": 1, r"сред[аеуы]": 2, r"четверг\w*": 3, r"пятниц\w*": 4, r"суббот\w*": 5,
    r"воскресень\w*": 6, r"дүйсенбі\w*": 0, r"сейсенбі\w*": 1, r"сәрсенбі\w*": 2, r"бейсенбі\w*": 3, r"жұма\w*": 4,
    r"сенбі\w*": 5, r"жексенбі\w*": 6,
}
UNITS = {"один": 1, "одн": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3, "трех": 3, "четыр": 4, "пят": 5,
         "шест": 6, "сем": 7, "восем": 8, "девят": 9, "десят": 10,
         "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5, "алты": 6, "жеті": 7, "сегіз": 8, "тоғыз": 9, "он": 10}
ORDINALS = [
    ("одиннадцат", 11), ("двенадцат", 12), ("тринадцат", 13), ("четырнадцат", 14), ("пятнадцат", 15),
    ("шестнадцат", 16), ("семнадцат", 17), ("восемнадцат", 18), ("девятнадцат", 19), ("двадцат", 20), ("тридцат", 30),
    ("перв", 1), ("втор", 2), ("трет", 3), ("четвёрт", 4), ("четверт", 4), ("пят", 5), ("шест", 6), ("седьм", 7),
    ("восьм", 8), ("девят", 9), ("десят", 10),
]
MONTH_WORD = r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яйе]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|қаңтар\w*|ақпан\w*|наурыз\w*|сәуір\w*|мамыр\w*|маусым\w*|шілде\w*|тамыз\w*|қыркүйек\w*|қазан\w*|қараша\w*|желтоқсан\w*)"


def _month(word: str) -> int | None:
    word = word.lower()
    for stem, number in sorted(MONTHS.items(), key=lambda item: -len(item[0])):
        if word.startswith(stem) and not (stem == "ма" and not re.match(r"ма[яйе]", word)):
            return number
    return None


def _ordinal(words: list[str]) -> int | None:
    """«двадцать шестого» → 26, «пятнадцатому» → 15."""
    total = 0
    for word in words:
        if word in {"двадцать", "тридцать"}:
            total += 20 if word == "двадцать" else 30
            continue
        for stem, number in ORDINALS:
            if word.startswith(stem):
                return total + number
    return total or None


def _count(text: str) -> int:
    match = re.search(r"(\d+)", text)
    if match:
        return int(match.group(1))
    for word in re.findall(r"\w+", text):
        for stem, number in UNITS.items():
            if word == stem or (len(stem) > 2 and word.startswith(stem)):
                return number
    return 1


def _explicit_date(text: str, start: date) -> date | None:
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", text)
    if match:
        year = int(match.group(3)) if match.group(3) else start.year
        year = year + 2000 if year < 100 else year
        return _safe_date(year, int(match.group(2)), int(match.group(1)), start, explicit_year=bool(match.group(3)))
    match = re.search(r"\b(\d{1,2})\s*(?:-?го|-?е)?\s+" + MONTH_WORD, text)
    if match:
        return _safe_date(start.year, _month(match.group(2)) or 0, int(match.group(1)), start)
    match = re.search(r"((?:двадцать|тридцать)?\s*[а-яё]+(?:ого|ому|ое|ым))\s+" + MONTH_WORD, text)
    if match:
        day = _ordinal(match.group(1).split())
        if day:
            return _safe_date(start.year, _month(match.group(2)) or 0, day, start)
    return None


def _safe_date(year: int, month: int, day: int, start: date, explicit_year: bool = False) -> date | None:
    try:
        result = date(year, month, day)
    except ValueError:
        return None
    if not explicit_year and result < start:
        result = date(year + 1, month, day)
    return result


def resolve(phrase: str, meeting_date: str | date) -> str:
    """Return YYYY-MM-DD for a spoken deadline phrase, or "" when no rule applies."""
    start = date.fromisoformat(meeting_date) if isinstance(meeting_date, str) else meeting_date
    text = (phrase or "").lower().replace("ё", "е")
    if not text.strip() or text.startswith("не указ"):
        return ""
    explicit = _explicit_date(text, start)
    if explicit:
        return explicit.isoformat()
    friday = start + timedelta(days=(4 - start.weekday()) % 7)
    if re.search(r"конц\w* недел|эт\w* недел|текущ\w* недел|осы апта", text):
        return friday.isoformat()
    if re.search(r"следующ\w* недел|келесі апта", text):
        return (friday + timedelta(days=7)).isoformat()
    if re.search(r"конц\w* месяц|ай соңына", text):
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]).isoformat()
    if re.search(r"конц\w* квартал", text):
        last_month = ((start.month - 1) // 3 + 1) * 3
        return date(start.year, last_month, calendar.monthrange(start.year, last_month)[1]).isoformat()
    if re.search(r"конц\w* год", text):
        return date(start.year, 12, 31).isoformat()
    if "послезавтра" in text:
        return (start + timedelta(days=2)).isoformat()
    if "завтра" in text or "ертең" in text:
        return (start + timedelta(days=1)).isoformat()
    if "сегодня" in text or "бүгін" in text:
        return start.isoformat()
    for pattern, weekday in WEEKDAYS.items():
        if re.search(rf"\b{pattern}\b", text):
            return (start + timedelta(days=(weekday - start.weekday()) % 7 or 7)).isoformat()
    match = re.search(r"(?:за|через|в течение|течение)?\s*([\w\s]*?)\s*(дн|день|дня|недел|месяц|апта|ай\b)", text)
    counted = re.search(r"\b(\d+|один|одну|две|два|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+(дн|день|дня|недел|месяц)", text)
    if match and (counted or re.search(r"\b(за|через|в течение|течение)\b|апта|ай\b", text)):
        count = _count(match.group(1))
        unit = match.group(2)
        if unit.startswith("дн") or unit == "день" or unit == "дня":
            return (start + timedelta(days=count)).isoformat()
        if unit.startswith("недел") or unit.startswith("апта"):
            return (start + timedelta(days=7 * count)).isoformat()
        month = start.month - 1 + count
        year, month = start.year + month // 12, month % 12 + 1
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1])).isoformat()
    return ""


DEADLINE_HINT = re.compile(r"\b(?:до|к|ко|за|через|в течение|на)\s+[^,.;!?]{1,40}", re.IGNORECASE)


def from_quote(quote: str, meeting_date: str) -> tuple[str, str]:
    """Find the single deadline phrase in a quote: (phrase, YYYY-MM-DD) or ("", "") when absent or ambiguous."""
    found = {}
    for match in DEADLINE_HINT.finditer(quote or ""):
        resolved = resolve(match.group(0), meeting_date)
        if resolved:
            found.setdefault(resolved, match.group(0).strip())
    if len(found) == 1:
        iso, phrase = next(iter(found.items()))
        return phrase, iso
    return "", ""
