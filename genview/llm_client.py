# genview/llm_client.py
"""HTTP client for the Phase-2 tree-query parser (Ollama or a /parse wrapper)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, TypedDict

import requests
from dotenv import load_dotenv

from .tree_query_capabilities import PLAN_TEMPLATE, build_parse_system_prompt

logger = logging.getLogger(__name__)
load_dotenv()

REQUEST_TIMEOUT = (2.0, 60.0)
_LOG_BODY_MAX_LEN = 400


class LlmParseResult(TypedDict):
    plan: dict[str, Any] | None
    raw: str
    error: str | None


def llm_parser_enabled() -> bool:
    url = (os.getenv("TREE_QUERY_LLM_URL", "http://localhost:11434") or "").strip().lower()
    return url not in ("", "off", "disabled", "none", "false", "0")


def _truncate_for_log(text: str, max_len: int = _LOG_BODY_MAX_LEN) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


def _config() -> dict[str, str]:
    return {
        "url": (os.getenv("TREE_QUERY_LLM_URL", "http://localhost:11434") or "").rstrip("/"),
        "model": os.getenv("TREE_QUERY_LLM_MODEL", "llama3.2:3b"),
        "api_key": os.getenv("TREE_QUERY_LLM_API_KEY", "").strip(),
    }


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output, including fenced markdown."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        inner = [line for line in lines if not line.strip().startswith("```")]
        raw = "\n".join(inner).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


def _plan_from_payload(payload: Any) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (plan, raw_text, error)."""
    if isinstance(payload, dict) and payload.get("success") is False:
        err = str(payload.get("message") or payload.get("detail") or payload.get("error") or "LLM-Fehler")
        return None, "", err
    if isinstance(payload, dict) and payload.get("error") and not payload.get("plan") and "intent" not in payload:
        return None, "", str(payload["error"])

    raw = ""
    plan: dict[str, Any] | None = None

    if isinstance(payload, dict):
        if isinstance(payload.get("plan"), dict):
            plan = payload["plan"]
            raw = json.dumps(plan, ensure_ascii=False)
        elif "intent" in payload:
            plan = payload
            raw = json.dumps(plan, ensure_ascii=False)
        else:
            message = payload.get("message")
            if isinstance(message, dict):
                raw = str(message.get("content") or "")
            elif payload.get("response"):
                raw = str(payload.get("response") or "")
            plan = extract_json_object(raw)
    elif isinstance(payload, str):
        raw = payload
        plan = extract_json_object(raw)

    if plan is None:
        return None, raw, "Das Sprachmodell lieferte keinen gültigen JSON-Plan."
    return plan, raw, None


def parse_question_via_llm(
    question: str, draft: dict[str, Any] | None = None
) -> LlmParseResult:
    """
    Ask the configured LLM to turn *question* into a tree-query plan.

    Supports:
    - Ollama chat API at TREE_QUERY_LLM_URL (default http://localhost:11434)
    - A wrapper that accepts POST {question} on a URL ending with /parse
    """
    result: LlmParseResult = {"plan": None, "raw": "", "error": None}
    question = (question or "").strip()
    if not question:
        result["error"] = "Keine Frage angegeben."
        return result
    if not llm_parser_enabled():
        result["error"] = "Sprachmodell-Parser ist deaktiviert."
        return result

    cfg = _config()
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["X-API-Key"] = cfg["api_key"]
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    if cfg["url"].endswith("/parse"):
        url = cfg["url"]
        body: dict[str, Any] = {
            "question": question,
            "template": PLAN_TEMPLATE,
            "capabilities_prompt": build_parse_system_prompt(),
        }
        if draft:
            body["draft"] = draft
    else:
        url = f"{cfg['url']}/api/chat"
        user_content = question
        if draft:
            user_content = (
                f"Question:\n{question}\n\n"
                "Draft plan from heuristics (may be wrong; fill/correct the template):\n"
                f"{json.dumps(draft, ensure_ascii=False)}"
            )
        body = {
            "model": cfg["model"],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.0
            },
            "messages": [
                {"role": "system", "content": build_parse_system_prompt()},
                {"role": "user", "content": user_content},
            ],
        }

    try:
        response = requests.post(
            url, json=body, headers=headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except json.JSONDecodeError:
            result["error"] = "Ungültige JSON-Antwort vom Sprachmodell."
            logger.error("%s Body: %s", result["error"], _truncate_for_log(response.text))
            return result

        plan, raw, error = _plan_from_payload(payload)
        result["raw"] = raw
        result["plan"] = plan
        result["error"] = error
        return result

    except requests.exceptions.Timeout:
        result["error"] = "Zeitüberschreitung beim Sprachmodell."
        logger.error(result["error"])
        return result
    except requests.exceptions.ConnectionError:
        result["error"] = "Sprachmodell nicht erreichbar. Ist Ollama gestartet?"
        logger.error(result["error"])
        return result
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        result["error"] = f"Sprachmodell-HTTP-Fehler ({status})."
        logger.error("%s %s", result["error"], _truncate_for_log(getattr(exc.response, "text", "") or ""))
        return result
    except requests.exceptions.RequestException as exc:
        result["error"] = "Anfrage an das Sprachmodell fehlgeschlagen."
        logger.error("%s (%s)", result["error"], exc)
        return result
