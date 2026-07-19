import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Both names are accepted by the CLI contract.  Keep the value empty when
    # neither is configured so callers fail explicitly instead of sending a
    # placeholder credential to the API.
    API_KEY = os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
    MODEL = os.getenv("MODEL", "gpt-4o")
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", 8000))
    API_TIMEOUT = float(os.getenv("API_TIMEOUT", 180))
    API_RETRIES = int(os.getenv("API_RETRIES", 2))
    # Context window settings for compression
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", 120000))
    COMPRESSION_THRESHOLD = int(os.getenv("COMPRESSION_THRESHOLD", 100000))


config = Config()
