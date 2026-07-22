import hashlib
import os
from typing import Dict, List, Optional

SKILLS_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


class SkillInfo:
    """一个技能的元信息（名称+描述），不含完整内容。"""
    def __init__(self, role_dir: str, name: str, description: str, filepath: str):
        self.role_dir = role_dir
        self.name = name
        self.description = description
        self.filepath = filepath

    def __repr__(self):
        return f"[{self.role_dir}] {self.name}: {self.description[:40]}..."

    def sha256(self) -> str:
        """Return a stable digest without exposing the local absolute path."""
        digest = hashlib.sha256()
        with open(self.filepath, "rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()


class SkillManager:
    """Skill 管理器。

    - skills/{role_id}/_system.md 自动加载为 system prompt
    - benefits.md 等按需技能只索引名称+描述，LLM 需时通过 load_skill 注入
    """

    SYSTEM_FILE = "_system.md"

    def __init__(self, role_id: str):
        self.role_id = role_id
        self._index: List[SkillInfo] = []
        self._loaded_content: Dict[str, str] = {}
        self._build_index()

    def _build_index(self):
        role_dir = os.path.join(SKILLS_BASE, self.role_id)
        if not os.path.isdir(role_dir):
            return
        for fname in sorted(os.listdir(role_dir)):
            if not fname.endswith(".md") or fname == self.SYSTEM_FILE:
                continue
            filepath = os.path.join(role_dir, fname)
            name = fname[:-3]
            with open(filepath, "r", encoding="utf-8") as f:
                desc = ""
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        desc = stripped[:120]
                        break
            self._index.append(SkillInfo(self.role_id, name, desc, filepath))

    def load_system(self) -> str:
        """加载 _system.md 作为 system prompt。"""
        path = os.path.join(SKILLS_BASE, self.role_id, self.SYSTEM_FILE)
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()

    @property
    def index(self) -> List[SkillInfo]:
        return list(self._index)

    @property
    def available_names(self) -> List[str]:
        return [skill.name for skill in self._index]

    def index_text(self, exclude: Optional[set] = None) -> str:
        if not self._index:
            return f"（角色 {self.role_id} 暂无可用技能）"
        exclude = exclude or set()
        lines = [f"角色 {self.role_id} 可用技能:"]
        for s in self._index:
            if s.name in exclude:
                continue
            lines.append(f"  - {s.name}: {s.description}")
        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def load_skill(self, name: str) -> Optional[str]:
        if name in self._loaded_content:
            return self._loaded_content[name]
        for s in self._index:
            if s.name == name:
                with open(s.filepath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                self._loaded_content[name] = content
                return content
        return None

    def require_skill(self, name: str) -> str:
        """Load a configured skill or fail fast on a misspelled plan entry."""
        content = self.load_skill(name)
        if content is None:
            available = ", ".join(self.available_names) or "无"
            raise ValueError(
                f"角色 {self.role_id} 不存在 Skill '{name}'；可用 Skill: {available}"
            )
        return content

    def skill_digest(self, name: str) -> str:
        for skill in self._index:
            if skill.name == name:
                return skill.sha256()
        raise ValueError(f"角色 {self.role_id} 不存在 Skill '{name}'")

    def loaded_skills_text(self) -> str:
        if not self._loaded_content:
            return ""
        parts = [f"## 已加载技能 ({self.role_id})"]
        for name, content in self._loaded_content.items():
            parts.append(f"### {name}\n{content}")
        return "\n\n".join(parts)
