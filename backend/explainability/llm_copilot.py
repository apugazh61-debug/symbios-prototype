"""
explainability/llm_copilot.py
-----------------------------
Enterprise LLM Copilot integrating Anthropic Claude and OpenAI.
Features strict error-trapping with automatic seamless fallback to deterministic templates.
"""

from typing import Dict, Any, Tuple
import os
from core.config import settings
from explainability.fallback_templates import generate_deterministic_explanation


def explain_decision(decision: Dict[str, Any], use_llm: bool = True) -> Tuple[str, str]:
    """
    Explains an industrial safety decision in plain language.
    Returns (explanation_text, source_identifier).
    """
    if not use_llm or not settings.ANTHROPIC_API_KEY:
        # Fast path: deterministic fallback
        return generate_deterministic_explanation(decision), "deterministic_template"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        prompt = f"""You are the Industrial Safety AI Copilot for SYMBIOS, a factory workforce and cobot orchestration platform.
A safety event just occurred on the manufacturing floor. Explain in 2 concise, professional sentences:
1. What was detected (worker fatigue level, posture, safety zone proximity).
2. Exactly what action the system took (e.g. reassigning work to a cobot, issuing a warning, or emergency stop) and why this protects the worker.

Event Telemetry:
- Action Taken: {decision.get('action')}
- Severity: {decision.get('severity')}
- Rule Fired: {decision.get('rule_fired')}
- Worker Fatigue Score: {decision.get('fatigue_score')}/100
- Inside Safety Zone: {decision.get('in_zone')} (Zone Name: {decision.get('zone_label') or decision.get('zone_name')})
- Cobot Reassigned: {decision.get('cobot_name') or 'N/A'}

Tone: Calm, authoritative, safety-focused, easy for both shop-floor workers and safety auditors to understand."""

        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=250,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}]
        )
        explanation = message.content[0].text.strip()
        return explanation, "llm_anthropic"

    except Exception as e:
        # Failure in LLM network/API must NEVER bring down safety reporting
        fallback_text = generate_deterministic_explanation(decision)
        return fallback_text, "fallback_after_llm_error"
