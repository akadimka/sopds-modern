"""Читательская часть Telegram-бота библиотеки (docs/telegram-library-bot.md).

Подключается к процессу бота (`manage.py telegram_bot`) как extra_handler
AdminBot: всё, что не решения админа по спорным порциям, — сюда.
- привязка аккаунта: /start link_<код> (ссылка из профиля) или сам код;
- заявки на вступление в канал (одобряются только привязанным);
- поиск и скачивание книг — только привязанным пользователям.
"""
from __future__ import annotations

import html
import logging
from typing import Any, Dict

from .telegram_users import (
    ERR_TAKEN,
    channel_join_link,
    handle_join_request,
    link_by_code,
    linked_user,
    looks_like_code,
)

_log = logging.getLogger(__name__)

INTRO = ("Это бот библиотеки SOPDS — он ищет и присылает книги читателям библиотеки.\n\n"
         "Чтобы пользоваться им, привяжите свой аккаунт: на сайте библиотеки откройте профиль, "
         "в разделе «Telegram» нажмите «Получить код» и отправьте код сюда "
         "(или просто откройте ссылку оттуда).")
HELP = "Напишите название книги, автора или серии — я поищу в библиотеке."


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


class LibraryHandler:
    def __call__(self, service, client, update: Dict[str, Any]) -> bool:
        from django.db import close_old_connections
        close_old_connections()  # процесс бота живёт долго — соединение с БД могло устареть
        if "chat_join_request" in update:
            handle_join_request(client, service, update["chat_join_request"])
            return True
        msg = update.get("message")
        if msg and (msg.get("chat") or {}).get("type") == "private" and msg.get("from"):
            text = (msg.get("text") or "").strip()
            if text.split(" ")[0] == "/id":
                return False  # chat id отвечает сам AdminBot
            self.on_text(service, client, msg, text)
            return True
        return False

    def on_text(self, service, client, msg: Dict[str, Any], text: str) -> None:
        tg_user, chat_id = msg["from"], msg["chat"]["id"]
        if text.startswith("/start"):
            payload = text[len("/start"):].strip()
            if payload.lower().startswith("link_"):
                self.link(service, client, chat_id, tg_user, payload)
                return
            client.send_message(chat_id, HELP if linked_user(tg_user["id"]) else INTRO)
            return
        user = linked_user(tg_user["id"])
        if user is None:
            if looks_like_code(text):
                self.link(service, client, chat_id, tg_user, text)
            else:
                client.send_message(chat_id, INTRO)
            return
        self.on_query(service, client, chat_id, user, text)

    def link(self, service, client, chat_id, tg_user: Dict[str, Any], code: str) -> None:
        link, error = link_by_code(code, tg_user)
        if link is None:
            client.send_message(chat_id, (
                "Этот Telegram уже привязан к другому аккаунту библиотеки — отвяжите его там "
                "или обратитесь к администратору." if error == ERR_TAKEN else
                "Код не подошёл или устарел — получите новый в профиле на сайте библиотеки."))
            return
        lines = [f"✅ Готово: Telegram привязан к аккаунту «{_e(link.user.username)}»."]
        try:
            join = channel_join_link(client, service)
        except Exception as e:  # без канала бот всё равно полезен
            _log.warning("telegram: ссылка в канал не создана: %s", e)
            join = ""
        if join:
            lines.append(f'Канал с новинками: {_e(join)} — подайте заявку, бот одобрит её сам.')
        lines += ["", HELP]
        client.send_message(chat_id, "\n".join(lines))

    def on_query(self, service, client, chat_id, user, text: str) -> None:
        client.send_message(chat_id, "Поиск книг появится в ближайшем обновлении бота.")
