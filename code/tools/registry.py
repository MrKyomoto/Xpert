from typing import Dict, Any, Callable, List
import inspect

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        
    def register(self, name: str, description: str, func: Callable):
        """Register a new tool."""
        # Introspect function to build parameters schema
        sig = inspect.signature(func)
        properties = {}
        required = []
        
        for param_name, param in sig.parameters.items():
            param_type = "string" # Default
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == float:
                param_type = "number"
                
            properties[param_name] = {
                "type": param_type,
                "description": f"Parameter {param_name}"
            }
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
                
        self._tools[name] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            },
            "callable": func
        }
        
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": t["function"]} for t in self._tools.values()]
        
    def execute(self, name: str, args: Dict[str, Any]) -> Any:
        if name not in self._tools:
            raise ValueError(f"Tool {name} not found")
        return self._tools[name]["callable"](**args)

# Global registry
registry = ToolRegistry()
