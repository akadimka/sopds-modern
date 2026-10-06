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
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any, Dict, Optional

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
NAV_PREFIXES = ("q", "a", "s", "b")  # экраны поиска — telegram_search.screen_for


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


class LibraryHandler:
    def __init__(self, executor: Optional[Executor] = None):
        # подготовка файла (конвертация — до минут) не задерживает опрос бота
        self.executor = executor or ThreadPoolExecutor(max_workers=2)

    def __call__(self, service, client, update: Dict[str, Any]) -> bool:
        from django.db import close_old_connections
        close_old_connections()  # процесс бота живёт долго — соединение с БД могло устареть
        if "chat_join_request" in update:
            handle_join_request(client, service, update["chat_join_request"])
            return True
        cq = update.get("callback_query")
        prefix = (cq.get("data") or "").split(":")[0] if cq else ""
        if prefix in NAV_PREFIXES:
            self.on_callback(client, cq)
            return True
        if prefix == "f":
            self.on_download(client, cq)
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
        from .telegram_search import results_screen, store_query
        screen = results_screen(store_query(text), 0)
        if screen is not None:
            client.send_message(chat_id, screen[0], reply_markup=screen[1])

    def on_callback(self, client, cq: Dict[str, Any]) -> None:
        """Переход по экранам поиска — в том же сообщении."""
        from fb2parser_core.telegram_notify import TelegramError

        from .telegram_search import screen_for
        if linked_user((cq.get("from") or {}).get("id")) is None:
            client.answer_callback(cq["id"], "Нет доступа — привяжите аккаунт в профиле на сайте библиотеки.")
            return
        screen = screen_for(cq.get("data") or "")
        if screen is None:
            client.answer_callback(cq["id"], "Это устарело — повторите поиск.")
            return
        client.answer_callback(cq["id"])
        msg = cq.get("message") or {}
        try:
            client.edit_message((msg.get("chat") or {}).get("id"), msg.get("message_id"), screen[0],
                                reply_markup=screen[1])
        except TelegramError as e:
            if "not modified" not in e.description:  # нажали ту же страницу — не ошибка
                raise

    # ---------- скачивание ----------
    def on_download(self, client, cq: Dict[str, Any]) -> None:
        user = linked_user((cq.get("from") or {}).get("id"))
        if user is None:
            client.answer_callback(cq["id"], "Нет доступа — привяжите аккаунт в профиле на сайте библиотеки.")
            return
        try:
            _f, book_id, fmt = (cq.get("data") or "").split(":")
            book_id = int(book_id)
        except ValueError:
            client.answer_callback(cq["id"], "Это устарело — повторите поиск.")
            return
        client.answer_callback(cq["id"], f"⏳ Готовлю {fmt.upper()}…")
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id") or cq["from"]["id"]
        self.executor.submit(self.deliver, client, chat_id, user, book_id, fmt)

    def deliver(self, client, chat_id, user, book_id: int, fmt: str) -> None:
        """Отправить книгу в формате: уже загруженную — по file_id, иначе
        подготовить тем же кодом, что сайт (opds_catalog.dl)."""
        from django.db import close_old_connections

        from fb2parser_core.telegram_notify import MAX_UPLOAD, TelegramError
        from opds_catalog.dl import (
            ConversionFailed,
            ConversionUnavailable,
            available_formats,
            convert_book,
            original_file,
        )
        from opds_catalog.models import Book, bookshelf

        from .models import TelegramFile
        close_old_connections()
        try:
            book = Book.objects.exclude(avail=0).filter(id=book_id).prefetch_related("authors").first()
            if book is None or fmt not in available_formats(book):
                client.send_message(chat_id, "Эта книга или формат больше недоступны — повторите поиск.")
                return
            bookshelf.objects.get_or_create(user=user, book=book)  # как скачивание на сайте
            authors = ", ".join(a.full_name for a in book.authors.all())
            caption = f"📖 <b>{_e(book.title)}</b>" + (f"\n👤 {_e(authors)}" if authors else "")
            cached = TelegramFile.objects.filter(book=book, fmt=fmt, filesize=book.filesize).first()
            if cached is not None:
                try:
                    client.send_document_id(chat_id, cached.file_id, caption)
                    return
                except TelegramError:
                    cached.delete()  # file_id больше не действует — загрузим заново
            try:
                if fmt != book.format:
                    client.call("sendChatAction", chat_id=chat_id, action="upload_document")
                filename, data = original_file(book) if fmt == book.format else convert_book(book, fmt)
            except ConversionUnavailable:
                client.send_message(chat_id, f"Формат {fmt.upper()} сейчас недоступен.")
                return
            except ConversionFailed:
                client.send_message(chat_id, f"Не удалось подготовить {fmt.upper()} — попробуйте другой формат.")
                return
            if len(data) > MAX_UPLOAD:
                client.send_message(chat_id, f"Файл слишком большой для Telegram ({len(data) // (1024 * 1024)} МБ, "
                                             "предел — 50 МБ). Скачайте его на сайте библиотеки.")
                return
            sent = client.send_document(chat_id, filename, data, caption) or {}
            file_id = (sent.get("document") or {}).get("file_id")
            if file_id:
                TelegramFile.objects.update_or_create(book=book, fmt=fmt,
                                                      defaults={"filesize": book.filesize, "file_id": file_id})
        except TelegramError as e:
            _log.warning("telegram: книга %s (%s) не отправлена: %s", book_id, fmt, e.description)
            try:
                client.send_message(chat_id, "Не удалось отправить файл — попробуйте ещё раз позже.")
            except TelegramError:
                pass
        except Exception:
            _log.exception("telegram: сбой отправки книги %s (%s)", book_id, fmt)
        finally:
            import threading

            from django.db import connection
            if threading.current_thread() is not threading.main_thread():
                connection.close()  # у потока пула своё соединение с БД
