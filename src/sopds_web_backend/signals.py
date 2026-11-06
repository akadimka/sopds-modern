"""Доступ к Telegram-каналу библиотеки следует за аккаунтом SOPDS:
удалили или заблокировали аккаунт — бот удаляет человека из канала
(sopds_web_backend/telegram_users.py)."""
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import TelegramLink, UserProfile


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


@receiver(post_save, sender=User)
def _create_profile(sender, instance, created, raw=False, **kwargs):
    """Профиль — сразу у каждого нового пользователя: в нём настройки его
    библиотеки (открыта всем, личные квоты — docs/multiuser-design.md)."""
    if created and not raw:
        UserProfile.objects.get_or_create(user=instance)


def _record_lockout(sender, request=None, username=None, **kwargs):
    """django-axes заблокировал вход после неудачных попыток — в журнал."""
    from . import activity
    from .usernames import find_user
    activity.record(request, "locked_out", user=find_user(username or ""), username=username or "", ok=False,
                    entered=username)


try:
    from axes.signals import user_locked_out
    user_locked_out.connect(_record_lockout, dispatch_uid="sopds-activity-lockout")
except ImportError:  # django-axes не установлен
    pass


def _record_admin_change(sender, instance, created, raw=False, **kwargs):
    """Изменение в /admin/ (django.contrib.admin пишет LogEntry) — в журнал:
    блокировка и разблокировка пользователей и всё прочее, что правят там."""
    if not created or raw:
        return
    from . import activity
    model = instance.content_type.model_class() if instance.content_type_id else None
    is_user = model is User
    target = User.objects.filter(pk=instance.object_id).first() if is_user and instance.object_id else None
    action = {1: "added", 2: "changed", 3: "deleted"}.get(instance.action_flag, "changed")
    activity.record(None, "admin_user_changed" if is_user else "admin_changed",
                    user=target, username=instance.object_repr if is_user else "", actor=instance.user,
                    channel="web", obj=("" if is_user else (model._meta.verbose_name if model else "object"),
                                        "" if is_user else instance.object_id or "",
                                        "" if is_user else instance.object_repr),
                    admin_action=action, message=instance.get_change_message())


try:
    from django.contrib.admin.models import LogEntry
    post_save.connect(_record_admin_change, sender=LogEntry, dispatch_uid="sopds-activity-admin")
except ImportError:  # без django.contrib.admin
    pass


@receiver(user_logged_out)
def leave_online_list(sender, request, user, **kwargs):
    """Вышел с сайта — из «Сейчас на сайте» сразу, не через 5 минут (читалка — свой канал)."""
    if user is not None:
        from . import online
        online.forget(user.pk, "web")
