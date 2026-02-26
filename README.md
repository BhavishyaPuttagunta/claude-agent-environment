# Claude Agent Environment

A ready-to-use environment for building Claude-powered AI agents.

## Setup Instructions

1. Clone this repository
2. Create a virtual environment: `python -m venv venv`
3. Activate it: `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Mac/Linux)
4. Install dependencies: `pip install -r requirements.txt`
5. Create a `.env` file and add your API key: `ANTHROPIC_API_KEY=your-key`
6. Run: `python main.py`

## Project Structure

- `agents/` — Agent definitions
- `tools/` — Tools agents can use
- `utils/` — Helper utilities
- `config/` — Configuration and settings

## Requirements

- Python 3.10+
- Anthropic API key