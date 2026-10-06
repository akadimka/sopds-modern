"""Процесс Telegram-бота: кнопки решений для админа (этап 5,
docs/watch-folder-autosync-design.md).

Long polling (getUpdates) — серверу не нужны HTTPS и входящие подключения.
- Нажатие кнопки жанра под сообщением о спорной порции → decide_all:
  жанр всей порции + синхронизация. Принимаются нажатия только от chat id
  администратора из настроек.
- /start или /id от кого угодно → ответ с его chat id (помогает настройке).
- Все увиденные чаты запоминаются — страница настроек показывает их, пока
  бот работает (getUpdates тогда занят им).
"""
from __future__ import annotations

import html
import json
import logging
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from .telegram_notify import TelegramClient, TelegramError, chats_from_updates

_log = logging.getLogger(__name__)

POLL_TIMEOUT = 50
KNOWN_CHATS_KEY = "tg_known_chats"


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


def remember_chats(journal, updates: List[dict]) -> None:
    known = json.loads(journal.get_state(KNOWN_CHATS_KEY) or "{}")
    for chat in chats_from_updates(updates):
        known[chat["id"]] = chat
    journal.set_state(KNOWN_CHATS_KEY, json.dumps(known, ensure_ascii=False))


def known_chats(journal) -> List[Dict[str, str]]:
    return list(json.loads(journal.get_state(KNOWN_CHATS_KEY) or "{}").values())


class AdminBot:
    def __init__(self, service_factory: Callable[[], Any],
                 client_factory: Optional[Callable[[dict], Any]] = None,
                 executor: Optional[Executor] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 extra_handler: Optional[Callable[[Any, Any, dict], bool]] = None):
        """extra_handler(service, client, update) -> обработано ли событие —
        читательская часть бота (поиск, скачивание, привязка аккаунтов,
        заявки в канал — sopds_web_backend/telegram_library.py)."""
        self.service_factory = service_factory
        self.extra_handler = extra_handler
        self.client_factory = client_factory or (
            lambda cfg: TelegramClient(cfg["telegram_token"], cfg["telegram_proxy"]))
        # решения выполняются по одному, не задерживая опрос
        self.executor = executor or ThreadPoolExecutor(max_workers=1)
        self.sleep = sleep
        self.stopped = False

    # ---------- цикл ----------
    def run_forever(self) -> None:
        offset: Optional[int] = None
        delay = 5.0
        while not self.stopped:
            try:
                offset = self.poll_once(offset)
                delay = 5.0
            except TelegramError as e:
                _log.warning("telegram_bot: %s", e.description)
                self.sleep(delay)
                delay = min(delay * 2, 300.0)
            except Exception:
                _log.exception("telegram_bot: сбой цикла")
                self.sleep(30)

    def poll_once(self, offset: Optional[int]) -> Optional[int]:
        service = self.service_factory()
        cfg = service.cfg
        if not cfg["telegram_token"]:
            self.sleep(60)  # токена нет — ждём, пока его зададут в настройках
            return offset
        client = self.client_factory(cfg)
        updates = client.get_updates(offset, timeout=POLL_TIMEOUT)
        if updates:
            remember_chats(service.journal, updates)
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                if (upd.get("callback_query") or {}).get("data", "").startswith("d:"):
                    self.on_callback(service, client, upd["callback_query"])
                elif self.extra_handler is not None and self.extra_handler(service, client, upd):
                    pass
                elif "message" in upd:
                    self.on_message(client, upd["message"])
            except TelegramError as e:
                _log.warning("telegram_bot: ответ не отправлен: %s", e.description)
        return offset

    # ---------- события ----------
    @staticmethod
    def on_message(client, message: dict) -> None:
        chat = message.get("chat") or {}
        text = (message.get("text") or "").strip()
        if chat.get("type") == "private" and text.split(" ")[0] in ("/start", "/id"):
            client.send_message(str(chat["id"]),
                                f"Ваш chat id: <code>{chat['id']}</code>\n"
                                "Его указывают в настройках SOPDS как «Chat id администратора».")

    def on_callback(self, service, client, cq: dict) -> None:
        cfg = service.cfg
        if str((cq.get("from") or {}).get("id")) != str(cfg["telegram_admin_chat"]):
            client.answer_callback(cq["id"], "Решения принимает только администратор.")
            return
        data = cq.get("data") or ""
        action = service.journal.get_action(data[2:]) if data.startswith("d:") else None
        if action is None:
            client.answer_callback(cq["id"], "Кнопка устарела — откройте «Входящие».")
            return
        msg = cq.get("message") or {}
        chat_id, message_id = (msg.get("chat") or {}).get("id"), msg.get("message_id")
        original = _e(msg.get("text") or "")
        keyboard = msg.get("reply_markup")
        client.answer_callback(cq["id"], f"⏳ {action['genre']}: синхронизирую…")
        client.edit_message(chat_id, message_id,
                            f"{original}\n\n⏳ Назначаю «{_e(action['genre'])}» и синхронизирую…",
                            reply_markup={"inline_keyboard": []})
        self.executor.submit(self._decide, action, chat_id, message_id, original, keyboard)

    def _decide(self, action: Dict[str, str], chat_id, message_id, original: str, keyboard) -> None:
        service = self.service_factory()
        client = self.client_factory(service.cfg)
        error = ""
        try:
            res = service.decide_all(action["batch"], action["genre"])
        except Exception as e:  # процесс бота не должен падать от одной порции
            _log.exception("telegram_bot: сбой решения")
            res = None
            error = str(e)
        if res is not None and res.status == "done":
            text = (f"{original}\n\n✅ «{_e(action['genre'])}»: перемещено {len(res.moved)}"
                    + (f", оставлено синхронизацией {res.kept}" if res.kept else "")
                    + (f", удалено как дубликаты {res.removed}" if res.removed else ""))
            markup = {"inline_keyboard": []}
        else:
            why = res.error if res is not None else error
            prefix = "⏸ Идёт другая синхронизация — нажмите позже" if res is not None and res.status == "busy" \
                else f"❌ {_e(why)}"
            text, markup = f"{original}\n\n{prefix}", keyboard or {"inline_keyboard": []}
        try:
            client.edit_message(chat_id, message_id, text, reply_markup=markup)
        except TelegramError as e:
            _log.warning("telegram_bot: итог решения не показан: %s", e.description)
