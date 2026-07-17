from typing import List, Dict, Any, Optional
import json
from openai import OpenAI
from code.config import config
from code.memory.context import ContextManager
from code.tools.registry import registry
from code.skills.manager import SkillManager

class Agent:
    def __init__(self, name: str, role_prompt: str):
        self.name = name
        self.client = OpenAI(api_key=config.API_KEY, base_url=config.API_BASE)
        self.context = ContextManager(model=config.MODEL)
        self.context.add_message("system", role_prompt)
        
        self.skill_manager = SkillManager()
        self.skill_manager.discover_and_load()
        
    def chat(self, message: str) -> str:
        self.context.add_message("user", message)
        
        while True:
            # Prepare call
            kwargs = {
                "model": config.MODEL,
                "messages": self.context.get_messages(),
                "temperature": 0.7,
                "max_tokens": config.MAX_TOKENS,
            }
            
            # Add tools if available
            tools = registry.get_tool_schemas()
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
                
            try:
                response = self.client.chat.completions.create(**kwargs)
                message_obj = response.choices[0].message
                
                # Check for tool calls
                if message_obj.tool_calls:
                    # Add assistant message with tool calls
                    assistant_msg = {"role": "assistant", "content": message_obj.content or ""}
                    assistant_msg["tool_calls"] = [
                        {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}} 
                        for t in message_obj.tool_calls
                    ]
                    self.context.messages.append(assistant_msg)
                    
                    # Execute tools
                    for tool_call in message_obj.tool_calls:
                        tool_name = tool_call.function.name
                        try:
                            args = json.loads(tool_call.function.arguments)
                            print(f"[{self.name}] Calling tool: {tool_name} with {args}")
                            result = registry.execute(tool_name, args)
                            result_str = str(result)
                        except Exception as e:
                            result_str = f"Error executing tool: {str(e)}"
                            
                        # Add tool result
                        self.context.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": result_str
                        })
                    
                    # Continue the loop to let the model process the tool results
                    continue
                else:
                    # Normal text response
                    content = message_obj.content
                    self.context.add_message("assistant", content)
                    return content
                    
            except Exception as e:
                return f"Error communicating with LLM API: {str(e)}"
