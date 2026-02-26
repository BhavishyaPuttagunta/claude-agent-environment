class DemoAgent:
    def __init__(self):
        self.conversation_history = []
        self.responses = {
            "hello": "Hello! I'm a Claude-powered AI assistant. I can help you with questions, analysis, writing, and much more. What would you like to explore today?",
            "what can you do": "I can help you with a wide range of tasks including:\n\n- Answering questions on any topic\n- Analyzing and summarizing documents\n- Writing and editing content\n- Data analysis and interpretation\n- Problem solving and brainstorming\n- Code explanation and debugging\n\nWhat would you like help with?",
            "who are you": "I am an AI assistant built on top of Claude by Anthropic. I've been set up as part of a Claude Agent Environment — a reusable foundation for building intelligent AI agents tailored to specific business needs.",
            "what is data quality": "Data quality refers to the accuracy, completeness, consistency, and reliability of data. High quality data is:\n\n- **Accurate** — correctly represents real-world values\n- **Complete** — no missing values\n- **Consistent** — uniform across systems\n- **Timely** — up to date\n- **Valid** — follows defined formats and rules\n\nPoor data quality can lead to bad business decisions, compliance issues, and operational inefficiencies.",
            "what is an ai agent": "An AI agent is a system that uses a large language model like Claude as its brain to:\n\n- Understand instructions in plain language\n- Reason through complex problems\n- Take actions using tools (like querying databases or calling APIs)\n- Remember context across a conversation\n- Deliver results autonomously\n\nUnlike a simple chatbot, an agent can perform multi-step tasks and adapt based on what it finds.",
            "default": "That's a great question! As a Claude-powered agent, I'm designed to assist with a wide range of tasks. Once connected to the live Claude API, I'll be able to give you fully dynamic, intelligent responses tailored exactly to your question. Is there anything else you'd like to explore?"
        }

    def chat(self, user_message: str) -> str:
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Find best matching response
        message_lower = user_message.lower()
        response = self.responses["default"]
        for key in self.responses:
            if key in message_lower:
                response = self.responses[key]
                break

        self.conversation_history.append({
            "role": "assistant",
            "content": response
        })

        return response

    def reset(self):
        self.conversation_history = []