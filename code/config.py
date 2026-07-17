import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_KEY = os.getenv("API_KEY", "your-api-key")
    API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
    MODEL = os.getenv("MODEL", "deepseek-v4-flash")
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", 4000))
    # Context window settings for compression
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", 120000))
    COMPRESSION_THRESHOLD = int(os.getenv("COMPRESSION_THRESHOLD", 100000))

config = Config()
