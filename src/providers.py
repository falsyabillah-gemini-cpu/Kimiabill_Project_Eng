"""Pluggable multi-model provider router.

Supports:
- antigravity_local: Writes prompt handoffs & reads structured JSON produced by Antigravity.
- claude_code_cli:   Invokes local `claude -p` headless CLI for future Antigravity <-> Claude Code handoffs.
- gemini_api:        Calls Google Gemini REST API directly (with GEMINI_API_KEY).
- anthropic_api:     Calls Anthropic Messages API directly (with ANTHROPIC_API_KEY).
- openrouter:        Calls OpenRouter chat completions (with OPENROUTER_API_KEY).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Type, TypeVar

import requests
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


import re


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start == -1:
        raise ProviderError(f"No JSON object found in model response: {text[:300]}")
    decoder = json.JSONDecoder()
    for candidate in (text[start:], re.sub(r",\s*([}\]])", r"\1", text[start:])):
        try:
            obj, _ = decoder.raw_decode(candidate, 0)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    end = text.rfind("}")
    if end == -1 or end <= start:
        raise ProviderError(f"Invalid JSON object in model response: {text[:300]}")
    cleaned = re.sub(r",\s*([}\]])", r"\1", text[start : end + 1])
    return json.loads(cleaned)


DEFAULT_MODELS: dict[str, str] = {
    "gemini_api": "gemini-3.8-flash",
    "anthropic_api": "claude-sonnet-4-5",
    "openrouter": "google/gemini-3.8-flash",
    "claude_code_cli": "claude-sonnet-4-5",
}

# Ordered strictly from highest-intelligence free tier model down to lightweight fallbacks
GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_HYDROGEN_ALERT_SENT = False
# Permanent skip for models that return 402/403/404 (deprecated or paid-only on this API key).
_EXHAUSTED_MODELS_THIS_RUN: set[str] = set()
# Short cooldown map (seconds) for temporary RPM 429 / 503 / timeout bursts so working models recover automatically.
_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}


def _notify_hydrogen_alert(status_code: int, detail: str) -> None:
    """Send a one-time-per-run email alert ONLY if gemini-4-hydrogen requires billing/paid access (402/403)."""
    global _HYDROGEN_ALERT_SENT
    if _HYDROGEN_ALERT_SENT:
        return
    _HYDROGEN_ALERT_SENT = True
    raw_url = os.environ.get("GDRIVE_WEBHOOK_URL", "").strip()
    if not raw_url:
        return
    try:
        from src.tools.gdrive_sync import _normalize_webhook_url

        webhook_url = _normalize_webhook_url(raw_url)
        token = os.environ.get("GDRIVE_WEBHOOK_TOKEN", "").strip()
        notify_to = os.environ.get("NOTIFY_EMAIL_TO", "researchfalsyabillah@gmail.com").strip()
        msg = (
            "PEMBERITAHUAN PENTING — STATUS MODEL GEMINI 4 HYDROGEN\n\n"
            f"Model 'gemini-4-hydrogen' mengembalikan HTTP {status_code} pada GEMINI_API_KEY Anda.\n"
            "Kemungkinan penyebab: Gemini 4 Hydrogen kini memerlukan billing / akses terbatas.\n\n"
            "Tindakan Otomatis Sistem:\n"
            "- Sistem OTOMATIS beralih ke model Gemini Flash gratis tertinggi ('gemini-3.8-flash' -> fallback berjenjang) "
            "agar produksi video tetap berjalan normal.\n\n"
            "Keputusan Selanjutnya untuk Anda:\n"
            "1. Tidak perlu apa-apa — tetap pakai Gemini Flash tertinggi yang gratis, ATAU\n"
            "2. Aktifkan billing di Google AI Studio jika ingin kembali memakai gemini-4-hydrogen.\n\n"
            f"Detail teknis: {detail}"
        )
        requests.post(
            webhook_url,
            json={
                "action": "notify_email",
                "token": token,
                "to": notify_to,
                "episode_name": "alert_gemini_hydrogen",
                "title": "INFO: Gemini 4 Hydrogen Berbayar/Dibatasi — Otomatis Pakai Gemini Flash Tertinggi",
                "captions_preview": msg,
            },
            timeout=25,
        )
        print(f"[providers] Notifikasi status Gemini 4 Hydrogen terkirim ke {notify_to}")
    except Exception as exc:
        print(f"[providers] Gagal kirim notifikasi alert Hydrogen: {exc}")


def _post_with_retry(
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    timeout: int = 28,
    fail_fast_429: bool = False,
) -> requests.Response:
    """POST with bounded backoff on transient 5xx/network errors; fast-fail on 429 when fallbacks exist."""
    last_exc: Exception | None = None
    resp: requests.Response | None = None
    for attempt in range(max_attempts):
        try:
            resp = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if resp.status_code not in TRANSIENT_STATUS_CODES:
                return resp
            if resp.status_code == 429 and fail_fast_429:
                return resp
            wait_sec = min(10, 2 * (2 ** attempt))
            print(f"[providers] Transient HTTP {resp.status_code}, retrying in {wait_sec}s (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(wait_sec)
        except requests.RequestException as exc:
            last_exc = exc
            wait_sec = min(10, 2 * (2 ** attempt))
            print(f"[providers] Network error ({exc}), retrying in {wait_sec}s (attempt {attempt + 1}/{max_attempts})...")
            time.sleep(wait_sec)
    if resp is not None:
        return resp
    assert last_exc is not None
    raise last_exc


def _discover_gemini_flash_models(api_key: str) -> list[str]:
    """Query Gemini /v1beta/models to find available Flash models supporting generateContent (non-omni first)."""
    try:
        resp = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=25,
        )
        if resp.status_code != 200:
            return []
        models = resp.json().get("models", [])
        candidates: list[str] = []
        for m in models:
            methods = m.get("supportedGenerationMethods", [])
            name = m.get("name", "").removeprefix("models/")
            if "generateContent" in methods and name.startswith("gemini-") and "flash" in name:
                if not any(skip in name for skip in ("tts", "image", "audio", "live", "embedding")):
                    if name not in _EXHAUSTED_MODELS_THIS_RUN:
                        candidates.append(name)
        if candidates:
            # Prefer full numeric Flash generations (3.x/2.x) over experimental omni / lite / preview
            candidates.sort(
                key=lambda n: ("omni" not in n, "lite" not in n, "preview" not in n, n),
                reverse=True,
            )
            return candidates
    except Exception:
        return []
    return []


def _discover_gemini_flash_model(api_key: str) -> str | None:
    models = _discover_gemini_flash_models(api_key)
    return models[0] if models else None


def resolve_provider_and_model(provider: str, model: str) -> tuple[str, str]:
    """Resolve 'auto' provider based on available environment API keys."""
    if provider != "auto":
        return provider, model or DEFAULT_MODELS.get(provider, "")

    forced = os.environ.get("LLM_PROVIDER", "").strip()
    if forced and forced != "auto":
        return forced, DEFAULT_MODELS.get(forced, model)

    if os.environ.get("GEMINI_API_KEY"):
        env_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_model:
            return "gemini_api", env_model
        return "gemini_api", model if model.startswith("gemini") else DEFAULT_MODELS["gemini_api"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic_api", model if model.startswith("claude") else DEFAULT_MODELS["anthropic_api"]
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter", model if "/" in model else DEFAULT_MODELS["openrouter"]

    raise ProviderError(
        "No LLM API key found for provider='auto'. Set GEMINI_API_KEY, ANTHROPIC_API_KEY, "
        "or OPENROUTER_API_KEY in .env or GitHub Repository Secrets."
    )


def _save_handoff(result: BaseModel, handoff_path: Path | None) -> None:
    if handoff_path is not None:
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


_RUN_MODEL_USAGE: list[dict[str, Any]] = []


def reset_model_usage_log() -> None:
    """Clear recorded model telemetry (call at start of each episode attempt or weekly audit)."""
    _RUN_MODEL_USAGE.clear()


def _infer_agent_label(handoff_path: Path | None, system_prompt: str) -> str:
    if handoff_path is not None:
        stem = handoff_path.stem.lower()
        if stem.startswith("agent1_topic_map"):
            return "Agent 1 (Peta Topik Mingguan)"
        if stem.startswith("agent1"):
            return "Agent 1 (Ideasi Topik)"
        if stem.startswith("agent2"):
            return "Agent 2 (Auditor Hook)"
        if stem.startswith("agent3"):
            return "Agent 3 (Skrip & Storyboard)"
        if stem.startswith("agent5"):
            ver = stem.split("_v")[-1] if "_v" in stem else "1"
            return f"Agent 5 (QA Video v{ver})"
        if stem.startswith("agent7"):
            return "Agent 7 (Packaging & SEO)"
        if stem.startswith("agent8"):
            return "Agent 8 (Auditor Mingguan)"
    low = system_prompt.lower()
    if "visual safety" in low or "screener" in low:
        return "Agent 6 (Screening Klip)"
    return "Agent Lain"


def _record_model_usage(agent_label: str, requested_model: str, actual_model: str) -> None:
    _RUN_MODEL_USAGE.append(
        {
            "agent": agent_label,
            "requested": requested_model,
            "actual": actual_model,
            "fallback": actual_model != requested_model,
        }
    )


def get_model_usage_summary() -> dict[str, str]:
    """Return ordered {agent_label: 'actual_model (notes)'} summary for comparison & history."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _RUN_MODEL_USAGE:
        grouped.setdefault(row["agent"], []).append(row)

    out: dict[str, str] = {}
    for agent, rows in grouped.items():
        actuals = [r["actual"] for r in rows]
        last = rows[-1]
        if len(rows) > 1:
            unique_actuals = list(dict.fromkeys(actuals))
            models_str = " / ".join(unique_actuals)
            out[agent] = f"{models_str} ({len(rows)}x)"
        elif last["fallback"]:
            out[agent] = f"{last['actual']} (fallback dari {last['requested']})"
        else:
            out[agent] = last["actual"]
    return out


