from datetime import date

from listing import (
    DEFAULT_LIST_SIZE,
    MAX_LIST_SIZE,
    format_category_listing,
    format_entry_line,
    format_listing,
    in_category,
    parse_limit,
    recent,
)
from sheets import TYPE_EXPENSE, TYPE_INCOME, Entry


def expense(description: str, amount: float = 100.0, day: date = date(2026, 8, 26)) -> Entry:
    return Entry(
        day=day,
        description=description,
        amount=amount,
        category="Транспорт",
        entry_type=TYPE_EXPENSE,
    )


def test_parse_limit_defaults_when_argument_missing():
    assert parse_limit(None) == DEFAULT_LIST_SIZE
    assert parse_limit("   ") == DEFAULT_LIST_SIZE


def test_parse_limit_reads_number():
    assert parse_limit("3") == 3
    assert parse_limit(" 7 ") == 7


def test_parse_limit_clamps_to_maximum():
    """Больше двадцати строк в чате нечитаемо, а 4096 символов Telegram режет."""
    assert parse_limit("500") == MAX_LIST_SIZE


def test_parse_limit_rejects_junk_and_non_positive():
    assert parse_limit("абв") is None
    assert parse_limit("0") is None
    assert parse_limit("-5") is None


def test_recent_takes_last_entries_newest_first():
    entries = [expense("первая"), expense("вторая"), expense("третья")]

    assert [entry.description for entry in recent(entries, 2)] == ["третья", "вторая"]


def test_recent_survives_short_history():
    assert len(recent([expense("одна")], 10)) == 1
    assert recent([], 10) == []


def test_entry_line_shows_date_amount_and_category():
    line = format_entry_line(1, expense("кофе", amount=300, day=date(2026, 8, 26)))

    assert line.startswith("1. 26.08")
    assert "кофе" in line
    assert "300" in line
    assert "Транспорт" in line


def test_income_line_is_marked_with_plus():
    entry = Entry(
        day=date(2026, 8, 1),
        description="зарплата",
        amount=100000.0,
        category="Стабильный доход",
        entry_type=TYPE_INCOME,
    )

    assert "+100 000" in format_entry_line(1, entry)


def test_entry_line_can_hide_category():
    line = format_entry_line(1, expense("кофе"), show_category=False)

    assert "Транспорт" not in line


def test_listing_numbers_lines_and_reports_the_cut():
    entries = [expense(f"трата {number}") for number in range(1, 6)]

    text = format_listing("Последние записи", recent(entries, 3), total=len(entries))

    assert "1. " in text and "3. " in text
    assert "4. " not in text
    assert "5" in text, "пользователь должен видеть, что показана не вся история"


def test_listing_without_cut_has_no_note():
    entries = [expense("одна")]

    text = format_listing("Последние записи", recent(entries, 10), total=len(entries))

    assert "из" not in text


def test_empty_listing_says_so():
    assert "нет" in format_listing("Последние записи", [], total=0).lower()


def test_in_category_filters_by_name_and_period():
    entries = [
        expense("прошлый месяц", day=date(2026, 7, 20)),
        expense("этот месяц", day=date(2026, 8, 3)),
        Entry(
            day=date(2026, 8, 4),
            description="кино",
            amount=500.0,
            category="Развлечения",
            entry_type=TYPE_EXPENSE,
        ),
    ]

    matched = in_category(entries, "Транспорт", "month", today=date(2026, 8, 26))

    assert [entry.description for entry in matched] == ["этот месяц"]


def test_in_category_returns_newest_first():
    entries = [expense("первая"), expense("вторая")]

    matched = in_category(entries, "Транспорт", "all", today=date(2026, 8, 26))

    assert [entry.description for entry in matched] == ["вторая", "первая"]


def test_category_listing_shows_total_and_count():
    matched = [expense("такси", amount=300), expense("метро", amount=70)]

    text = format_category_listing("Транспорт", "Август 2026", matched)

    assert "Транспорт — Август 2026" in text
    assert "370" in text
    assert "2 записи" in text
    assert text.count("Транспорт") == 1, "категория уже в заголовке, в строках она лишняя"


def test_category_listing_cuts_long_history():
    matched = [expense(f"трата {number}") for number in range(MAX_LIST_SIZE + 5)]

    text = format_category_listing("Транспорт", "Август 2026", matched)

    assert f"Показал {MAX_LIST_SIZE} из {MAX_LIST_SIZE + 5}." in text
