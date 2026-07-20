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
    MAX_TOKENS = max(1, int(os.getenv("MAX_TOKENS", 8000)))
    MAX_RETRY_TOKENS = max(
        MAX_TOKENS, int(os.getenv("MAX_RETRY_TOKENS", 16000))
    )
    API_TIMEOUT = max(1.0, float(os.getenv("API_TIMEOUT", 300)))
    API_CONNECT_TIMEOUT = max(
        1.0, float(os.getenv("API_CONNECT_TIMEOUT", 20))
    )
    API_RETRIES = max(0, int(os.getenv("API_RETRIES", 5)))
    API_RETRY_BASE_DELAY = max(
        0.0, float(os.getenv("API_RETRY_BASE_DELAY", 1))
    )
    API_RETRY_MAX_DELAY = max(
        API_RETRY_BASE_DELAY,
        float(os.getenv("API_RETRY_MAX_DELAY", 30)),
    )
    API_RETRY_JITTER = min(
        1.0, max(0.0, float(os.getenv("API_RETRY_JITTER", 0.25)))
    )
    # Context window settings for compression
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", 120000))
    COMPRESSION_THRESHOLD = int(os.getenv("COMPRESSION_THRESHOLD", 100000))


config = Config()
