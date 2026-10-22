"""
Concurrent client for an OpenAI-compatible server (vLLM, Ollama, OpenAI...).

vLLM batches requests that arrive together, so keeping 16-64 requests in flight is what makes
the GPU fast; sending one page at a time leaves it mostly idle.

Each request asks for structured output (`response_format` with a JSON schema). If the server
rejects that option, the engine switches it off once and continues without it (the parser
still repairs JSON, as in the interactive app).
"""

from __future__ import annotations

import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests

from modules.extraction import schema as sc
from modules.extraction.pages import Unit

TRANSPORT = "transport"     # server unreachable / timeout / 5xx: retry the paper later
CONTENT = "content"         # the server answered but the request was refused (e.g. too long)


class ServerUnavailable(RuntimeError):
    """No request in a batch reached the server."""


@dataclass
class Result:
    unit_id: str
    text: str = ""
    ok: bool = False
    error: str = ""
    error_kind: str = ""
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    used_schema: bool = False


@dataclass
class Request:
    unit_id: str
    messages: list
    schema: Optional[dict] = None
    max_tokens: int = 3072
    meta: dict = field(default_factory=dict)


def build_request(unit: Unit, max_tokens: int) -> Request:
    if unit.kind == "table":
        prompt, schema = sc.TABLE_PROMPT, sc.TABLE_SCHEMA
    else:
        prompt, schema = sc.PROCEDURE_PROMPT, sc.PROCEDURE_SCHEMA
    if unit.mode == "text":
        content = prompt + "\n\nDOCUMENT TEXT:\n" + unit.text
    else:
        b64 = base64.b64encode(unit.png).decode()
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
    return Request(unit.unit_id, [{"role": "user", "content": content}], schema, max_tokens)


def chat_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


class HttpEngine:
    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY", concurrency: int = 32,
                 timeout: float = 300, retries: int = 2, use_schema: bool = True,
                 session: Optional[requests.Session] = None):
        self.url = chat_url(base_url)
        self.model = model
        self.api_key = api_key or "EMPTY"
        self.concurrency = max(1, int(concurrency))
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.use_schema = use_schema
        self._lock = threading.Lock()
        self._local = threading.local()
        self._session = session

    def _http(self) -> requests.Session:
        if self._session is not None:
            return self._session
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = requests.Session()
        return s

    def _payload(self, req: Request, with_schema: bool) -> dict:
        p = {"model": self.model, "temperature": 0, "max_tokens": req.max_tokens, "messages": req.messages}
        if with_schema and req.schema:
            p["response_format"] = {"type": "json_schema",
                                    "json_schema": {"name": "extraction", "schema": req.schema}}
        return p

    def call(self, req: Request) -> Result:
        res = Result(req.unit_id)
        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        attempt = 0
        while True:
            with_schema = self.use_schema and req.schema is not None
            try:
                r = self._http().post(self.url, json=self._payload(req, with_schema), headers=headers,
                                      timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                res.error, res.error_kind = f"{type(e).__name__}: {e}", TRANSPORT
                break
            if r.status_code == 400 and with_schema and any(
                    k in r.text.lower() for k in ("response_format", "json_schema", "grammar", "structured")):
                with self._lock:              # server does not support structured output
                    self.use_schema = False
                continue
            if r.status_code >= 500 or r.status_code == 429:
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(2 ** attempt)
                    continue
                res.error, res.error_kind = f"HTTP {r.status_code}: {r.text[:300]}", TRANSPORT
                break
            if not r.ok:
                kind = TRANSPORT if r.status_code in (401, 403, 404) else CONTENT
                res.error, res.error_kind = f"HTTP {r.status_code}: {r.text[:300]}", kind
                break
            try:
                body = r.json()
                content = body["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                res.error, res.error_kind = f"unexpected response: {r.text[:300]}", CONTENT
                break
            if isinstance(content, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            usage = body.get("usage") or {}
            res.text, res.ok, res.used_schema = content or "", True, with_schema
            res.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            res.completion_tokens = int(usage.get("completion_tokens") or 0)
            break
        res.seconds = time.perf_counter() - t0
        return res

    def run(self, reqs: list[Request], on_done: Optional[Callable[[Result], None]] = None) -> list[Result]:
        """All requests, `concurrency` at a time. Results come back in the order of `reqs`."""
        if not reqs:
            return []
        out: list[Optional[Result]] = [None] * len(reqs)

        def work(i):
            r = self.call(reqs[i])
            out[i] = r
            if on_done:
                on_done(r)

        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(reqs))) as ex:
            list(ex.map(work, range(len(reqs))))
        return out  # type: ignore[return-value]
