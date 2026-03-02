"""
FDA Regulatory Intelligence Agent
Features: multi-turn memory | streaming | tool use | file versioning
"""

import anthropic
import json
from tools import TOOL_DEFINITIONS, execute_tool
from config import MODEL, MAX_TOKENS, KB_DIR
SYSTEM_PROMPT = """You are an FDA Regulatory Intelligence Agent. You help users:

1. SCRAPE FDA sources — ecfr.gov, fda.gov, federalregister.gov
2. SAVE & VERSION scraped docs to a local knowledge base (with timestamps for auditing)
3. COMPARE regulation versions to identify what changed between scrapes
4. ANSWER questions using saved knowledge base files
5. CHECK product descriptions against loaded FDA requirements

Behavior rules:
- After every scrape, immediately save the result with save_file
- When asked about a regulation, first check list_files — if it exists, read it. If not, scrape it.
- When comparing versions, load both files and give a clear bullet-point diff summary
- Always mention the date a document was scraped so users know how current it is
"""

class FDAgent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.history = []
        print("\n🏥  FDA Regulatory Intelligence Agent")
        print("=" * 50)
        print("Commands: 'exit' | 'clear' (reset memory) | 'history'\n")
        print("Try: 'Scrape 21 CFR Part 820 from ecfr.gov and save it'\n")

    def chat(self, user_message: str):
        self.history.append({"role": "user", "content": user_message})

        # Agentic loop — keeps running until no more tool calls
        while True:
            response_text = ""

            with self.client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=self.history,
            ) as stream:
                for event in stream:
                    if hasattr(event, "type") and event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            print(event.delta.text, end="", flush=True)
                            response_text += event.delta.text

                final = stream.get_final_message()

            # Collect any tool calls
            tool_calls = [b for b in final.content if b.type == "tool_use"]

            if not tool_calls:
                print()
                self.history.append({"role": "assistant", "content": final.content})
                return

            # Execute tools
            print()
            self.history.append({"role": "assistant", "content": final.content})

            tool_results = []
            for tc in tool_calls:
                print(f"\n⚙️  [{tc.name}] {json.dumps(tc.input)}")
                result = execute_tool(tc.name, tc.input)
                print(f"✅  Done — {str(result)[:120]}...")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": str(result),
                })

            self.history.append({"role": "user", "content": tool_results})
            print("\n🤖 Agent: ", end="", flush=True)

    def run(self):
        while True:
            try:
                user_input = input("\n🔬 You: ").strip()
                if not user_input:
                    continue
                if user_input.lower() == "exit":
                    print("Goodbye!"); break
                if user_input.lower() == "clear":
                    self.history = []
                    print("🗑️  Memory cleared."); continue
                if user_input.lower() == "history":
                    print(json.dumps(self.history, indent=2, default=str)); continue

                print("\n🤖 Agent: ", end="", flush=True)
                self.chat(user_input)

            except KeyboardInterrupt:
                print("\nUse 'exit' to quit.")

if __name__ == "__main__":
    agent = FDAgent()
    agent.run()