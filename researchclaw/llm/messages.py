"""Provider-neutral chat message normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass


JSON_MODE_INSTRUCTION = (
    "You MUST respond with valid JSON only. "
    "Do not include any text outside the JSON object."
)


@dataclass(frozen=True)
class ProviderMessages:
    """Messages split into provider-level system text and chat turns."""

    system: str | None
    messages: list[dict[str, str]]


def normalize_provider_messages(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    default_user_content: str = "Hello.",
    leading_user_content: str = "Continue.",
) -> ProviderMessages:
    """Split system messages and normalize turns for native providers.

    Anthropic and Gemini both require system instructions outside the main
    content list, merged consecutive turns, and a user turn before assistant
    content. Keeping that behavior here avoids provider drift.
    """

    system_parts: list[str] = []
    non_system: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user") or "user")
        content = str(message.get("content", "") or "")
        if role == "system":
            system_parts.append(content)
        else:
            non_system.append({"role": role, "content": content})

    system = "\n\n".join(system_parts) if system_parts else None
    if json_mode:
        system = f"{JSON_MODE_INSTRUCTION}\n\n{system}" if system else JSON_MODE_INSTRUCTION

    merged = merge_consecutive_messages(non_system)
    if not merged:
        merged = [{"role": "user", "content": default_user_content}]
    elif merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": leading_user_content})

    return ProviderMessages(system=system, messages=merged)


def merge_consecutive_messages(
    messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge adjacent chat turns with the same role."""

    merged: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user") or "user")
        content = str(message.get("content", "") or "")
        if merged and merged[-1]["role"] == role:
            merged[-1] = {
                "role": role,
                "content": merged[-1]["content"] + "\n\n" + content,
            }
        else:
            merged.append({"role": role, "content": content})
    return merged


def build_gemini_contents(messages: list[dict[str, str]]) -> list[dict[str, object]]:
    """Convert normalized provider messages to Gemini contents."""

    normalized = normalize_provider_messages(messages)
    contents: list[dict[str, object]] = []
    for message in normalized.messages:
        role = "user" if message["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    return contents
