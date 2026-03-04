"""
agents/agent.py
FDA Regulatory Intelligence Agent — multi-turn memory, streaming, tool use, SQLite versioning
"""

import anthropic
import json
from tools.tools import TOOL_DEFINITIONS, execute_tool
from config.config import MODEL, MAX_TOKENS

SYSTEM_PROMPT = """You are an FDA Regulatory Intelligence Agent. You monitor, track, and explain FDA regulations using a SQLite knowledge base.

## Tools & When to Use Them

| Task                                  | Tool                  |
|---------------------------------------|-----------------------|
| Fetch any 21 CFR regulation           | fetch_ecfr            |
| Check when a CFR part was amended     | fetch_ecfr_versions   |
| Fetch fda.gov / federalregister.gov   | scrape_url            |
| Save after every fetch                | save_regulation       |
| Read a saved regulation               | read_regulation       |
| Browse saved regulations              | list_regulations      |
| See what changed between two versions | compare_versions      |
| Review change history / audit log     | check_changes         |

## Strict Rules
1. After EVERY fetch_ecfr or scrape_url — immediately call save_regulation, passing the COMPLETE return text as the content field
2. CRITICAL: When calling save_regulation, the content field MUST be the full raw text returned by fetch_ecfr/scrape_url — never omit it, never summarize it
3. Before answering about a regulation — call list_regulations first, then read_regulation if it exists, else fetch it
4. When save_regulation returns ⚠️ CHANGED — immediately run compare_versions and summarise what changed
5. Always cite specific section numbers (§820.30 not just "design controls")
6. Always state the fetch date AND archive date so users know how current the data is
7. fetch_ecfr auto-retries with older dates if today returns 404 — always let it run fully before concluding a part is missing
8. If fetch_ecfr fails completely, try scrape_url on www.ecfr.gov as a fallback

## Key Regulatory Context (as of 2026)
- 21 CFR Part 820 (QSR) was REPLACED by QMSR effective February 2, 2026
  The new QMSR is still numbered Part 820 but now incorporates ISO 13485:2016 by reference
  fetch_ecfr will automatically find the correct archive date
- 21 CFR Part 11 covers Electronic Records and Electronic Signatures (unchanged)
- 21 CFR Part 210/211 covers drug cGMP (unchanged)
- 21 CFR Part 803 covers Medical Device Reporting (MDR)

## When fetch_ecfr Returns an Error
1. Try fetch_ecfr_versions first — confirms if the part exists and when it was last amended
2. Try scrape_url on https://www.ecfr.gov/current/title-{N}/part-{N} as fallback
3. Try scrape_url on https://www.fda.gov for guidance documents on that topic
4. Always save whatever content you retrieve — partial content is better than nothing

## Response Style
- Lead with the key finding
- Flag ⚠️ CHANGED items as requiring compliance team review  
- Be specific and concise — compliance teams are busy
- If using archived content, always note the archive date prominently
"""


class FDAgent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.history = []
        print("\n🏥  FDA Regulatory Intelligence Agent")
        print("=" * 50)
        print("Storage: SQLite (fda_knowledge.db)")
        print("Commands: 'exit' | 'clear' | 'history'")
        print("\nExamples:")
        print("  • Fetch 21 CFR Part 820 and save it")
        print("  • What are the requirements in 21 CFR Part 11?")
        print("  • Has 21 CFR Part 820 changed since last fetch?")
        print("  • Show me the change log\n")

    def chat(self, user_message: str) -> None:
        self.history.append({"role": "user", "content": user_message})

        MAX_ITERATIONS = 10  # prevent infinite tool-call loops

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

            # No tool calls — agent is done, return normally
            if not tool_calls:
                print()
                self.history.append({"role": "assistant", "content": final.content})
                return

            # Execute tools and feed results back
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

        # Reached iteration limit — stop gracefully
        print(f"\n⚠️  Reached max iterations ({MAX_ITERATIONS}). Stopping to prevent infinite loop.")
        print("    Try rephrasing your request or use 'clear' to reset memory.")

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