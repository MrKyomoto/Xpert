from typing import List, Dict, Any, Optional, Generator
import json
from openai import OpenAI
from code.config import config
from code.memory.context import ContextManager
from code.tools.registry import registry
from code.skills.manager import SkillManager


class Agent:
    def __init__(self, name: str, role_id: str = "", role_prompt: str = "", use_tools: bool = True):
        self.name = name
        self.use_tools = use_tools
        self.client = OpenAI(api_key=config.API_KEY, base_url=config.API_BASE)
        self.context = ContextManager(model=config.MODEL)

        # system prompt: from _system.md or param
        self.skill_manager = SkillManager(role_id) if role_id else None
        if self.skill_manager:
            loaded = self.skill_manager.load_system()
            if loaded:
                role_prompt = loaded
        if not role_prompt:
            role_prompt = f"你是{name}。"

        # inject skills index + full content (small files, direct injection)
        if self.skill_manager:
            idx = self.skill_manager.index_text()
            if idx:
                role_prompt += "\n\n" + idx
            for s in self.skill_manager.index:
                content = self.skill_manager.load_skill(s.name)
                if content:
                    role_prompt += f"\n\n### {s.name}\n{content}"

        self.context.add_message("system", role_prompt)

    def chat(self, message: str) -> str:
        self.context.add_message("user", message)
        return self._call_llm()

    def chat_stream(self, message: str) -> Generator[str, None, None]:
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
                if hasattr(delta, 'content') and delta.content:
                    full_content += delta.content
                    yield delta.content
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc in delta.tool_calls:
                        fn = tc.function
                        if fn and fn.name:
                            yield f"\n[Tool: {fn.name}]"
                        if fn and fn.arguments:
                            full_content += fn.arguments
            self.context.add_message("assistant", full_content)
        except Exception as e:
            yield f"\n[Error: {str(e)}]"

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
                        {"id": t.id, "type": "function",
                         "function": {"name": t.function.name, "arguments": t.function.arguments}}
                        for t in message_obj.tool_calls
                    ]
                    self.context.messages.append(assistant_msg)

                    for tool_call in message_obj.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            args = json.loads(tool_call.function.arguments)
                            print(f"[{self.name}] Calling tool: {tool_name} with {args}")
                            result = registry.execute(tool_name, args)
                            result_str = str(result) if result is not None else "ok"
                        except Exception as e:
                            result_str = f"Error: {str(e)}"
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