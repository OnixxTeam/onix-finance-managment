import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

from catalog import SYSTEM_CATEGORY, Category
from categories import EXPENSE_CATEGORIES, EXPENSE_KEYWORDS
from config import GOOGLE_SHEETS_CREDENTIALS_PATH, GOOGLE_SPREADSHEET_ID

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DB_SHEET = "БД"
HEADER_ROW = ["Дата", "Описание", "Сумма", "Категория", "Тип"]

# Колонка «Категория» в листе БД: её переписывает переименование категории.
CATEGORY_COLUMN = "D"

# Справочник категорий расходов. Ключевые слова правятся руками прямо в таблице,
# бот их только читает.
CATEGORIES_SHEET = "Категории"
CATEGORIES_HEADER = ["id", "Название", "Ключевые слова", "Служебная"]
KEYWORDS_SEPARATOR = ","
SYSTEM_FLAG = "да"

# Одно чтение листа обслуживает серию экранов (отчёт, список, разбивка по категории),
# иначе каждый клик — отдельный запрос, а квота Sheets API 60 чтений в минуту.
CACHE_TTL_SECONDS = 60

# Служебный лист с парами ключ-значение: состояние бота, которое должно пережить
# перезапуск контейнера (у него нет своего тома).
STATE_SHEET = "Состояние"
STATE_HEADER = ["Ключ", "Значение"]

TYPE_EXPENSE = "Расход"
TYPE_INCOME = "Доход"

# Google Sheets/Excel serial date epoch.
SHEETS_DATE_EPOCH = date(1899, 12, 30)


def _to_sheets_serial_date(d: date) -> int:
    return (d - SHEETS_DATE_EPOCH).days


def _from_sheets_serial_date(value) -> date | None:
    """Дата из ячейки. Числа читаем как serial date, строки — как dd.mm.yyyy."""
    if isinstance(value, (int, float)):
        return SHEETS_DATE_EPOCH + timedelta(days=int(value))
    if isinstance(value, str):
        for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                continue
    return None


@dataclass(frozen=True)
class Entry:
    day: date
    description: str
    amount: float
    category: str
    entry_type: str


def category_to_row(category: Category) -> list:
    return [
        category.id,
        category.name,
        f"{KEYWORDS_SEPARATOR} ".join(category.keywords),
        SYSTEM_FLAG if category.is_system else "",
    ]


def category_from_row(row: list) -> Category | None:
    """None для строк, которые справочником не являются: пустых или с нечисловым id."""
    if len(row) < 2 or not str(row[1]).strip():
        return None
    try:
        category_id = int(row[0])
    except (TypeError, ValueError):
        return None

    raw_keywords = str(row[2]) if len(row) > 2 else ""
    keywords = tuple(
        word.strip().lower() for word in raw_keywords.split(KEYWORDS_SEPARATOR) if word.strip()
    )
    is_system = len(row) > 3 and str(row[3]).strip().lower() == SYSTEM_FLAG
    return Category(id=category_id, name=str(row[1]).strip(), keywords=keywords, is_system=is_system)


def seed_categories() -> list[Category]:
    """Первое наполнение листа: то, что раньше было константами в categories.py."""
    return [
        Category(
            id=number,
            name=name,
            keywords=tuple(EXPENSE_KEYWORDS.get(name, ())),
            is_system=name == SYSTEM_CATEGORY,
        )
        for number, name in enumerate(EXPENSE_CATEGORIES, start=1)
    ]


