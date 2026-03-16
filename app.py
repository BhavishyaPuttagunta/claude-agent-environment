import streamlit as st
from agents.demo_agent import DemoAgent

# Page config
st.set_page_config(
    page_title="Claude AI Assistant",
    page_icon="🤖",
    layout="centered"
)

# Header
st.title("🤖 Claude AI Assistant")
st.markdown("*Powered by Anthropic's Claude — Built on a reusable Agent Environment*")
st.divider()

# Initialize agent in session
if "agent" not in st.session_state:
    st.session_state.agent = DemoAgent()

if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Ask me anything..."):
    # Show user message
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Get and show agent response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            response = st.session_state.agent.chat(prompt)
        st.markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})

# Sidebar
with st.sidebar:
    st.header("ℹ️ About")
    st.markdown("""
    This is a **Claude-powered AI Agent** built on a reusable agent environment.
    
    **Built with:**
    - Python
    - Anthropic Claude API
    - Streamlit
    
    **Capabilities:**
    - Natural language conversation
    - Context-aware responses
    - Extensible for any use case
    """)

    st.divider()

    if st.button("🔄 Reset Conversation"):
        st.session_state.agent.reset()
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.markdown("*Ready to connect to live Claude API*")


