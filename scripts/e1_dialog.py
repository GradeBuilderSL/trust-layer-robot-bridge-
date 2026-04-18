#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


BRIDGE_URL = os.environ.get("E1_BRIDGE_URL", "http://127.0.0.1:8083")
CHAT_URL = os.environ.get("TRUST_LAYER_CHAT_URL", "http://127.0.0.1:8080/chat")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
SYSTEM_PROMPT = os.environ.get(
    "E1_DIALOG_SYSTEM",
    (
        "Ты дружелюбный русскоязычный голос робота E1. "
        "Отвечай коротко, естественно и вслух-пригодно. "
        "Не используй списки, markdown и длинные абзацы."
    ),
)


def post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def speak(text: str) -> None:
    result = post_json(f"{BRIDGE_URL}/api/audio/speak", {"text": text, "lang": "ru"})
    method = result.get("method", "unknown")
    print(f"[speak:{method}] {text}")


def fallback_reply(user_text: str) -> str:
    text = user_text.strip().lower()
    if not text:
        return "Я не расслышал фразу."
    if "привет" in text:
        return "Привет. Я робот E1. Рад общению."
    if "как тебя зовут" in text:
        return "Я робот E1."
    if "что ты умеешь" in text:
        return "Сейчас я умею поддерживать диалог и говорить вслух через Jetson."
    if "пока" in text or "до свидания" in text:
        return "До свидания."
    return f"Я услышал: {user_text}"


def bridge_chat_reply(user_text: str) -> str:
    try:
        result = post_json(CHAT_URL, {"message": user_text})
    except Exception as exc:
        print(f"[bridge-chat:error] {exc}", file=sys.stderr)
        return ""

    for key in ("reply", "answer", "text", "message"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def llm_reply(user_text: str) -> str:
    via_bridge = bridge_chat_reply(user_text)
    if via_bridge:
        return via_bridge

    if not OPENAI_API_KEY:
        return fallback_reply(user_text)

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"[openai:http_error] {details}", file=sys.stderr)
        return fallback_reply(user_text)
    except Exception as exc:
        print(f"[openai:error] {exc}", file=sys.stderr)
        return fallback_reply(user_text)

    text = data.get("output_text", "").strip()
    if text:
        return text
    return fallback_reply(user_text)


def main() -> int:
    print("E1 dialog mode")
    print(f"bridge: {BRIDGE_URL}")
    print(f"chat:   {CHAT_URL}")
    print("Введите фразу и нажмите Enter. Команды: /quit для выхода, /say <текст> для прямой озвучки.")

    try:
        speak("Диалоговый режим запущен.")
    except Exception as exc:
        print(f"[bridge:error] {exc}", file=sys.stderr)

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_text:
            continue
        if user_text in {"/quit", "/exit"}:
            break
        if user_text.startswith("/say "):
            speak(user_text[5:].strip())
            continue

        reply = llm_reply(user_text)
        speak(reply)

    try:
        speak("Диалоговый режим завершен.")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
