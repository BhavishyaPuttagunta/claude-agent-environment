import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = "claude-opus-4-5"
MAX_TOKENS = 4096
KB_DIR = "knowledge_base"

os.makedirs(KB_DIR, exist_ok=True)