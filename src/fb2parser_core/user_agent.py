"""Подпись программы в исходящих HTTP-запросах (Telegram, Wikidata).

`sopds-modern/<версия>` — без ссылок на хостинг кода. Стандартную подпись
«Python-urllib/…» Cloudflare (ретранслятор Telegram) отклоняет ошибкой 1010,
а Wikidata просит у программ осмысленную подпись.
"""


def _user_agent() -> str:
    try:
        from sopds.version import display_version
        return f"sopds-modern/{display_version()}"
    except Exception:  # пакет sopds недоступен (fb2parser_core отдельно)
        return "sopds-modern"


USER_AGENT = _user_agent()
