"""gemini_client.py — minimal Google Gemini chat adapter (I/O, stdlib-only).

Powers the conversational "Ask Wellington" chat when a ``GEMINI_API_KEY`` is
configured. It is deliberately dependency-free (uses ``urllib.request``) so the
project keeps its lightweight footprint, and it is bounded by a short timeout so
a slow/unreachable API degrades gracefully to the deterministic fallback rather
than hanging the request.

Grounding / anti-fabrication: the caller passes a compact, factual snapshot of
the real chapter data (computed stats + a member table) as ``context``. The
system instruction tells the model to answer *only* from that context and to say
it doesn't know rather than invent figures — so the conversational layer never
fabricates member data. The deterministic engine remains the source of truth for
the numbers; Gemini is the natural-language presenter.

Public surface:
    is_configured() -> bool
    generate_reply(question, context, history, *, timeout) -> str

``generate_reply`` raises :class:`GeminiError` on any failure (missing key,
network error, non-200, malformed/blocked response) so the route can catch it
and fall back deterministically.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

# Default model + endpoint. gemini-1.5-flash is fast and cheap, well-suited to a
# grounded Q&A turn. Overridable via env for other deployments.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

_SYSTEM_INSTRUCTION = (
    "You are Wellington the Wise, a friendly, encouraging owl mascot and "
    "assistant for a JCI (Junior Chamber International) chapter's Member Growth "
    "Tracker. You help a chapter administrator understand their members.\n\n"
    "RULES:\n"
    "1. Answer ONLY using the CHAPTER DATA provided below. Never invent members, "
    "numbers, badges, or events.\n"
    "2. If the answer is not in the data, say you don't have that information "
    "and suggest what you can answer (member counts, health scores, at-risk "
    "members, stage breakdowns, attendance, aging-out, badges, and engagement "
    "points/tiers).\n"
    "3. Be concise and conversational — 1-3 sentences. You may use light owl "
    "personality and an emoji occasionally, but stay professional.\n"
    "4. When citing numbers, use exactly the figures in the data.\n"
)


class GeminiError(RuntimeError):
    """Raised on any failure so the caller can fall back deterministically."""


def is_configured() -> bool:
    """Return whether a Gemini API key is present in the environment."""
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def _build_contents(
    question: str,
    context: str,
    history: List[Tuple[str, str]],
) -> List[dict]:
    """Assemble the Gemini ``contents`` array from history + the current turn.

    ``history`` is a list of ``(role, text)`` where role is ``"user"`` or
    ``"model"``. The current question is appended as the final user turn, with
    the grounding context prepended to it so the model always sees the latest
    data.
    """
    contents: List[dict] = []
    for role, text in history:
        norm_role = "model" if role in ("model", "assistant", "wellington") else "user"
        contents.append({"role": norm_role, "parts": [{"text": text}]})

    user_turn = (
        f"CHAPTER DATA (the only facts you may use):\n{context}\n\n"
        f"QUESTION: {question}"
    )
    contents.append({"role": "user", "parts": [{"text": user_turn}]})
    return contents


def generate_reply(
    question: str,
    context: str,
    history: Optional[List[Tuple[str, str]]] = None,
    *,
    timeout: float = 12.0,
) -> str:
    """Ask Gemini a grounded question and return its text reply.

    Raises :class:`GeminiError` on a missing key, network/HTTP failure, timeout,
    or a blocked/empty response, so the route can fall back to the deterministic
    engine.
    """
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("GEMINI_API_KEY is not configured")

    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
        "contents": _build_contents(question, context, history or []),
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 400,
        },
    }

    url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=key)
    data = json.dumps(payload).encode("utf-8")

    # Transient errors (503 overloaded, 429 rate-limited) are retried briefly
    # with backoff before giving up and letting the caller fall back.
    import time

    last_error: Optional[GeminiError] = None
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            break  # success
        except urllib.error.HTTPError as exc:  # non-2xx
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:  # pragma: no cover - best-effort detail
                pass
            last_error = GeminiError(f"Gemini HTTP {exc.code}: {detail}")
            # Retry only transient capacity errors.
            if exc.code in (429, 500, 503) and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = GeminiError(f"Gemini request failed: {exc}")
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_error from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise last_error or GeminiError("Gemini request failed")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini returned malformed JSON") from exc

    # Extract the first candidate's text. A blocked prompt has no candidates.
    candidates = parsed.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini returned no candidates (possibly blocked)")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise GeminiError("Gemini returned an empty reply")
    return text


def generate_text(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.6,
    max_output_tokens: int = 600,
    timeout: float = 12.0,
) -> str:
    """Run a single-shot text generation with a custom system instruction.

    Unlike :func:`generate_reply` (which forces the Wellington Q&A persona and a
    1-3 sentence answer), this is a general-purpose helper for tasks such as
    drafting an outreach email. Raises :class:`GeminiError` on any failure so the
    caller can fall back deterministically.
    """
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("GEMINI_API_KEY is not configured")

    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    url = GEMINI_ENDPOINT.format(model=GEMINI_MODEL, key=key)
    data = json.dumps(payload).encode("utf-8")

    import time

    last_error: Optional[GeminiError] = None
    body = ""
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:  # pragma: no cover
                pass
            last_error = GeminiError(f"Gemini HTTP {exc.code}: {detail}")
            if exc.code in (429, 500, 503) and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = GeminiError(f"Gemini request failed: {exc}")
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise last_error from exc
    else:  # pragma: no cover
        raise last_error or GeminiError("Gemini request failed")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GeminiError("Gemini returned malformed JSON") from exc

    candidates = parsed.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini returned no candidates (possibly blocked)")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise GeminiError("Gemini returned an empty reply")
    return text


__all__ = [
    "GeminiError",
    "is_configured",
    "generate_reply",
    "generate_text",
    "GEMINI_MODEL",
]
