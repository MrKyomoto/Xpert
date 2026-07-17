from typing import List, Dict, Any, Optional, Generator
import json
from openai import OpenAI
from code.config import config
from code.memory.context import ContextManager
from code.tools.registry import registry
from code.skills.manager import SkillManager

class Agent:
    def __init__(self, name: str, role_prompt: str, use_tools: bool = True):
        self.name = name
        self.use_tools = use_tools
        self.client = OpenAI(api_key=config.API_KEY, base_url=config.API_BASE)
        self.context = ContextManager(model=config.MODEL)
        self.context.add_message("system", role_prompt)

        if use_tools:
            self.skill_manager = SkillManager()
            self.skill_manager.discover_and_load()

    def chat(self, message: str) -> str:
        self.context.add_message("user", message)
        return self._call_llm()

    def chat_stream(self, message: str) -> Generator[str, None, None]:
        """流式调用，逐 chunk 产出文本，同时保留完整消息到上下文。"""
        self.context.add_message("user", message)

        kwargs = {
            "model": config.MODEL,
            "messages": self.context.get_messages(),
            "temperature": 0.7,
            "max_tokens": config.MAX_TOKENS,
            "stream": True,
        }

        tools = registry.get_tool_schemas() if self.use_tools else []
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        full_content = ""
        try:
            stream = self.client.chat.completions.create(**kwargs)
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                # Text content
                if hasattr(delta, 'content') and delta.content:
                    full_content += delta.content
                    yield delta.content

                # Tool calls — simplified: just note them
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc in delta.tool_calls:
                        fn = tc.function
                        if fn and fn.name:
                            yield f"\n[⚙ 调用工具: {fn.name}]"
                        if fn and fn.arguments:
                            full_content += fn.arguments

            # Done streaming — save the full response to context
            self.context.add_message("assistant", full_content)
        except Exception as e:
            yield f"\n[错误: {str(e)}]"

    def _call_llm(self) -> str:
        kwargs = {
            "model": config.MODEL,
            "messages": self.context.get_messages(),
            "temperature": 0.7,
            "max_tokens": config.MAX_TOKENS,
        }
        tools = registry.get_tool_schemas() if self.use_tools else []
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        while True:
            try:
                response = self.client.chat.completions.create(**kwargs)
                message_obj = response.choices[0].message

                if hasattr(message_obj, 'tool_calls') and message_obj.tool_calls:
                    assistant_msg = {"role": "assistant", "content": message_obj.content or ""}
                    assistant_msg["tool_calls"] = [
                        {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
                        for t in message_obj.tool_calls
                    ]
                    self.context.messages.append(assistant_msg)

                    for tool_call in message_obj.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            args = json.loads(tool_call.function.arguments)
                            print(f"[{self.name}] Calling tool: {tool_name} with {args}")
                            result = registry.execute(tool_name, args)
                            result_str = str(result)
                        except Exception as e:
                            result_str = f"Error executing tool: {str(e)}"
                        self.context.messages.append({
                            "role": "tool", "tool_call_id": tool_call.id,
                            "name": tool_name, "content": result_str
                        })
                    continue

                content = message_obj.content or ""
                self.context.add_message("assistant", content)
                return content

            except Exception as e:
                return f"Error communicating with LLM API: {str(e)}"