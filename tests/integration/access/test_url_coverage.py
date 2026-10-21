"""Полнота проверки изоляции (docs/multiuser-plan.md шаг 1.9): каждый адрес
сайта и OPDS либо проверен на изоляцию библиотек (ссылка на тест, где чужая
книга не видна), либо явно отмечен как не отдающий данных каталога.

Новый адрес без отметки роняет этот тест: добавьте ему фильтр видимости
(opds_catalog/visibility.py) и тест изоляции — или объясните, почему данных
каталога в нём нет.
"""
import importlib

from django.urls import URLResolver, get_resolver

OPDS = "tests.integration.access.test_opds_isolation"
WEB = "tests.integration.access.test_web_isolation"

ISOLATION_TESTED = {
    # OPDS: книги и файлы по id
    "opds:download": (OPDS, "test_foreign_book_is_not_served"),
    "opds:cover": (OPDS, "test_foreign_book_is_not_served"),
    "opds:thumb": (OPDS, "test_foreign_book_is_not_served"),
    "opds:view_html": (OPDS, "test_foreign_book_is_not_served"),
    "opds:convert": (OPDS, "test_foreign_book_is_not_served"),
    "opds:save_progress": (OPDS, "test_foreign_book_is_not_served"),
    # OPDS: списки, поиск, счётчики
    "opds:main": (OPDS, "test_genre_and_main_counters_are_per_user"),
    "opds:genres": (OPDS, "test_genre_and_main_counters_are_per_user"),
    "opds:catalogs": (OPDS, "test_feeds_show_only_visible"),
    "opds:cat_tree": (OPDS, "test_foreign_catalog_folder_is_not_shown"),
    "opds:cat_page": (OPDS, "test_catalog_page_and_bookshelf"),
    "opds:bookshelf": (OPDS, "test_catalog_page_and_bookshelf"),
    "opds:searchbooks": (OPDS, "test_feeds_show_only_visible"),
    "opds:searchauthors": (OPDS, "test_feeds_show_only_visible"),
    "opds:searchseries": (OPDS, "test_author_series_page_hides_foreign_series"),
    "opds:char_books": (OPDS, "test_first_letter_menus_count_only_visible"),
    "opds:char_authors": (OPDS, "test_first_letter_menus_count_only_visible"),
    "opds:char_series": (OPDS, "test_first_letter_menus_count_only_visible"),
    "opds:nolang_books": (OPDS, "test_other_letter_menus"),
    "opds:nolang_authors": (OPDS, "test_other_letter_menus"),
    "opds:nolang_series": (OPDS, "test_other_letter_menus"),
    "opds:chars_books": (OPDS, "test_other_letter_menus"),
    "opds:chars_authors": (OPDS, "test_other_letter_menus"),
    "opds:chars_series": (OPDS, "test_other_letter_menus"),
    # Сайт
    "web:main": (WEB, "test_main_page_and_every_page_context"),
    "web:searchbooks": (WEB, "test_foreign_ids_in_search_reveal_nothing"),
    "web:searchauthors": (WEB, "test_author_and_series_search"),
    "web:searchseries": (WEB, "test_author_and_series_search"),
    "web:suggest": (WEB, "test_suggestions"),
    "web:book_card": (WEB, "test_book_card"),
    "web:catalog": (WEB, "test_catalog_folder"),
    "web:book": (WEB, "test_menus_count_only_visible"),
    "web:author": (WEB, "test_menus_count_only_visible"),
    "web:series": (WEB, "test_menus_count_only_visible"),
    "web:genre": (WEB, "test_menus_count_only_visible"),
    "web:ratings_progress": (WEB, "test_rating_progress_admin_only"),
}

NO_CATALOG_DATA = {
    "opds:lang_books": "меню выбора алфавита — без данных",
    "opds:lang_authors": "меню выбора алфавита — без данных",
    "opds:lang_series": "меню выбора алфавита — без данных",
    "opds:opensearch": "описание OpenSearch — без данных",
    "opds:searchtypes": "меню видов поиска — повторяет введённый запрос",
    "opds:covertmpl": "шаблон адреса обложки без id — книгу не отдаёт",
    "web:login": "вход",
    "web:login_code": "код входа",
    "web:logout": "выход",
    "web:offline": "страница «нет сети»",
    "web:profile": "профиль самого пользователя",
    "web:bsdel": "удаление своей записи полки",
    "web:bsclear": "очистка своей полки",
    "web:settings": "настройки — только суперпользователь",
    "web:telegram_chats": "настройки Telegram — только суперпользователь",
    "web:telegram_test": "настройки Telegram — только суперпользователь",
    "web:users_list": "пользователи — только суперпользователь",
    "web:user_create": "пользователи — только суперпользователь",
    "web:user_edit": "пользователи — только суперпользователь",
    "web:user_delete": "пользователи — только суперпользователь",
    "web:user_device_delete": "устройства пользователей — только суперпользователь",
    "web:user_telegram_unlink": "пользователи — только суперпользователь",
    "web:scan_start": "запуск скана — только суперпользователь",
    "web:scan_status": "статус скана — без книг",
    "web:ratings_restart": "перезапуск рейтингов — только суперпользователь",
}

# Инструменты персонала: суперпользователь и так видит всю библиотеку.
STAFF_NAMESPACES = {"fb2parser", "admin", "djdt"}
# Служебные адреса вне пространств имён.
OTHER_ALLOWED = {"set_language", "service_worker", None}


def _url_names():
    def walk(resolver, ns=""):
        for p in resolver.url_patterns:
            if isinstance(p, URLResolver):
                yield from walk(p, p.namespace or ns)
            else:
                yield ns, p.name
    return set(walk(get_resolver()))


def test_every_url_is_classified():
    unclassified = []
    for ns, name in sorted(_url_names(), key=str):
        if ns in STAFF_NAMESPACES:
            continue
        if ns in ("opds", "web"):
            key = f"{ns}:{name}"
            if key not in ISOLATION_TESTED and key not in NO_CATALOG_DATA:
                unclassified.append(key)
        elif name not in OTHER_ALLOWED:
            unclassified.append(f"{ns}:{name}")
    assert not unclassified, (
        f"Адреса без проверки изоляции библиотек: {unclassified} — добавьте фильтр видимости и тест "
        "(tests/integration/access/) или отметьте в NO_CATALOG_DATA с причиной")


def test_classification_has_no_stale_or_overlapping_entries():
    names = {f"{ns}:{name}" for ns, name in _url_names() if ns in ("opds", "web")}
    assert not set(ISOLATION_TESTED) & set(NO_CATALOG_DATA)
    assert not (set(ISOLATION_TESTED) | set(NO_CATALOG_DATA)) - names, "адрес удалён — уберите его отсюда"


def test_referenced_isolation_tests_exist():
    for key, (module, test) in ISOLATION_TESTED.items():
        assert callable(getattr(importlib.import_module(module), test, None)), f"{key}: нет теста {module}.{test}"
