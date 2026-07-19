import json
import time
from typing import Any, Dict, Generator, List, Optional

from openai import OpenAI

from code.config import config
from code.memory.context import ContextManager
from code.skills.manager import SkillManager
from code.tools.registry import registry


class LLMCallError(RuntimeError):
    """Raised when an LLM request cannot produce a usable response."""


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
                timeout=config.API_TIMEOUT,
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

        for attempt in range(config.API_RETRIES + 1):
            try:
                kwargs = self._request_kwargs(stream=True)
                stream = self.client.chat.completions.create(**kwargs)
                chunks: List[str] = []
                for event in stream:
                    delta = event.choices[0].delta if event.choices else None
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
                if not full_content.strip():
                    raise LLMCallError("LLM returned an empty streaming response")
                self.context.add_message("assistant", full_content)
                yield from chunks
                return
            except Exception as exc:  # OpenAI exposes several transport subclasses
                last_error = exc
                if attempt < config.API_RETRIES:
                    time.sleep(min(2 ** attempt, 4))

        raise LLMCallError(
            f"{self.name} API 调用在 {config.API_RETRIES + 1} 次尝试后失败: {last_error}"
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
        for attempt in range(config.API_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < config.API_RETRIES:
                    time.sleep(min(2 ** attempt, 4))
        raise LLMCallError(
            f"{self.name} API 调用在 {config.API_RETRIES + 1} 次尝试后失败: {last_error}"
        ) from last_error

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
