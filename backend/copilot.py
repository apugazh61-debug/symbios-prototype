"""
copilot.py
----------
Turns a machine decision (from decision.py) into a plain-language
explanation a worker can read or hear. This is Module 03 on the
architecture slide — the "trust layer".

Requires an Anthropic API key set as an environment variable:
    export ANTHROPIC_API_KEY="sk-ant-..."

Get a key at https://console.anthropic.com
"""

import os
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

REASON_TEXT = {
    "high_fatigue": "the worker's fatigue score crossed the high-risk threshold",
    "moderate_fatigue": "the worker's fatigue score is moderately elevated",
    "in_danger_zone": "the worker is currently positioned inside a high-risk zone",
    "nominal": "no risk factors are currently active",
}

_client = None


def _get_client():
    """
    Created lazily, on first real use, instead of at import time.
    This means a missing ANTHROPIC_API_KEY or an SDK/httpx version
    mismatch only breaks the /explain endpoint (which falls back to
    the offline explanation) instead of crashing the entire server
    on startup.
    """
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic(api_key=api_key)
    return _client


def explain_decision(decision_dict: dict) -> str:
    """
    decision_dict: output of decision.decision_to_dict()
    Returns a short natural-language explanation string.
    """
    reasons = ", ".join(
        REASON_TEXT.get(tag, tag) for tag in decision_dict.get("reason_tags", [])
    )

    prompt = f"""You are the on-floor safety copilot for a factory worker.
Explain the following automated decision in 2-3 short, plain-language
sentences. Be calm, direct, and reassuring — not alarming. Do not use
jargon like "RL agent" or "fatigue vector". Speak to the worker directly.

Decision: {decision_dict['action']}
Severity: {decision_dict['severity']}
Fatigue score: {decision_dict['fatigue_score']} / 100
Currently in a marked zone: {decision_dict['in_zone']} ({decision_dict.get('zone_label')})
Underlying reasons: {reasons}
"""

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


def explain_decision_offline(decision_dict: dict) -> str:
    """
    Fallback used automatically if no API key is set, so the demo
    still works without network/API access (e.g. on a slow venue wifi).
    """
    action_text = {
        "REASSIGN_TO_COBOT": "Your current task is being handed to the cobot for now.",
        "MONITOR": "You're being monitored a little more closely right now.",
        "ZONE_ALERT": "You've entered a marked high-risk zone.",
        "NORMAL": "Everything looks normal — no action needed.",
    }.get(decision_dict["action"], "")

    reasons = ", ".join(
        REASON_TEXT.get(tag, tag) for tag in decision_dict.get("reason_tags", [])
    )
    return f"{action_text} This is because {reasons}."
