import os

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def load_prompt(name: str) -> str:
    """从 prompts/ 目录加载 prompt 文件内容。

    Args:
        name: prompt 文件名（不含 .md 后缀），如 "expert_system", "judge_system"

    Returns:
        prompt 文本内容。

    Raises:
        FileNotFoundError: 文件不存在
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_prompt_with_vars(name: str, **kwargs) -> str:
    """加载 prompt 并做简单变量替换。

    用法: load_prompt_with_vars("judge_system", lesson="xxx")
    文件中使用 {{lesson}} 占位符。
    """
    text = load_prompt(name)
    for key, val in kwargs.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text