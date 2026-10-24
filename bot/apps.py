from django.apps import AppConfig


class BotConfig(AppConfig):
    name = 'bot'

    def ready(self):   # claude code changed: 2026-10-22 — dev-only template boundary markers (off unless SQT_TEMPLATE_MARKERS=1; never with DATABASE_URL)
        from bot.dev_template_markers import install_if_requested   # claude code changed: 2026-10-22
        install_if_requested()   # claude code changed: 2026-10-22
