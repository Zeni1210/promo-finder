"""One tiny, dependency-free client for every model provider.

Swap models with a flag, not code:
    --provider gemini     --model gemini-2.5-flash          (GEMINI_API_KEY; free tier - model ids change, check AI Studio)
    --provider openai     --model gpt-4o-mini               (OPENAI_API_KEY; any OpenAI-compatible
                          --base-url https://api.groq.com/openai/v1   -> Groq / OpenRouter / Together /
                          --base-url http://localhost:11434/v1        -> Ollama (local, free)
    --provider anthropic  --model claude-sonnet-4-5          (ANTHROPIC_API_KEY)
    --provider none       rules only, no network

Every call asks for JSON, is retried with backoff on 429/5xx, paced to an RPM
limit (free tiers are rate-limited, not token-limited), and cached on disk by a
hash of (provider, model, prompt) so re-runs are free and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "openai": "gpt-4o-mini",
                  "anthropic": "claude-sonnet-4-5", "ollama": "llama3.1"}


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider="none", model=None, base_url=None, api_key=None, rpm=10,
                 cache_dir=".cache/llm", temperature=0.2, timeout=120, log=print):
        self.provider = (provider or "none").lower()
        self.model = model or DEFAULT_MODELS.get(self.provider, "")
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout
        self.min_gap = 60.0 / rpm if rpm else 0
        self._last = 0.0
        self.cache_dir = cache_dir
        self.log = log
        self.calls = 0
        self.cached = 0
        self.tokens_in = 0
        self.tokens_out = 0
        env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
               "ollama": "OLLAMA_API_KEY"}.get(self.provider)
        self.api_key = api_key or (os.environ.get(env) if env else None)
        if self.provider == "ollama":
            self.base_url = self.base_url or "http://localhost:11434/v1"
            self.api_key = self.api_key or "ollama"
        if self.enabled and not self.api_key:
            raise LLMError(f"provider '{self.provider}' needs an API key (set {env})")

    @property
    def enabled(self):
        return self.provider not in ("none", "", "rules")

    def describe(self):
        return f"{self.provider}:{self.model}" + (f" @ {self.base_url}" if self.base_url else "")

    # ------------------------------------------------------------------
    def json(self, system: str, user: str) -> dict:
        key = hashlib.sha256(json.dumps([self.provider, self.model, system, user]).encode()).hexdigest()[:24]
        path = os.path.join(self.cache_dir, f"{key}.json") if self.cache_dir else None
        if path and os.path.exists(path):
            self.cached += 1
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        text = self._call_with_retry(system, user)
        data = parse_json(text)
        if path:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        return data

    def _call_with_retry(self, system, user, tries=5):
        delay = 4.0
        for attempt in range(tries):
            wait = self.min_gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                self.calls += 1
                return self._call(system, user)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                    self.log(f"[llm] HTTP {e.code}, retrying in {delay:.0f}s")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"HTTP {e.code}: {body}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < tries - 1:
                    self.log(f"[llm] network error {e}, retrying in {delay:.0f}s")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(str(e)) from e
        raise LLMError("exhausted retries")

    def _post(self, url, payload, headers):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _call(self, system, user) -> str:
        p = self.provider
        if p == "gemini":
            base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
            url = f"{base}/models/{self.model}:generateContent?key={self.api_key}"
            out = self._post(url, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": self.temperature, "responseMimeType": "application/json"},
            }, {})
            um = out.get("usageMetadata", {})
            self.tokens_in += um.get("promptTokenCount", 0)
            self.tokens_out += um.get("candidatesTokenCount", 0)
            try:
                return "".join(part.get("text", "") for part in out["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError) as e:
                raise LLMError(f"unexpected Gemini response: {str(out)[:300]}") from e
        if p in ("openai", "ollama"):
            base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
            payload = {"model": self.model, "temperature": self.temperature,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if p == "openai":
                payload["response_format"] = {"type": "json_object"}
            out = self._post(f"{base}/chat/completions", payload, {"Authorization": f"Bearer {self.api_key}"})
            u = out.get("usage", {})
            self.tokens_in += u.get("prompt_tokens", 0)
            self.tokens_out += u.get("completion_tokens", 0)
            return out["choices"][0]["message"]["content"]
        if p == "anthropic":
            base = (self.base_url or "https://api.anthropic.com/v1").rstrip("/")
            out = self._post(f"{base}/messages", {
                "model": self.model, "max_tokens": 4096, "temperature": self.temperature, "system": system,
                "messages": [{"role": "user", "content": user + "\n\nReply with JSON only."}],
            }, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            u = out.get("usage", {})
            self.tokens_in += u.get("input_tokens", 0)
            self.tokens_out += u.get("output_tokens", 0)
            return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        raise LLMError(f"unknown provider {p}")


def parse_json(text: str) -> dict:
    """Models wrap JSON in prose or ``` fences surprisingly often; recover the object."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMError(f"model did not return valid JSON: {text[:200]!r}")
