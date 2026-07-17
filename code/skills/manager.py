import os
import importlib.util
from typing import Dict, Any, List
from code.tools.registry import registry

class SkillManager:
    def __init__(self, skills_dir: str = "code/skills"):
        self.skills_dir = skills_dir
        self.loaded_skills: Dict[str, Any] = {}
        
    def discover_and_load(self):
        """Discover and load all skills in the skills directory."""
        if not os.path.exists(self.skills_dir):
            return
            
        for file in os.listdir(self.skills_dir):
            if file.endswith(".py") and file != "__init__.py" and file != "manager.py":
                module_name = file[:-3]
                self.load_skill(module_name)
                
    def load_skill(self, name: str):
        """Load a specific skill by name."""
        file_path = os.path.join(self.skills_dir, f"{name}.py")
        if not os.path.exists(file_path):
            return False
            
        spec = importlib.util.spec_from_file_location(f"skills.{name}", file_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # If the module has a setup function, call it with the registry
            if hasattr(module, "setup"):
                module.setup(registry)
                self.loaded_skills[name] = module
                return True
        return False
