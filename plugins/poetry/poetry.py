import os
import sys
from pathlib import Path

import requests
import toml

SYSTEM_PROMPT = (
    "You are a poet. Given the user's input, reply with a short poem of at "
    "most 3 lines. Output only the poem itself: no title, no preamble, no "
    "explanation, no quotation marks."
)


def load_config():
    here = os.path.dirname(os.path.abspath(__file__))
    return toml.load(here + "/config.toml")["poetry_plugin"]


def read_secret(secret_path):
    path = Path(secret_path).expanduser()
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        print(f"Error: secret file not found {path}", file=sys.stderr)
        sys.exit(1)


def resolve_route(cfg):
    """Pick the provider and return (base_url, model, headers)."""
    provider = cfg.get("provider", "ollama").lower()
    if provider not in cfg:
        print(f"Error: no [{provider}] config section for selected provider", file=sys.stderr)
        sys.exit(1)

    route = cfg[provider]
    headers = {"Content-Type": "application/json"}

    if provider == "openrouter":
        api_key = read_secret(route["secret_path"])
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "ollama":
        # Local model, no auth. base_url defaults to the Ollama OpenAI-compatible port.
        pass
    else:
        print(f"Error: unsupported provider '{provider}' (use 'openrouter' or 'ollama')", file=sys.stderr)
        sys.exit(1)

    base_url = route.get("base_url")
    model = route.get("model")
    if not base_url or not model:
        print(f"Error: [{provider}] needs both base_url and model", file=sys.stderr)
        sys.exit(1)

    return base_url.rstrip("/"), model, headers


def write_poem(topic):
    cfg = load_config()
    base_url, model, headers = resolve_route(cfg)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": topic},
        ],
        "temperature": cfg.get("temperature", 0.8),
    }

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"API returns error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        print(f"Error: unexpected response shape: {data}", file=sys.stderr)
        sys.exit(1)

    # Enforce the "up to 3 lines" contract deterministically, regardless of the model.
    lines = [line for line in text.splitlines() if line.strip()][:3]
    print("\n".join(lines))


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else ""
    if not topic:
        print("Error: no input provided", file=sys.stderr)
        sys.exit(1)
    write_poem(topic)