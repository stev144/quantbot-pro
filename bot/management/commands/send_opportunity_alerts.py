# bot/management/commands/send_opportunity_alerts.py  # claude code changed: 2026-10-28 — new
"""python manage.py send_opportunity_alerts            check the monitors, push a phone notification on any change
   python manage.py send_opportunity_alerts --test     send one test notification
   python manage.py send_opportunity_alerts --dry-run  show what would be sent, send nothing, change nothing
Schedule it hourly (Task Scheduler). Needs NTFY_TOPIC in .env and the ntfy app subscribed to that topic."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.notifications import ntfy
from bot.research import opportunity_alerts as A


class Command(BaseCommand):
    help = "Phone alerts (ntfy) when a watched opportunity or a running test changes state"

    def add_arguments(self, parser):
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **o):
        if not settings.NTFY_TOPIC and not o["dry_run"]:
            raise CommandError("NTFY_TOPIC is not set in .env")
        if o["test"]:
            ok = ntfy.send("Steph Quant alerts are working",
                           "You will get a message here when a funding opportunity appears or the FX carry test needs "
                           "attention. Nothing else.", tags="white_check_mark")
            self.stdout.write("test notification sent" if ok else "test notification FAILED — check NTFY_TOPIC / network")
            return
        sent = A.run(A.gather(), ntfy.send, dry_run=o["dry_run"])
        for s in sent:
            self.stdout.write(f"{'WOULD SEND' if o['dry_run'] else ('sent' if s['sent'] else 'FAILED')}: "
                              f"{s['title']}  ({s['key']}: {s['from']} → {s['to']})")
        if not sent:
            self.stdout.write("no changes — nothing sent")
