from agents.base_agent import BaseAgent

# Instantiate a simple agent
agent = BaseAgent(
    system_prompt="You are a helpful assistant. Answer clearly and concisely."
)

print("Agent environment is running. Type 'quit' to exit.\n")

while True:
    user_input = input("You: ")
    if user_input.lower() == "quit":
        break
    response = agent.chat(user_input)
    print(f"Agent: {response}\n")