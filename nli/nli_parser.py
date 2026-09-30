"""Offline by default; optional Gemini classifies into the same finite operations."""
import json
import os

from nli.offline_fallback import parse_offline, unknown
from nli.safety_validator import OPERATIONS, make_proposal


def parse_user_input(user_input: str, *, offline: bool = True, cwd=None):
    local = parse_offline(user_input, cwd)
    if offline or local.action != "unknown":
        return local
    # Reject explicit shell/compound syntax before any optional classification.
    if type(user_input) is not str or len(user_input) > 2000 or any(
            char in user_input for char in ";|&`$><\n\r"):
        return unknown()
    key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_MODEL")
    if not key or not model:
        return unknown("Online mode requires GEMINI_API_KEY and GEMINI_MODEL environment variables.")
    try:
        from google import genai
        from google.genai import types
        prompt = (
            "Classify a single read-only request into exactly one supported action. "
            "Return only JSON with one key, action. Unknown, compound, write, root, "
            "service, script, or process-changing requests must use unknown. "
            "Never generate code or commands. Actions: " + json.dumps(OPERATIONS)
        )
        with genai.Client(api_key=key) as client:
            response = client.models.generate_content(
                model=model, contents=user_input,
                config=types.GenerateContentConfig(system_instruction=prompt,
                                                   response_mime_type="application/json"))
        data = json.loads(response.text)
        if (type(data) is not dict or set(data) != {"action"}
                or type(data["action"]) is not str or data["action"] not in OPERATIONS):
            return unknown("Online classification did not identify a supported operation.")
        return make_proposal(data["action"], cwd)
    except Exception:
        # Provider exceptions can contain request headers or credentials.
        return unknown("Online classification unavailable; use a supported offline request.")
