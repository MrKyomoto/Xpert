import json
import tiktoken
from typing import List, Dict, Any, Optional
from code.config import config

class ContextManager:
    def __init__(self, model: str = "gpt-4o"):
        self.messages: List[Dict[str, Any]] = []
        self.model = model
        try:
            self.encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self.encoder = tiktoken.get_encoding("cl100k_base")
            
    def add_message(self, role: str, content: str, name: Optional[str] = None):
        msg = {"role": role, "content": content}
        if name:
            msg["name"] = name
        self.messages.append(msg)
        self._check_and_compress()
        
    def get_messages(self) -> List[Dict[str, Any]]:
        return self.messages
        
    def get_token_count(self) -> int:
        count = 0
        for msg in self.messages:
            # Approximate token count for message structure
            count += 4 
            for key, value in msg.items():
                if isinstance(value, str):
                    count += len(self.encoder.encode(value))
        count += 3  # every reply is primed with <|start|>assistant<|message|>
        return count
        
    def _check_and_compress(self):
        # MVP: Simple compression by removing older messages (excluding system)
        if self.get_token_count() > config.COMPRESSION_THRESHOLD:
            self._compress()
            
    def _compress(self):
        # Keep system message if exists
        system_msgs = [m for m in self.messages if m["role"] == "system"]
        # Keep last few recent messages
        recent_msgs = self.messages[-5:]
        
        # Summarize or drop the middle part (MVP: just drop for now)
        # A full version would use an LLM call to summarize the middle messages
        
        # Update messages
        self.messages = system_msgs + [{"role": "system", "content": "--- Context compressed ---"}] + recent_msgs

    def save_checkpoint(self):
        """保存当前消息列表长度，供后续回滚时截断。"""
        self._checkpoint = len(self.messages)

    def restore_checkpoint(self):
        """将消息列表截断回最近一次 save_checkpoint 时的状态。"""
        if hasattr(self, '_checkpoint') and self._checkpoint is not None:
            self.messages = self.messages[:self._checkpoint]

    def to_json(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False, indent=2)

    def load_json(self, json_str: str):
        self.messages = json.loads(json_str)
