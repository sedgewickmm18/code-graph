"""LLM client helper for cgraph gen-spec.

Uses the openai package directly (OpenAI-compatible endpoint) so any local
server that speaks /v1/chat/completions works without extra env-var magic.
"""

import os

import openai

from .prompts import SPEC_GEN_SYSTEM


def generate_spec(
    context: str,
    capability_name: str,
    base_url: str = "",
    model: str = "",
    api_key: str = "",
) -> str:
    """Call an OpenAI-compatible endpoint and return a spec.md draft.

    Resolution order for *base_url*: (1) explicit argument, (2)
    ``CGRAPH_LLM_BASE_URL`` env var, (3) ``http://localhost:8080/v1``.

    Resolution order for *model*: (1) explicit argument, (2)
    ``CGRAPH_LLM_MODEL`` env var, (3) ``local``.

    Resolution order for *api_key*: (1) explicit argument, (2)
    ``CGRAPH_LLM_API_KEY`` env var, (3) ``local``.

    Args:
        context: Structured context block assembled by :func:`build_spec_context`.
        capability_name: Human-readable name of the capability being specified.
        base_url: OpenAI-compatible endpoint base URL (overrides env var).
        model: Model name (overrides env var).
        api_key: API key (overrides env var).

    Returns:
        The assistant message content (markdown spec draft).

    Raises:
        RuntimeError: If the LLM endpoint is not reachable.
    """
    resolved_base_url = base_url or os.getenv("CGRAPH_LLM_BASE_URL", "http://localhost:8080/v1")
    resolved_model = model or os.getenv("CGRAPH_LLM_MODEL", "local")
    resolved_api_key = api_key or os.getenv("CGRAPH_LLM_API_KEY", "local")

    client = openai.OpenAI(base_url=resolved_base_url, api_key=resolved_api_key)

    try:
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": SPEC_GEN_SYSTEM},
                {"role": "user", "content": context},
            ],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to reach LLM endpoint at {resolved_base_url}: {exc}"
        ) from exc

    return response.choices[0].message.content or ""
