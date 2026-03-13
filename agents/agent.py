"""
agents/agent.py
Regulatory Intelligence Agent — simplified URL scraping flow

Core flow:
  1. User gives a URL → agent scrapes and saves it (auto-saved by deep_scrape)
  2. User asks questions → agent reads from knowledge base
  3. User gives same URL again → agent scrapes, compares, reports changes
"""

import anthropic
import json
from tools.tools import TOOL_DEFINITIONS, execute_tool
from config.config import MODEL, MAX_TOKENS

SYSTEM_PROMPT = """You are a Regulatory Intelligence Agent. You help users monitor regulatory and compliance web pages for changes.

## Your Core Flow

1. User gives you a URL → scrape it with deep_scrape → it auto-saves to knowledge base
2. User asks a question about saved content → use read_regulation or list_regulations
3. User gives the same URL again → scrape it again → compare_versions shows what changed

## Tools

| Task                                   | Tool             |
|----------------------------------------|------------------|
| Scrape a URL and save it (auto-saves)  | deep_scrape      |
| Read saved content from knowledge base | read_regulation  |
| List all saved pages                   | list_regulations |
| Compare two versions of a saved page   | compare_versions |
| View full change history               | check_changes    |

## Rules

1. When user gives a URL → always use deep_scrape. It auto-saves — do NOT call save_regulation after deep_scrape.
2. When user asks about a topic → call list_regulations first, then read_regulation to get content.
3. When deep_scrape says CHANGED → immediately call compare_versions and summarise what changed.
4. Never make up content — only answer from what is in the knowledge base.
5. Always tell the user when the page was last scraped and how many versions exist.

## Response Style
- Be concise and clear
- Always mention the source URL and date scraped
- Highlight changes with ⚠️ when content has changed
- If nothing is saved yet, ask the user to provide a URL to get started
"""


class FDAgent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.history = []
        print("\n🏥  Regulatory Intelligence Agent")
        print("=" * 50)
        from config.config import DB_SERVER, DB_NAME; print(f"Storage: SQL Server ({DB_SERVER}/{DB_NAME})")
        print("Commands: 'exit' | 'clear' | 'history'")
        print("\nExamples:")
        print("  • Scrape https://www.fda.gov/food/... and save it as fda_food")
        print("  • What does the saved page say about labeling?")
        print("  • Scrape https://www.fda.gov/food/... again and show changes")
        print("  • List all saved pages\n")

    def chat(self, user_message: str) -> None:
        self.history.append({"role": "user", "content": user_message})

        MAX_ITERATIONS = 10

        for iteration in range(1, MAX_ITERATIONS + 1):
            if iteration > 1:
                print(f"  [loop {iteration}/{MAX_ITERATIONS}]", flush=True)

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
                final = stream.get_final_message()

            tool_calls = [b for b in final.content if b.type == "tool_use"]

            if not tool_calls:
                print()
                self.history.append({"role": "assistant", "content": final.content})
                return

            print()
            self.history.append({"role": "assistant", "content": final.content})

            tool_results = []
            for tc in tool_calls:
                print(f"\n  ⚙️  [{tc.name}] {json.dumps(tc.input)[:120]}")
                result = execute_tool(tc.name, tc.input)
                preview = str(result).replace("\n", " ")[:160]
                print(f"  ↳  {preview}{'...' if len(str(result)) > 160 else ''}")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tc.id,
                    "content":     str(result),
                })

            self.history.append({"role": "user", "content": tool_results})
            print("\n🤖 Agent: ", end="", flush=True)

        print(f"\n⚠️  Reached max iterations ({MAX_ITERATIONS}). Stopping.")

    def run(self) -> None:
        while True:
            try:
                user_input = input("\n🔬 You: ").strip()
                if not user_input:
                    continue
                match user_input.lower():
                    case "exit" | "quit":
                        print("Goodbye!")
                        break
                    case "clear":
                        self.history = []
                        print("🗑️  Memory cleared.")
                    case "history":
                        print(json.dumps(self.history, indent=2, default=str))
                    case _:
                        print("\n🤖 Agent: ", end="", flush=True)
                        self.chat(user_input)
            except KeyboardInterrupt:
                print("\n  (Use 'exit' to quit cleanly)")
            except anthropic.APIStatusError as e:
                print(f"\n❌ API error {e.status_code}: {e.message}")
            except Exception as e:
                print(f"\n❌ {type(e).__name__}: {e}")