"""
Provider-agnostic LLM layer over any OpenAI-compatible chat API.

Pick a provider with env LLM_PROVIDER (default: deepseek) and supply its key:

  deepseek  DEEPSEEK_API_KEY   (default; cheapest — a full report costs a few cents)
  openai    OPENAI_API_KEY
  custom    LLM_API_KEY + LLM_BASE_URL + LLM_MODEL   (Qwen/DashScope, Kimi, Zhipu, OpenRouter,
            a local server … anything that speaks the OpenAI chat-completions API)

Optional for any provider: LLM_MODEL overrides the model name.
Keys are read from env only — never hard-code them or paste them into chat.

chat_json() returns parsed JSON with defensive extraction + retries, because not every
provider enforces JSON mode strictly (and some don't support response_format at all).
"""
from __future__ import annotations
import json, os, re, time
from openai import OpenAI

PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").lower()

# provider: (key env, base url, default model)
_PRESETS = {
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com", "deepseek-chat"),
    "openai":   ("OPENAI_API_KEY",   None,                      "gpt-4o-mini"),
    "custom":   ("LLM_API_KEY",      None,                      None),
}


def _cfg():
    if PROVIDER not in _PRESETS:
        raise SystemExit(f"未知 LLM_PROVIDER={PROVIDER}（可选: {', '.join(_PRESETS)}）")
    key_env, base, model = _PRESETS[PROVIDER]
    if PROVIDER == "custom":
        base = os.environ.get("LLM_BASE_URL")
        if not base:
            raise SystemExit("LLM_PROVIDER=custom 需要设置 LLM_BASE_URL（OpenAI 兼容接口地址）")
    model = os.environ.get("LLM_MODEL") or model
    if not model:
        raise SystemExit("LLM_PROVIDER=custom 需要设置 LLM_MODEL（模型名）")
    return key_env, base, model


def check() -> str:
    """Raise SystemExit with a friendly message if the provider isn't configured."""
    key_env, base, model = _cfg()
    if not os.environ.get(key_env):
        raise SystemExit(f"缺少 {key_env}。请在终端设置: export {key_env}=...（不要贴到聊天里）")
    return f"{PROVIDER} · {model}"


def _client() -> OpenAI:
    key_env, base, _ = _cfg()
    key = os.environ.get(key_env)
    if not key:
        raise SystemExit(f"缺少 {key_env}。请在终端设置: export {key_env}=...")
    return OpenAI(api_key=key, base_url=base) if base else OpenAI(api_key=key)


def fast_model() -> str:
    return _cfg()[2]


smart_model = fast_model  # kept for compatibility; every step uses the same JSON-capable model


def _extract_json(s: str):
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        for a, b in (("{", "}"), ("[", "]")):  # last resort: outermost { } or [ ]
            i, j = s.find(a), s.rfind(b)
            if i != -1 and j != -1:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    pass
        raise


_JSON_MODE = True  # flipped off once if the provider rejects response_format


def chat_json(messages, model=None, max_tokens=3000, retries=6, temperature=0.2):
    global _JSON_MODE
    cli = _client()
    model = model or fast_model()
    last = None
    for i in range(retries):
        try:
            kw = {"response_format": {"type": "json_object"}} if _JSON_MODE else {}
            r = cli.chat.completions.create(model=model, messages=messages,
                                            max_tokens=max_tokens, temperature=temperature, **kw)
            return _extract_json(r.choices[0].message.content), r.usage
        except Exception as e:
            last = e
            if _JSON_MODE and "response_format" in str(e):
                _JSON_MODE = False  # provider doesn't support JSON mode — rely on the prompt
                continue
            time.sleep(min(30, 2 ** (i + 1)))  # 2,4,8,16,30s — rides out network blips / 429s
    raise last
