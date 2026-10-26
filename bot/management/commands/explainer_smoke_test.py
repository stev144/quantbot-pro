# claude code changed: 2026-10-26 — new file. ONE minimal live Claude API call to prove the key, credit and model work.
# Sends a fixed generic sentence — no research data, no user data — and prints status, model, tokens and an estimated
# cost. Refuses to run without --confirm (it spends real credit, roughly a hundredth of a US cent on Haiku).
#   venv\Scripts\python.exe manage.py explainer_smoke_test --confirm
from django.conf import settings
from django.core.management.base import BaseCommand

from bot.academy.explainer.service import estimate_cost_usd
from bot.ai.client import Declined, api_key, check_stop, classify_api_error, redact, usage_of

PROMPT = "This is a connection test. Reply with exactly the word: ok"


class Command(BaseCommand):
    help = "One minimal live Claude API call (needs --confirm; spends a tiny amount of credit)."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true", help="really send the request")
        parser.add_argument("--mode", choices=("beginner", "research"), default="beginner",
                            help="which mode's model to test (claude code changed: 2026-10-26 — per-mode models)")

    def handle(self, *args, **o):
        from bot.academy.explainer.service import model_for
        model = model_for(o["mode"])
        key = api_key()
        self.stdout.write(f"model: {model}")
        self.stdout.write(f"API key configured: {'yes' if key else 'NO'}"
                          + (f" (starts with {key[:7]}…, {len(key)} characters)" if key else ""))
        if not key:
            self.stdout.write(self.style.ERROR("Add ANTHROPIC_API_KEY to .env first (see .env.example)."))
            return
        if not o["confirm"]:
            self.stdout.write(self.style.WARNING("Not sent. Re-run with --confirm to make the one live call."))
            return
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=0)
        try:
            resp = client.messages.create(model=model, max_tokens=300, output_config={"effort": "low"},
                                          messages=[{"role": "user", "content": PROMPT}])
            check_stop(resp)
        except Declined as exc:
            self.stdout.write(self.style.ERROR(f"DECLINED ({exc})"))
            return
        except Exception as exc:
            info = classify_api_error(exc)
            self.stdout.write(self.style.ERROR(f"{info['status']}: {redact(info['message'])}"))
            return
        usage = usage_of(resp)
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "").strip()
        self.stdout.write(self.style.SUCCESS(f"OK — reply: {redact(text)[:80]!r}"))
        self.stdout.write(f"tokens: {usage.get('input_tokens')} in / {usage.get('output_tokens')} out (thinking included)")
        cost = estimate_cost_usd(model, usage)
        self.stdout.write(f"estimated cost: ${cost} (estimate from published prices; the Console shows the real charge)"
                          if cost is not None else "estimated cost: no price on file for this model")
        self.stdout.write(f"request id: {getattr(resp, '_request_id', '?')}")
