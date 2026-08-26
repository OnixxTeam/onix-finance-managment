"""
Списки записей: последние N (`/list`) и траты одной категории за период.

Модуль чистый: не ходит в сеть, работает со списком Entry из sheets.py. Форматтер
строки один на оба списка — иначе они разъезжаются при первой же правке.
"""

from datetime import date

from report import format_amount, period_bounds
from sheets import TYPE_INCOME, Entry

DEFAULT_LIST_SIZE = 10

# Длиннее — нечитаемо в чате, а Telegram режет сообщение по 4096 символов.
MAX_LIST_SIZE = 20


def parse_limit(argument: str | None) -> int | None:
    """Число записей из аргумента команды. None — аргумент не число или не положителен."""
    if argument is None or not argument.strip():
        return DEFAULT_LIST_SIZE
    try:
        limit = int(argument.strip())
    except ValueError:
        return None
    if limit < 1:
        return None
    return min(limit, MAX_LIST_SIZE)


def recent(entries: list[Entry], limit: int) -> list[Entry]:
    """Последние записи, новые сверху.

    Порядок берём из листа, а не из дат: дата записи — всегда день добавления, и внутри
    одного дня сортировка по ней ничего не упорядочивает.
    """
    return list(reversed(entries[-limit:]))


def in_category(
    entries: list[Entry], category: str, period: str, today: date | None = None
) -> list[Entry]:
    """Записи одной категории за период, новые сверху."""
    today = today or date.today()
    start, end = period_bounds(period, today)
    matched = [
        entry
        for entry in entries
        if entry.category == category
        and (start is None or entry.day >= start)
        and (end is None or entry.day <= end)
    ]
    return list(reversed(matched))


def format_entry_line(number: int, entry: Entry, show_category: bool = True) -> str:
    amount = format_amount(entry.amount)
    if entry.entry_type == TYPE_INCOME:
        amount = f"+{amount}"

    line = f"{number}. {entry.day:%d.%m} · {entry.description} — {amount}"
    if show_category:
        line += f" — {entry.category}"
    return line


def format_listing(
    title: str,
    entries: list[Entry],
    total: int,
    show_category: bool = True,
) -> str:
    lines = [f"🧾 {title}", ""]

    if not entries:
        lines.append("Записей нет.")
        return "\n".join(lines)

    lines += [
        format_entry_line(number, entry, show_category)
        for number, entry in enumerate(entries, start=1)
    ]

    if total > len(entries):
        lines += ["", f"Показал {len(entries)} из {total}."]
    return "\n".join(lines)
