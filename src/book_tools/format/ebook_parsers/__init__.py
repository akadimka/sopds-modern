# parsers/__init__.py
#
# Баг №105: раньше реэкспортировал EbookParser/ParserFactory
# (abstract-base-class + registry для расширений файлов) — ни один
# внешний вызывающий не обращался к пакету через этот __init__.py вовсе
# (book_tools/services.py импортирует Author/BookMetadata/Series прямо
# из .dto, минуя реэкспорт), и никто не подписывался на реестр через
# ParserFactory.register(). Убрано целиком вместе с base.py/factory.py
# (см. docs/quality-roadmap.md, баг №105). dto.py (Author/BookMetadata/
# Series/Cover) — реально используемые dataclass'ы — не тронуты.
