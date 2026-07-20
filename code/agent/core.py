import json
import random
import sys
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI

from code.config import config
from code.memory.context import ContextManager
from code.skills.manager import SkillManager
from code.tools.registry import registry


class LLMCallError(RuntimeError):
    """Raised when an LLM request cannot produce a usable response."""


class LLMResponseError(LLMCallError):
    """A syntactically successful API call with no usable assistant payload."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: Optional[str] = None,
        retryable: bool = True,
        completion_tokens: Optional[int] = None,
        reasoning_tokens: Optional[int] = None,
    ):
        super().__init__(message)
        self.finish_reason = finish_reason
        self.retryable = retryable
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens


class Agent:
    def __init__(
        self,
        name: str,
        role_id: str = "",
        role_prompt: str = "",
        use_tools: bool = True,
        model: Optional[str] = None,
        client: Optional[Any] = None,
    ):
        self.name = name
        self.role_id = role_id
        self.use_tools = use_tools
        self.model = model or config.MODEL
        self.skill_manager = SkillManager(role_id) if role_id else None

        if self.skill_manager:
            system_prompt = self.skill_manager.load_system()
            if system_prompt:
                role_prompt = system_prompt
        self._base_role_prompt = role_prompt or f"你是{name}。"
        self.loaded_skill_names: List[str] = []

        if client is not None:
            self.client = client
        else:
            if not config.API_KEY:
                raise ValueError("API_KEY or OPENAI_API_KEY is not configured")
            self.client = OpenAI(
                api_key=config.API_KEY,
                base_url=config.API_BASE,
                timeout=httpx.Timeout(
                    config.API_TIMEOUT,
                    connect=config.API_CONNECT_TIMEOUT,
                ),
                # Keep one retry owner.  The application policy below also
                # validates HTTP-200 responses with empty choices/content,
                # which the SDK's transport-only retry cannot cover.
                max_retries=0,
            )

        self.context = ContextManager(model=self.model)
        self.context.add_message("system", self._base_role_prompt)

    def configure_skills(self, skill_names: List[str]) -> List[str]:
        """Activate exactly the requested skills and reset task conversation state.

        Rebuilding the system prompt, instead of appending forever, prevents a PBL
        run from leaking its case card into a later regular-lesson run.
        """
        unique_names = list(dict.fromkeys(skill_names))
        parts: List[str] = []

        if unique_names and not self.skill_manager:
            raise ValueError(f"角色 {self.name} 没有 SkillManager，无法加载 Skill")

        for name in unique_names:
            content = self.skill_manager.require_skill(name)  # type: ignore[union-attr]
            parts.append(f"### Skill: {name}\n{content}")

        prompt = self._base_role_prompt
        if parts:
            prompt += "\n\n## 本任务已加载的专业技能\n\n" + "\n\n".join(parts)

        self.loaded_skill_names = unique_names
        self.context = ContextManager(model=self.model)
        self.context.add_message("system", prompt)
        return list(self.loaded_skill_names)

    def ensure_skills(self, skill_names: List[str]) -> List[str]:
        """Compatibility helper that adds skills without duplicating them."""
        previous = set(self.loaded_skill_names)
        combined = self.loaded_skill_names + [
            name for name in skill_names if name not in previous
        ]
        self.configure_skills(combined)
        return [name for name in self.loaded_skill_names if name not in previous]

    def get_loaded_skills(self) -> List[str]:
        return list(self.loaded_skill_names)

    def get_skill_manifest(self) -> List[Dict[str, str]]:
        if not self.skill_manager:
            return []
        return [
            {
                "name": name,
                "sha256": self.skill_manager.skill_digest(name),
            }
            for name in self.loaded_skill_names
        ]

    def chat(
        self,
        message: str,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        self.context.add_message("user", message)
        return self._call_llm(response_format=response_format)

    def chat_stream(self, message: str) -> Generator[str, None, None]:
        """Stream a response after one complete, retryable request succeeds.

        Chunks are buffered until completion so a broken connection cannot leave
        a partial assistant message in history or be mistaken for valid output.
        """
        self.context.add_message("user", message)
        last_error: Optional[Exception] = None
        kwargs = self._request_kwargs(stream=True)
        total_attempts = config.API_RETRIES + 1
        attempts_made = 0

        for attempt in range(total_attempts):
            attempts_made = attempt + 1
            attempt_started = time.monotonic()
            try:
                stream = self.client.chat.completions.create(**kwargs)
                chunks: List[str] = []
                finish_reason: Optional[str] = None
                for event in stream:
                    choice = event.choices[0] if event.choices else None
                    if choice is not None and getattr(choice, "finish_reason", None):
                        finish_reason = str(choice.finish_reason)
                    delta = choice.delta if choice is not None else None
                    if delta is None:
                        continue
                    content = getattr(delta, "content", None)
                    if content:
                        chunks.append(content)
                    if getattr(delta, "tool_calls", None):
                        raise LLMCallError(
                            "流式模式收到工具调用；当前圆桌角色不允许流式工具调用"
                        )

                full_content = "".join(chunks)
                if finish_reason == "length":
                    raise LLMResponseError(
                        "LLM streaming response was truncated by the token limit",
                        finish_reason=finish_reason,
                    )
                if finish_reason == "content_filter":
                    raise LLMResponseError(
                        "LLM streaming response was blocked by content filtering",
                        finish_reason=finish_reason,
                        retryable=False,
                    )
                if not full_content.strip():
                    raise LLMResponseError(
                        "LLM returned an empty streaming response "
                        f"(finish_reason={finish_reason or 'unknown'})",
                        finish_reason=finish_reason,
                        retryable=finish_reason != "content_filter",
                    )
                self.context.add_message("assistant", full_content)
                yield from chunks
                return
            except Exception as exc:
                last_error = exc
                if isinstance(exc, LLMResponseError):
                    self._increase_token_budget(kwargs, exc)
                if not self._should_retry(exc) or attempt >= total_attempts - 1:
                    break
                self._wait_before_retry(
                    exc,
                    attempt,
                    total_attempts,
                    time.monotonic() - attempt_started,
                )

        raise LLMCallError(
            f"{self.name} API 调用在 {attempts_made} 次尝试后失败: "
            f"{self._error_detail(last_error)}"
        ) from last_error

    def _request_kwargs(
        self,
        stream: bool = False,
        response_format: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": self.context.get_messages(),
            "stream": stream,
        }
        if self.model.startswith("gpt-5"):
            kwargs["max_completion_tokens"] = config.MAX_TOKENS
        else:
            kwargs["max_tokens"] = config.MAX_TOKENS
            kwargs["temperature"] = 0.3
        if response_format:
            kwargs["response_format"] = response_format

        tools = registry.get_tool_schemas() if self.use_tools else []
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    def _create_with_retry(self, kwargs: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None
        request_kwargs = dict(kwargs)
        total_attempts = config.API_RETRIES + 1
        attempts_made = 0
        for attempt in range(total_attempts):
            attempts_made = attempt + 1
            attempt_started = time.monotonic()
            try:
                response = self.client.chat.completions.create(**request_kwargs)
                self._validate_completion_response(response)
                return response
            except Exception as exc:
                last_error = exc
                if isinstance(exc, LLMResponseError):
                    self._increase_token_budget(request_kwargs, exc)
                if not self._should_retry(exc) or attempt >= total_attempts - 1:
                    break
                self._wait_before_retry(
                    exc,
                    attempt,
                    total_attempts,
                    time.monotonic() - attempt_started,
                )
        raise LLMCallError(
            f"{self.name} API 调用在 {attempts_made} 次尝试后失败: "
            f"{self._error_detail(last_error)}"
        ) from last_error

    def _validate_completion_response(self, response: Any) -> None:
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMResponseError(
                "API response has no choices; " + self._response_metadata(response)
            )

        choice = choices[0]
        message = getattr(choice, "message", None)
        finish_reason = getattr(choice, "finish_reason", None)
        finish_text = str(finish_reason) if finish_reason is not None else None
        if message is None:
            raise LLMResponseError(
                "API response choice has no message; "
                + self._response_metadata(response, finish_text),
                finish_reason=finish_text,
            )

        if getattr(message, "tool_calls", None):
            return
        if finish_text == "length":
            raise LLMResponseError(
                "API response was truncated by the token limit; "
                + self._response_metadata(response, finish_text),
                finish_reason=finish_text,
            )
        if finish_text == "content_filter":
            raise LLMResponseError(
                "API response was blocked by content filtering; "
                + self._response_metadata(response, finish_text),
                finish_reason=finish_text,
                retryable=False,
            )
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return

        refusal = getattr(message, "refusal", None)
        retryable = finish_text not in {"content_filter"} and not bool(refusal)
        reasoning = getattr(message, "reasoning_content", None)
        reasoning_length = len(reasoning) if isinstance(reasoning, str) else 0
        usage = getattr(response, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)
        detail = self._response_metadata(response, finish_text)
        if reasoning_length:
            detail += f", hidden_reasoning_chars={reasoning_length}"
        if refusal:
            detail += ", refusal=true"
        raise LLMResponseError(
            "API returned an empty assistant response; " + detail,
            finish_reason=finish_text,
            retryable=retryable,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    @staticmethod
    def _response_metadata(
        response: Any,
        finish_reason: Optional[str] = None,
    ) -> str:
        response_id = getattr(response, "id", None) or "unknown"
        request_id = getattr(response, "_request_id", None)
        usage = getattr(response, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)
        parts = [f"response_id={response_id}"]
        if request_id:
            parts.append(f"request_id={request_id}")
        if finish_reason:
            parts.append(f"finish_reason={finish_reason}")
        if completion_tokens is not None:
            parts.append(f"completion_tokens={completion_tokens}")
        if total_tokens is not None:
            parts.append(f"total_tokens={total_tokens}")
        if reasoning_tokens is not None:
            parts.append(f"reasoning_tokens={reasoning_tokens}")
        return ", ".join(parts)

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        if isinstance(exc, LLMResponseError):
            return exc.retryable
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        should_retry = headers.get("x-should-retry") if headers else None
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
        if isinstance(status_code, int):
            return status_code in {408, 409, 425, 429} or status_code >= 500
        if isinstance(exc, APIConnectionError):
            return True
        if isinstance(exc, APIStatusError):
            return False
        if isinstance(exc, (ValueError, TypeError, KeyError, AssertionError)):
            return False
        # Preserve compatibility with OpenAI-compatible gateways that wrap
        # transport failures in their own exception class.
        return True

    def _wait_before_retry(
        self,
        exc: Exception,
        attempt: int,
        total_attempts: int,
        elapsed: float,
    ) -> None:
        delay = self._retry_after_seconds(exc)
        if delay is None:
            base = min(
                config.API_RETRY_BASE_DELAY * (2 ** attempt),
                config.API_RETRY_MAX_DELAY,
            )
            spread = max(0.0, base * config.API_RETRY_JITTER)
            delay = max(0.0, base + random.uniform(-spread, spread))
        delay = min(delay, config.API_RETRY_MAX_DELAY)
        print(
            f"[{self.name}] API 第 {attempt + 1}/{total_attempts} 次请求失败："
            f"{self._error_detail(exc)}；本次耗时 {elapsed:.2f}s；"
            f"{delay:.2f}s 后重试",
            file=sys.stderr,
        )
        time.sleep(delay)

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> Optional[float]:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        retry_ms = headers.get("retry-after-ms")
        if retry_ms is not None:
            try:
                value = float(retry_ms) / 1000
                return value if value > 0 else None
            except (TypeError, ValueError):
                pass
        retry_after = headers.get("retry-after")
        if retry_after is None:
            return None
        try:
            value = float(retry_after)
            return value if value > 0 else None
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                value = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return value if value > 0 else None
            except (TypeError, ValueError, OverflowError):
                return None

    def _increase_token_budget(
        self,
        kwargs: Dict[str, Any],
        exc: LLMResponseError,
    ) -> None:
        key = (
            "max_completion_tokens"
            if "max_completion_tokens" in kwargs
            else "max_tokens"
        )
        current = int(kwargs.get(key, config.MAX_TOKENS))
        token_limited = exc.finish_reason == "length"
        if exc.completion_tokens is not None:
            token_limited = token_limited or exc.completion_tokens >= current * 0.9
        if exc.reasoning_tokens is not None:
            token_limited = token_limited or exc.reasoning_tokens >= current * 0.8
        if not token_limited:
            return
        ceiling = max(config.MAX_TOKENS, config.MAX_RETRY_TOKENS)
        increased = min(max(current + 1024, current * 2), ceiling)
        if increased <= current:
            return
        kwargs[key] = increased
        print(
            f"[{self.name}] 响应因 token 上限截断，将 {key} "
            f"从 {current} 提高到 {increased}",
            file=sys.stderr,
        )

    @staticmethod
    def _error_detail(exc: Optional[Exception]) -> str:
        if exc is None:
            return "unknown error"
        status_code = getattr(exc, "status_code", None)
        request_id = getattr(exc, "request_id", None)
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not request_id and headers:
            request_id = headers.get("x-request-id")
        parts = [f"{type(exc).__name__}: {exc}"]
        if status_code is not None:
            parts.append(f"status={status_code}")
        if request_id:
            parts.append(f"request_id={request_id}")
        return ", ".join(parts)

    def _call_llm(
        self,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        tool_rounds = 0
        while True:
            response = self._create_with_retry(
                self._request_kwargs(response_format=response_format)
            )
            if not response.choices:
                raise LLMCallError(f"{self.name} API response has no choices")
            message_obj = response.choices[0].message

            if getattr(message_obj, "tool_calls", None):
                tool_rounds += 1
                if tool_rounds > 8:
                    raise LLMCallError(f"{self.name} exceeded the tool-call limit")
                assistant_msg = {
                    "role": "assistant",
                    "content": message_obj.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in message_obj.tool_calls
                    ],
                }
                self.context.messages.append(assistant_msg)

                for tool_call in message_obj.tool_calls:
                    try:
                        args = json.loads(tool_call.function.arguments)
                        print(
                            f"[{self.name}] Calling tool: "
                            f"{tool_call.function.name} with {args}"
                        )
                        result = registry.execute(tool_call.function.name, args)
                        result_text = str(result) if result is not None else "ok"
                    except Exception as exc:
                        result_text = f"Error: {exc}"
                    self.context.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.function.name,
                            "content": result_text,
                        }
                    )
                continue

            content = message_obj.content or ""
            if not content.strip():
                raise LLMCallError(f"{self.name} returned an empty response")
            self.context.add_message("assistant", content)
            return content
