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


def entries_phrase(count: int) -> str:
    tail = "записей"
    if count % 10 == 1 and count % 100 != 11:
        tail = "запись"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        tail = "записи"
    return f"{count} {tail}"


def format_listing(
    title: str,
    entries: list[Entry],
    total: int,
    show_category: bool = True,
    subtitle: str | None = None,
) -> str:
    lines = [f"🧾 {title}"]
    if subtitle is not None:
        lines.append(subtitle)
    lines.append("")

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


def format_category_listing(
    category: str, period_title: str, matched: list[Entry], limit: int = MAX_LIST_SIZE
) -> str:
    """Траты одной категории за период. Категорию в строках не повторяем — она в заголовке."""
    total_amount = sum(entry.amount for entry in matched)
    return format_listing(
        f"{category} — {period_title}",
        matched[:limit],
        total=len(matched),
        show_category=False,
        subtitle=f"{format_amount(total_amount)} · {entries_phrase(len(matched))}",
    )