def format_model_usage_summary(header: str = "[MODEL AI YANG DIPAKAI]") -> str:
    """Format a clean human-readable model comparison block for email alerts."""
    summary = get_model_usage_summary()
    if not summary:
        return ""
    lines = [header]
    for agent, desc in summary.items():
        lines.append(f"- {agent}: {desc}")
    return "\n".join(lines)


def call_structured_agent(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    response_model: Type[T],
    handoff_path: Path | None = None,
    image_paths: list[Path] | None = None,
) -> T:
    """Route an agent step to the configured provider and validate against a Pydantic schema."""
    requested_model = model
    agent_label = _infer_agent_label(handoff_path, system_prompt)
    provider, model = resolve_provider_and_model(provider, model)
    requested_model = model or requested_model
    schema_hint = json.dumps(response_model.model_json_schema(), indent=2)
    prompt_body = (
        f"{system_prompt}\n\n"
        f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}\n\n"
        f"OUTPUT MUST BE VALID JSON MATCHING THIS SCHEMA (no extra markdown):\n{schema_hint}"
    )

    if provider == "antigravity_local":
        if handoff_path is None:
            raise ProviderError("antigravity_local mode requires handoff_path")
        if not handoff_path.exists():
            req_path = handoff_path.with_suffix(".prompt.txt")
            req_path.parent.mkdir(parents=True, exist_ok=True)
            req_path.write_text(prompt_body, encoding="utf-8")
            raise ProviderError(
                f"Handoff required for Antigravity agent. Prompt saved at {req_path}; "
                f"write validated JSON to {handoff_path}."
            )
        raw = json.loads(handoff_path.read_text(encoding="utf-8"))
        return response_model.model_validate(raw)

    if provider == "claude_code_cli":
        cmd = [
            "claude",
            "-p",
            prompt_body,
            "--output-format",
            "json",
        ]
        if model:
            cmd.extend(["--model", model])
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ProviderError(f"Claude Code CLI failed: {proc.stderr}")
        outer = json.loads(proc.stdout)
        text = outer.get("result", proc.stdout) if isinstance(outer, dict) else proc.stdout
        parsed = response_model.model_validate(_extract_json(text))
        _save_handoff(parsed, handoff_path)
        return parsed

    if provider == "gemini_api":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ProviderError("GEMINI_API_KEY is not set")
        user_parts: list[dict[str, Any]] = [
            {
                "text": (
                    f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                    f"Return JSON matching schema:\n{schema_hint}"
                )
            }
        ]
        for img_p in image_paths or []:
            if img_p.exists():
                mime = "image/png" if img_p.suffix.lower() == ".png" else "image/jpeg"
                user_parts.append(
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(img_p.read_bytes()).decode("ascii"),
                        }
                    }
                )
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": user_parts}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        last_parse_err: Exception | None = None
        for parse_try in range(2):
            tried: list[str] = []
            raw_candidates = [model, *[m for m in GEMINI_FALLBACK_MODELS if m != model]]
            non_perm = [m for m in raw_candidates if m not in _EXHAUSTED_MODELS_THIS_RUN] or raw_candidates
            now_mono = time.monotonic()
            candidates = [m for m in non_perm if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now_mono]
            if not candidates:
                earliest = min(_MODEL_COOLDOWN_UNTIL.get(m, now_mono) for m in non_perm)
                cool_wait = min(25.0, max(4.0, earliest - now_mono))
                print(f"[providers] Semua model Gemini sedang jeda RPM sesaat; menunggu {cool_wait:.0f}s agar kuota pulih...")
                time.sleep(cool_wait)
                candidates = non_perm

            resp = None
            active_model = model
            for cand_idx, cand_model in enumerate(candidates):
                tried.append(cand_model)
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{cand_model}:generateContent?key={api_key}"
                )
                has_more = cand_idx < len(candidates) - 1
                try:
                    resp = _post_with_retry(
                        url,
                        json_body=body,
                        max_attempts=1 if (has_more and cand_model in ("gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash")) else (2 if has_more else 3),
                        timeout=22 if has_more else 32,
                        fail_fast_429=has_more,
                    )
                except requests.RequestException as exc:
                    print(f"[providers] Gemini model '{cand_model}' timeout/network error ({exc}), trying next fallback...")
                    if has_more:
                        _MODEL_COOLDOWN_UNTIL[cand_model] = time.monotonic() + 45.0
                    resp = None
                    continue
                if resp.status_code == 200:
                    _MODEL_COOLDOWN_UNTIL.pop(cand_model, None)
                    active_model = cand_model
                    break
                if resp.status_code in (402, 403, 404) and has_more:
                    _EXHAUSTED_MODELS_THIS_RUN.add(cand_model)
                elif resp.status_code in (429, 503) and has_more:
                    _MODEL_COOLDOWN_UNTIL[cand_model] = time.monotonic() + 35.0
                if cand_model == "gemini-4-hydrogen" and resp.status_code in (402, 403):
                    _notify_hydrogen_alert(resp.status_code, resp.text[:400])
                if resp.status_code not in (402, 403, 404, *TRANSIENT_STATUS_CODES):
                    break
                label = "limit/quota" if resp.status_code == 429 else f"HTTP {resp.status_code}"
                print(f"[providers] Model '{cand_model}' ({label}), otomatis beralih ke fallback berikutnya...")
            if resp is None or resp.status_code in (404, *TRANSIENT_STATUS_CODES):
                for discovered in _discover_gemini_flash_models(api_key):
                    if discovered in tried or _MODEL_COOLDOWN_UNTIL.get(discovered, 0.0) > time.monotonic():
                        continue
                    url = (
                        f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{discovered}:generateContent?key={api_key}"
                    )
                    resp = _post_with_retry(url, json_body=body, max_attempts=2, fail_fast_429=True)
                    if resp.status_code == 200:
                        active_model = discovered
                        break
                    if resp.status_code in (429, 503):
                        _MODEL_COOLDOWN_UNTIL[discovered] = time.monotonic() + 35.0
                    elif resp.status_code in (402, 403, 404):
                        _EXHAUSTED_MODELS_THIS_RUN.add(discovered)
            assert resp is not None
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            try:
                parsed = response_model.model_validate(_extract_json(text))
                _record_model_usage(agent_label, requested_model, active_model)
                _save_handoff(parsed, handoff_path)
                return parsed
            except Exception as exc:
                last_parse_err = exc
                print(f"[providers] Gemini JSON/schema parse error (attempt {parse_try + 1}/2): {exc}")
                time.sleep(2)
        assert last_parse_err is not None
        raise last_parse_err

    if provider == "anthropic_api":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 2048,
                "system": system_prompt,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                            f"Return ONLY valid JSON matching schema:\n{schema_hint}"
                        ),
                    }
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        parsed = response_model.model_validate(_extract_json(text))
        _record_model_usage(agent_label, requested_model, model)
        _save_handoff(parsed, handoff_path)
        return parsed

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is not set")
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"INPUT_JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                            f"Return ONLY valid JSON matching schema:\n{schema_hint}"
                        ),
                    },
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        parsed = response_model.model_validate(_extract_json(text))
        _record_model_usage(agent_label, requested_model, model)
        _save_handoff(parsed, handoff_path)
        return parsed

    raise ProviderError(f"Unsupported provider: {provider}")
