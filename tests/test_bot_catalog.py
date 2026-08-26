from bot_catalog import _entries_phrase, catalog_text
from catalog import SYSTEM_CATEGORY, Category


def test_entries_phrase_declension():
    assert _entries_phrase(1) == "1 запись"
    assert _entries_phrase(3) == "3 записи"
    assert _entries_phrase(5) == "5 записей"
    assert _entries_phrase(0) == "0 записей"
    assert _entries_phrase(11) == "11 записей"
    assert _entries_phrase(21) == "21 запись"
    assert _entries_phrase(112) == "112 записей"


def test_catalog_text_numbers_categories_and_marks_system():
    text = catalog_text(
        [
            Category(id=1, name="Транспорт"),
            Category(id=2, name=SYSTEM_CATEGORY, is_system=True),
        ]
    )

    assert "1. Транспорт" in text
    assert f"2. {SYSTEM_CATEGORY} — служебная" in text
