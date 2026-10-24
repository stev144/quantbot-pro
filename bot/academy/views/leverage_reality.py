# claude code changed: new file — 2026-10-22, Leverage Reality Phase 2. Public educational page (no @login_required,
# like path_list — no per-user data). The calculator is a deterministic, cheap computation on the visitor's own
# GET inputs; the historical tables are READ from the persisted summary (manage.py run_leverage_reality) and never
# recomputed here. No exchange call, no order, no recommendation.
from django.shortcuts import render

from bot.research import leverage_reality as lr

DEFAULTS = {"equity": "1000", "entry": "100", "leverage": "20", "steps": "100, 200, 300, 400", "spacing": "1",
            "direction": lr.LONG, "mode": lr.CROSS, "maintenance": "0.5", "fee": "0.05"}


def _parse(params) -> tuple:
    """(ScenarioInput or None, cleaned form values, error message or '')."""
    form = {k: (params.get(k) or v).strip() for k, v in DEFAULTS.items()}
    try:
        steps = tuple(float(x) for x in form["steps"].replace(";", ",").split(",") if x.strip())
        inp = lr.ScenarioInput(
            account_equity=float(form["equity"]), entry_price=float(form["entry"]), leverage=float(form["leverage"]),
            step_margins=steps, step_spacing_pct=float(form["spacing"]), direction=form["direction"].upper(),
            margin_mode=form["mode"].upper(), maintenance_rate=float(form["maintenance"]) / 100.0,
            taker_fee_rate=float(form["fee"]) / 100.0)
        inp.validate()
        return inp, form, ""
    except ValueError as e:                      # ScenarioError is a ValueError; so is a bad float
        msg = str(e) if isinstance(e, lr.ScenarioError) else "every field must be a number (steps: comma-separated amounts)"
        return None, form, msg


def leverage_reality(request):
    inp, form, error = _parse(request.GET)
    scenario = None
    if inp is not None:
        try:
            scenario = lr.ladder_scenario(inp)
        except lr.ScenarioError as e:
            error = str(e)
    return render(request, "academy/leverage_reality.html", {
        "active": "academy", "form": form, "error": error, "scenario": scenario,
        "historical": lr.load_historical(), "max_leverage": lr.MAX_LEVERAGE, "max_steps": lr.MAX_STEPS,
        "command": "python manage.py run_leverage_reality",
    })
