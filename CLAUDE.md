# sopds-modern

Django 5.2 веб-приложение: OPDS-каталог электронных книг (SOPDS) +
инструмент организации FB2-библиотеки (fb2parser), в одном репозитории.

## Стек и структура

- Django 5.2, Python 3.13, SQLite (WAL). htmx + Foundation 6 на фронте.
- `src/sopds/settings/` — настройки (`base.py`, `local.py` — dev, LocMemCache).
- `src/opds_catalog/` — модели SOPDS, сканер (`sopdscan.py`), OPDS-фиды.
- `src/sopds_web_backend/` — основной SOPDS web UI (`/web/`).
- `src/fb2parser_core/` — логика организации библиотеки: 6-пасовый
  пайплайн определения автора/серии/жанра (`regen_csv.py` + `passes/`),
  компилятор многотомных серий в один файл (`fb2_compiler.py`),
  синхронизация в библиотеку (`synchronization.py`).
- `src/fb2parser_web/` — Django web-UI для fb2parser_core (`/fb2parser/`).
- `src/book_tools/` — парсеры FB2/EPUB/MOBI.
- `src/fb2_data/` — пользовательские данные: CSV, `settings/app_settings.json`,
  `genres.xml`.

**Важно:** fb2parser полностью вендорен в этом репозитории
(`fb2parser_core`/`fb2parser_web`) — это НЕ мост к внешнему проекту.
Старый standalone Tkinter-инструмент (когда-то был по пути
`c:\Temp\fb2parser`) устарел и не связан с текущим кодом.

## План повышения качества regen_csv/fb2_compiler

См. `docs/quality-roadmap.md` — что уже сделано, что в очереди (пункт 4
требует прогона на домашней библиотеке пользователя, не на этой машине).

## regen_csv / fb2_compiler — регрессионные фикстуры

`fb2parser_core`'s пайплайн определения автора/серии/жанра — эвристический
и легко ломается на новых структурах папок. Каждый разобранный краевой
случай должен закрепляться тестом:
`tests/integration/fb2parser_core/` — см. **README.md там** за полным
процессом (как добавить новый кейс через `scripts/build_regen_fixtures.py`).
Запуск: `DJANGO_SETTINGS_MODULE=sopds.settings.local python -m pytest tests/integration/fb2parser_core/`.

## Обязательные конвенции

**i18n.** Любой новый UI-текст в шаблонах (`sopds_web_backend/templates/`,
`fb2parser_web/templates/`) — обязательно в `{% trans %}`/`{% blocktrans %}`,
плюс перевод в `locale/ru/LC_MESSAGES/django.po` соответствующего приложения.
Компиляция `.mo`: `python src/compile_messages.py` (не `manage.py
compilemessages` — `msgfmt` может отсутствовать в системе, скрипт
использует `polib`). Исключение: технические имена колонок CSV/полей
(`file_path`, `series_source` и т.п. в таблицах normalize.html) — всегда
на английском, не оборачиваются в `{% trans %}`.

**Выбор папки.** Любое текстовое поле ввода пути к папке в `/fb2parser/`
обязано иметь рядом кнопку «Обзор»/«📁» (htmx-запрос к
`/fb2parser/browse/?path=...`).

## Обновление проекта («обнови проект»)

1. `git fetch origin && git log HEAD..origin/master --oneline`
2. `git merge origin/master` (не rebase)
3. `python manage.py migrate`
4. `python compile_messages.py`
5. `python manage.py check`
6. Если менялся `pyproject.toml`/`uv.lock` — `uv sync` (или `pip install`
   недостающих пакетов вручную, если `uv` недоступен в шелле)

## Git-дисциплина

- Никогда `git add -A` — стейджить конкретные файлы.
- Коммитить только по явному подтверждению пользователя.
- `git fetch origin` + проверка расхождения перед каждым push.
- Коммит-сообщения заканчиваются `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
