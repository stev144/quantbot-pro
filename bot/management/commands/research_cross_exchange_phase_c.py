# claude code changed: new file — 2026-10-25, cross-exchange research PHASE C (preregistered confirmatory test XARB-C1).
#   --draft [--fee venue=rate …]   write the registration draft (never replaces an existing one)
#   --sign "<name>"                owner act: freeze the draft and stamp the registration time
#   --status                       show the registration and how many eligible post-registration hours exist
#   --calibrate                    false-pass / power checks + the Phase B session run as a known-answer (NOT evidence)
#   --evaluate                     the single, one-shot evaluation (refuses unless signed and enough fresh data)
# Research-only: no exchange connection, no orders, no signals. See CROSS_EXCHANGE_PHASE_C_PREREGISTRATION_2026-10-25.md.
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.research.cross_exchange import phase_c as pc
from bot.research.cross_exchange.config import RecorderConfig
from bot.research.cross_exchange.gap_study import default_fees
from bot.research.cross_exchange.recorder import latest_session_id


class Command(BaseCommand):
    help = "RESEARCH-ONLY Phase C: preregistered test XARB-C1 (draft / sign / status / calibrate / evaluate)."

    def add_arguments(self, parser):
        g = parser.add_mutually_exclusive_group(required=True)
        g.add_argument("--draft", action="store_true")
        g.add_argument("--sign", metavar="NAME")
        g.add_argument("--status", action="store_true")
        g.add_argument("--calibrate", action="store_true")
        g.add_argument("--evaluate", action="store_true")
        parser.add_argument("--fee", action="append", default=[], metavar="VENUE=RATE",
                            help="with --draft: your real taker fee fraction per venue (default: base-tier table)")
        parser.add_argument("--out", default=None)

    def handle(self, *args, **o):
        out = o["out"] or str(Path(settings.BASE_DIR) / RecorderConfig().out_dir)
        self.stdout.write(self.style.WARNING("RESEARCH-ONLY · Phase C preregistered test · no orders · no signals"))
        try:
            if o["draft"]:
                fees, basis = default_fees()
                for item in o["fee"]:
                    v, _, r = item.partition("=")
                    fees[v.strip().lower()] = float(r)
                    basis[v.strip().lower()] = "OWNER-SUPPLIED account tier"
                reg = pc.Registration(fees={v: fees[v] for v in ("binance", "kraken", "coinbase")},
                                      fee_basis={v: basis.get(v, "") for v in ("binance", "kraken", "coinbase")})
                path = pc.write_draft(out, reg)
                self.stdout.write(self.style.SUCCESS(f"draft written: {path}  (digest {reg.digest()[:16]})"))
            elif o["sign"]:
                path = pc.owner_sign(out, o["sign"])
                self.stdout.write(self.style.SUCCESS(f"signed: {path} — only data recorded from now on counts"))
            elif o["status"]:
                reg, raw = pc.load_registration(out)
                self.stdout.write(json.dumps({k: raw.get(k) for k in ("prereg_id", "status", "registered_utc", "signed_by",
                                                                       "theta", "l_min", "n_hours", "fees")}, indent=1))
                if raw.get("status") == pc.SIGNED:
                    sessions, refused, hours, _ = pc.collect(out, reg, raw)
                    self.stdout.write(f"eligible post-registration hours: {len(hours)} / {reg.n_hours} "
                                      f"({len(sessions)} session(s) used, {len(refused)} refused)")
            elif o["calibrate"]:
                self._calibrate(out)
            elif o["evaluate"]:
                r = pc.evaluate(out)
                self.stdout.write(json.dumps({k: v for k, v in r.items() if k != "input_sha256"}, indent=1, default=str))
        except FileNotFoundError as e:
            raise CommandError(f"{e} — write a draft first with --draft")
        except (ValueError, PermissionError, FileExistsError) as e:
            raise CommandError(str(e))

    def _calibrate(self, out):
        try:
            reg, _ = pc.load_registration(out)
        except FileNotFoundError:
            fees, basis = default_fees()
            reg = pc.Registration(fees=fees, fee_basis=basis)
        self.stdout.write("false-pass rate at the null boundary (AR(1) hourly series, true mean = θ):")
        for rho in (0.0, 0.5, 0.8, 0.9):
            self.stdout.write(f"  ρ={rho:.1f}: {pc.false_pass_rate(reg, n_sims=2000, rho=rho):.3f}  (target ≤ 0.05)")
        z = pc.decide(np.zeros(reg.n_hours), reg)
        h = pc.decide(np.full(reg.n_hours, 3 * reg.theta), reg)
        self.stdout.write(f"known answers: all-zero hours → {z['verdict']} (expect FAIL); constant 3θ → {h['verdict']} (expect PASS)")
        sid = latest_session_id(out)
        if sid:
            hours, _ = pc.hourly_outcomes(out, sid, reg)
            counted = [x for x in hours if x["counts"]]
            ys = [x["y"] for x in counted]
            self.stdout.write(f"Phase B session {sid} as a KNOWN-ANSWER run (pre-registration data: NOT evidence, "
                              f"never evaluated): {len(counted)} eligible hours, mean Y = "
                              f"{(np.mean(ys) if ys else float('nan')):.6f}, max Y = {(max(ys) if ys else float('nan')):.6f}")
