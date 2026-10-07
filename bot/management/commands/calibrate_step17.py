# claude code changed: new file — 2026-10-07, D-4 Step-17 null size calibration under the signed OPEN-36 criterion.
#   python manage.py calibrate_step17 --workers 6                      (the signed run: R = 3,202 testable, B = 999)
#   python manage.py calibrate_step17 --pilot --reps 8 --workers 8     (timing pilot — labelled, never evidence)
# Resumable: re-running the same command on the same code resumes from the checkpoint.
from django.core.management.base import BaseCommand, CommandError

from bot.research import step17_calibration as sc


class Command(BaseCommand):
    help = "D-4 Step-17 null size calibration (OPEN-36: R >= 3,202 testable, Wilson UB <= 6 %, B = 999)"

    def add_arguments(self, parser):
        parser.add_argument("--scenario", default="RW_GAUSSIAN_UNRELATED", choices=sorted(sc.SCENARIOS))
        parser.add_argument("--workers", type=int, default=1)
        parser.add_argument("--pilot", action="store_true", help="labelled pilot: --reps/--n-perm may differ; never evidence")
        parser.add_argument("--reps", type=int, default=None, help="pilot only (the signed run always uses 3,202)")
        parser.add_argument("--n-perm", type=int, default=sc.SIGNED_B, help="pilot only (the signed run always uses 999)")

    def handle(self, *args, **o):
        if not o["pilot"] and (o["reps"] is not None or o["n_perm"] != sc.SIGNED_B):
            raise CommandError("--reps/--n-perm may only differ from the signed values with --pilot")
        try:
            s = sc.run_size_calibration(o["scenario"], target=o["reps"], n_perm=o["n_perm"], workers=o["workers"],
                                        pilot=o["pilot"], log=lambda m: self.stdout.write(m))
        except ValueError as e:
            raise CommandError(str(e))
        keys = ("label", "status", "reason", "n_testable", "n_untestable", "rejections", "rejection_rate",
                "wilson_upper_one_sided_95", "allowed_upper_bound", "open36_gate", "d4_band", "replicate_seconds_total",
                "runtime_s_this_session", "code_commit", "code_dirty", "output_path")
        for k in keys:
            if k in s:
                self.stdout.write(f"  {k}: {s[k]}")
