from typing import List, Dict, Any, Optional, Generator
import json
from openai import OpenAI
from code.config import config
from code.memory.context import ContextManager
from code.tools.registry import registry
from code.skills.manager import SkillManager


class Agent:
    def __init__(self, name: str, role_id: str = "", role_prompt: str = "",
                 use_tools: bool = True, preload_skills: Optional[List[str]] = None):
        self.name = name
        self.use_tools = use_tools
        self.client = OpenAI(api_key=config.API_KEY, base_url=config.API_BASE)
        self.context = ContextManager(model=config.MODEL)

        # skill 管理器
        self.skill_manager = SkillManager(role_id) if role_id else None
        self._loaded_skill_names: List[str] = []
        if self.skill_manager:
            loaded = self.skill_manager.load_system()
            if loaded:
                role_prompt = loaded
        if not role_prompt:
            role_prompt = f"你是{name}。"

        # 预注入的核心技能（直接加载全文）
        preload = preload_skills or []
        if self.skill_manager and preload:
            for sname in preload:
                content = self.skill_manager.require_skill(sname)
                role_prompt += f"\n\n### {sname}\n{content}"
            role_prompt += "\n"

        # 其余技能只注入索引（名称+描述），按需加载。use_tools=False 时跳过索引。
        if use_tools and self.skill_manager:
            idx = self.skill_manager.index_text(exclude=set(preload))
            if idx:
                role_prompt += "\n\n" + idx
                role_prompt += "\n\n需要时可通过 load_skill 工具加载某个技能的完整内容。"

        self.context.add_message("system", role_prompt)

        # 注册 load_skill 工具（幂等），use_tools=False 时不注册
        if use_tools:
            self._register_load_skill()

    def _register_load_skill(self):
        """注册 load_skill 工具，LLM 可调用它按需加载技能内容。"""
        for name in registry._tools:
            if name == "load_skill":
                return
        registry.register(
            "load_skill",
            "按名称加载一个技能的完整内容到对话上下文。名称来自可用技能列表。",
            self._load_skill_impl
        )

    def _load_skill_impl(self, skill_name: str) -> str:
        if not self.skill_manager:
            return "错误: 未配置 SkillManager"
        content = self.skill_manager.load_skill(skill_name)
        if content is None:
            return f"错误: 未找到 skill '{skill_name}'"
        return f"技能 '{skill_name}' 的完整内容:\n\n{content}"

    def configure_skills(self, skill_names: List[str]) -> List[str]:
        """预留接口，当前由索引 + 按需加载替代，直接返回。"""
        return []

    def chat(self, message: str, temperature: Optional[float] = None) -> str:
        self.context.add_message("user", message)
        return self._call_llm(temperature=temperature)

    def _call_llm(self, temperature: Optional[float] = None) -> str:
        kwargs = {
            "model": config.MODEL,
            "messages": self.context.get_messages(),
            "temperature": temperature if temperature is not None else 0.7,
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