class _Cache:
    """Значение с временем жизни. Пишущие операции сбрасывают его явно, TTL нужен
    для правок, сделанных руками в самой таблице."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS):
        self._ttl = ttl
        self._value = None
        self._stored_at = 0.0

    def get(self):
        if self._value is None or time.monotonic() - self._stored_at > self._ttl:
            return None
        return self._value

    def put(self, value):
        self._value = value
        self._stored_at = time.monotonic()

    def drop(self) -> None:
        self._value = None


class SheetsClient:
    def __init__(self):
        creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDENTIALS_PATH, scopes=SCOPES)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(GOOGLE_SPREADSHEET_ID)
        self._sheet = self._get_or_create_sheet(DB_SHEET, HEADER_ROW, rows=1000)

        # Числовой формат колонок задаём отдельно от записи строк (не через
        # value_input_option="USER_ENTERED" на всю строку) — иначе Sheets
        # начинает "по-умному" интерпретировать и текстовые колонки тоже
        # (категория, тип), что и сломало категории в прошлый раз.
        self._sheet.format("A2:A1000", {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}})
        self._sheet.format("C2:C1000", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.##"}})

        self._entries_cache = _Cache()
        self._categories_cache = _Cache()

    def _get_or_create_sheet(self, title: str, header: list[str], rows: int) -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            sheet = self._spreadsheet.add_worksheet(title=title, rows=rows, cols=len(header))
            sheet.append_row(header)
            return sheet

    def _add_row(self, description: str, amount: float, category: str, entry_type: str) -> None:
        row_date = _to_sheets_serial_date(date.today())
        self._sheet.append_row([row_date, description, amount, category, entry_type])
        self._entries_cache.drop()

    def add_expense(self, description: str, amount: float, category: str) -> None:
        self._add_row(description, amount, category, TYPE_EXPENSE)

    def add_income(self, description: str, amount: float, category: str) -> None:
        self._add_row(description, amount, category, TYPE_INCOME)

    def fetch_entries(self) -> list[Entry]:
        cached = self._entries_cache.get()
        if cached is None:
            cached = self._read_entries()
            self._entries_cache.put(cached)
        return cached

    def _read_entries(self) -> list[Entry]:
        """Все записи листа. Значения читаем сырыми, иначе суммы приходят строками
        с разделителями разрядов, а дата — уже отформатированным текстом."""
        rows = self._sheet.get_values(value_render_option="UNFORMATTED_VALUE")

        entries = []
        for row in rows[1:]:
            if len(row) < len(HEADER_ROW):
                continue

            day = _from_sheets_serial_date(row[0])
            if day is None:
                continue
            try:
                amount = float(row[2])
            except (TypeError, ValueError):
                continue

            entries.append(
                Entry(
                    day=day,
                    description=str(row[1]),
                    amount=abs(amount),
                    category=str(row[3]),
                    entry_type=str(row[4]),
                )
            )
        return entries

    def fetch_categories(self) -> list[Category]:
        """Справочник категорий расходов. Пустой или отсутствующий лист засеваем
        константами из categories.py — иначе после деплоя бот остаётся без категорий."""
        cached = self._categories_cache.get()
        if cached is not None:
            return cached

        sheet = self._get_or_create_sheet(
            CATEGORIES_SHEET, CATEGORIES_HEADER, rows=len(EXPENSE_CATEGORIES) + 50
        )
        rows = sheet.get_values()
        categories = [
            category for category in map(category_from_row, rows[1:]) if category is not None
        ]
        if not categories:
            categories = seed_categories()
            self._write_categories(sheet, categories)

        self._categories_cache.put(categories)
        return categories

    def save_categories(self, categories: list[Category]) -> None:
        sheet = self._get_or_create_sheet(
            CATEGORIES_SHEET, CATEGORIES_HEADER, rows=len(categories) + 50
        )
        self._write_categories(sheet, categories)
        self._categories_cache.put(categories)

    def _write_categories(self, sheet: gspread.Worksheet, categories: list[Category]) -> None:
        """Перезаписывает лист целиком: справочник маленький, а построчная правка
        разъезжается с номерами строк после удалений."""
        sheet.clear()
        sheet.update([CATEGORIES_HEADER] + [category_to_row(category) for category in categories])

    def count_by_category(self, category: str) -> int:
        return sum(1 for entry in self.fetch_entries() if entry.category == category)

    def recategorize(self, old_category: str, new_category: str) -> int:
        """Переписывает колонку «Категория» во всех записях старой категории.
        Возвращает число затронутых строк."""
        if old_category == new_category:
            return 0

        column = self._sheet.col_values(HEADER_ROW.index("Категория") + 1)
        updates = [
            {"range": f"{CATEGORY_COLUMN}{number}", "values": [[new_category]]}
            for number, value in enumerate(column[1:], start=2)
            if value == old_category
        ]
        if updates:
            self._sheet.batch_update(updates)
            self._entries_cache.drop()
        return len(updates)

    def get_state(self) -> dict[str, str]:
        """Служебные пары ключ-значение. Лист создаётся лениво, поэтому его
        отсутствие — не ошибка, а просто пустое состояние."""
        try:
            sheet = self._spreadsheet.worksheet(STATE_SHEET)
        except gspread.WorksheetNotFound:
            return {}

        rows = sheet.get_values()
        return {str(row[0]): str(row[1]) for row in rows[1:] if len(row) >= 2 and row[0]}

    def save_state(self, values: dict[str, str]) -> None:
        """Перезаписывает служебный лист целиком: состояние маленькое и всегда
        пишется полным набором ключей."""
        sheet = self._get_or_create_sheet(STATE_SHEET, STATE_HEADER, rows=10)
        sheet.clear()
        sheet.update([STATE_HEADER] + [[key, value] for key, value in values.items()])
