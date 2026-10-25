# claude code changed: 2026-10-25 — new. LTHP-1 exploratory hedge study (LTHP_1_PREREGISTRATION_2026-10-25.md).
# Historical simulation only: no orders, no DRY_RUN change. Refuses to run if the registered protocol file was altered.
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from bot.research.lthp_study import run_lthp_study


class Command(BaseCommand):
    help = "EXPLORATORY LTHP-1: GBP/JPY hedge-after-5th-fill arms vs baseline (history frozen at the FX seal)."

    def handle(self, *args, **o):
        root = Path(settings.BASE_DIR)
        self.stdout.write(self.style.WARNING("EXPLORATORY · may not select/inform the full-ladder stop rule's K · no production use"))
        r = run_lthp_study(root, root / "research_data" / "lthp_study")
        t = r["statistics"]["tests_primary_10000_gross"]
        for name, v in t.items():
            self.stdout.write(f"  {name:24s} total vs baseline {v['total_diff_usd']:+10.2f} USD · months better/worse "
                              f"{v['months_better']}/{v['months_worse']} · p {v['p_one_sided']:.3f} (Holm {v['p_holm']:.3f})")
        if not r["statistics"]["calibration_ok"]:
            self.stdout.write(self.style.WARNING("  p-values UNRELIABLE (registered calibration failed at rho = 0.3)"))
        self.stdout.write(self.style.SUCCESS(f"report: {r['path']}"))
