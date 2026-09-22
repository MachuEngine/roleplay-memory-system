"""채팅 모델 호출 어댑터.

공급자 교체를 위해 호출부를 인터페이스로 분리한다. 구현체는 OpenRouter 하나이며,
프레임워크는 도입하지 않는다(표준 라이브러리만 사용).

보안:
- API 키는 환경 변수 OPENROUTER_API_KEY에서만 읽는다.
- 키는 로그·예외 메시지·반환값 어디에도 넣지 않는다. scrub()로 한 번 더 거른다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Protocol

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
ENV_KEY = "OPENROUTER_API_KEY"
UNKNOWN = "확인 불가"


class MissingKey(RuntimeError):
    pass


@dataclass
class ChatResult:
    """호출 1회의 기록. 없는 값은 추측하지 않고 UNKNOWN으로 남긴다."""
    ok: bool
    text: str = ""
    error: str = ""
    request_model: str = ""
    routed_provider: str = UNKNOWN
    served_model: str = UNKNOWN
    tokens_in: int | str = UNKNOWN
    tokens_out: int | str = UNKNOWN
    tokens_reasoning: int | str = UNKNOWN
    tokens_cached: int | str = UNKNOWN
    finish_reason: str = UNKNOWN
    native_finish_reason: str = UNKNOWN
    cost_usd_api: float | str = UNKNOWN
    upstream_cost_usd: float | str = UNKNOWN
    latency_s: float = 0.0
    generation_id: str = UNKNOWN
    raw_usage: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class ChatClient(Protocol):
    def complete(self, system: str, user: str, *, max_tokens: int,
                 reasoning_max_tokens: int | None) -> ChatResult: ...


def scrub(text: str, secret: str | None) -> str:
    """혹시라도 문자열에 키가 섞이면 제거한다."""
    if secret and secret in text:
        text = text.replace(secret, "[REDACTED]")
    return text


class OpenRouterClient:
    """OpenRouter chat/completions 어댑터.

    재시도는 일시적 오류(429/5xx/타임아웃)에 한해 최대 2회. 그 외는 즉시 반환한다.
    """

    def __init__(self, model: str = "google/gemini-2.5-pro", *,
                 timeout: int = 180, max_retries: int = 2, provider: str | None = None,
                 referer: str = "http://localhost", title: str = "eleven-assignment-verification"):
        # provider: OpenRouter 엔드포인트 태그를 지정해 라우팅을 고정한다.
        # 암시적 캐싱은 같은 백엔드로 요청이 모여야 성립하므로, 캐시 측정에는 고정이 필요하다.
        self.provider = provider
        self._key = os.environ.get(ENV_KEY, "").strip()
        if not self._key:
            raise MissingKey(
                f"환경 변수 {ENV_KEY}가 비어 있습니다. "
                f"export {ENV_KEY}=... 후 다시 실행하세요."
            )
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "HTTP-Referer": referer,
            "X-Title": title,
        }

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 reasoning_max_tokens: int | None = 128,
                 temperature: float = 1.0, seed: int | None = 7) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if seed is not None:
            body["seed"] = seed
        if self.provider:
            body["provider"] = {"order": [self.provider], "allow_fallbacks": False}
        if reasoning_max_tokens is not None:
            # 문서상 Gemini에서는 thinkingBudget으로 전달된다.
            body["reasoning"] = {"max_tokens": reasoning_max_tokens}

        payload = json.dumps(body).encode("utf-8")
        delay, last = 2.0, "no attempt"
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                req = urllib.request.Request(ENDPOINT, data=payload,
                                             headers=self._headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                return self._parse(data, time.time() - started)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                last = f"HTTP {e.code}: {scrub(detail, self._key)}"
                transient = e.code == 429 or 500 <= e.code < 600
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = scrub(f"{type(e).__name__}: {e}", self._key)
                transient = True
            if not transient or attempt == self.max_retries:
                break
            time.sleep(delay)
            delay *= 2
        return ChatResult(ok=False, error=last, request_model=self.model)

    def _parse(self, data: dict[str, Any], latency: float) -> ChatResult:
        if "error" in data and not data.get("choices"):
            return ChatResult(ok=False, request_model=self.model,
                              error=scrub(json.dumps(data["error"], ensure_ascii=False)[:400], self._key),
                              latency_s=round(latency, 2))
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        pdet = usage.get("prompt_tokens_details") or {}
        cdet = usage.get("completion_tokens_details") or {}
        cost_details = usage.get("cost_details") or {}

        def num(v: Any) -> int | float | str:
            return v if isinstance(v, (int, float)) else UNKNOWN

        return ChatResult(
            ok=True,
            text=scrub(msg.get("content") or "", self._key),
            request_model=self.model,
            served_model=data.get("model") or UNKNOWN,
            routed_provider=data.get("provider") or UNKNOWN,
            tokens_in=num(usage.get("prompt_tokens")),
            tokens_out=num(usage.get("completion_tokens")),
            tokens_reasoning=num(cdet.get("reasoning_tokens")),
            tokens_cached=num(pdet.get("cached_tokens")),
            finish_reason=choice.get("finish_reason") or UNKNOWN,
            native_finish_reason=choice.get("native_finish_reason") or UNKNOWN,
            cost_usd_api=num(usage.get("cost")),
            upstream_cost_usd=num(cost_details.get("upstream_inference_cost")),
            latency_s=round(latency, 2),
            generation_id=data.get("id") or UNKNOWN,
            raw_usage=usage,
        )
