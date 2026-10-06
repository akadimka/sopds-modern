"""Доступ к Telegram-каналу библиотеки следует за аккаунтом SOPDS:
удалили или заблокировали аккаунт — бот удаляет человека из канала
(sopds_web_backend/telegram_users.py)."""
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import TelegramLink


@receiver(pre_delete, sender=TelegramLink)
def _kick_on_link_delete(sender, instance, **kwargs):
    if instance.telegram_id is not None and instance.in_channel:
        from .telegram_users import kick_from_channel
        kick_from_channel(instance.telegram_id)


@receiver(post_save, sender=User)
def _kick_on_deactivate(sender, instance, **kwargs):
    if instance.is_active:
        return
    link = TelegramLink.objects.filter(user=instance, telegram_id__isnull=False, in_channel=True).first()
    if link is not None:
        from .telegram_users import kick_from_channel
        if kick_from_channel(link.telegram_id):
            TelegramLink.objects.filter(pk=link.pk).update(in_channel=False)
