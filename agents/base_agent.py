import anthropic
from config.settings import ANTHROPIC_API_KEY, DEFAULT_MODEL, MAX_TOKENS

class BaseAgent:
    def __init__(self, system_prompt: str, tools: list = []):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.system_prompt = system_prompt
        self.tools = tools
        self.conversation_history = []

    def chat(self, user_message: str) -> str:
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        response = self.client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=MAX_TOKENS,
            system=self.system_prompt,
            messages=self.conversation_history
        )

        assistant_message = response.content[0].text
        self.conversation_history.append({
            "role": "assistant",
            "content": assistant_message
        })

        return assistant_message

    def reset(self):
        self.conversation_history = []