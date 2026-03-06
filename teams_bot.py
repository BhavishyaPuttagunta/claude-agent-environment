"""
teams_bot.py
FDA Regulatory Intelligence Agent — Microsoft Teams Bot

Architecture:
    Teams user sends message
        → Azure Bot Service forwards to this server (POST /api/messages)
        → teams_bot.py passes message to FDAgent
        → FDAgent streams response (with tool calls)
        → Bot sends reply back to Teams

Setup:
    1. Register bot at https://dev.botframework.com
    2. Add MicrosoftAppId + MicrosoftAppPassword to .env
    3. Deploy this server to Render.com
    4. Set the bot's messaging endpoint to: https://your-app.onrender.com/api/messages
    5. Add the bot to your Teams tenant (see TEAMS_SETUP.md)
"""

import os
import asyncio
from http import HTTPStatus

from dotenv import load_dotenv
from aiohttp import web
from botbuilder.core import (
    BotFrameworkAdapterSettings,
    BotFrameworkAdapter,
    TurnContext,
)
from botbuilder.schema import Activity

from agents.agent import FDAgent

load_dotenv()

# ── Bot credentials (from .env) ───────────────────────────────────────────────
APP_ID       = os.getenv("MicrosoftAppId", "")
APP_PASSWORD = os.getenv("MicrosoftAppPassword", "")

SETTINGS = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
ADAPTER  = BotFrameworkAdapter(SETTINGS)

# ── One agent per user — keeps conversation memory per Teams user ─────────────
_agents: dict[str, FDAgent] = {}

def _get_agent(user_id: str) -> FDAgent:
    if user_id not in _agents:
        _agents[user_id] = FDAgent()
    return _agents[user_id]


# ── Error handler ─────────────────────────────────────────────────────────────
async def on_error(context: TurnContext, error: Exception):
    print(f"[TeamsBot] Unhandled error: {error}")
    await context.send_activity("Sorry, something went wrong. Please try again.")

ADAPTER.on_turn_error = on_error


# ── Main message handler ──────────────────────────────────────────────────────
async def _handle_message(turn_context: TurnContext):
    user_text = (turn_context.activity.text or "").strip()

    # Strip @mention prefix Teams sometimes adds (e.g. "<at>FDAAgent</at> fetch...")
    if "<at>" in user_text:
        import re
        user_text = re.sub(r"<at>[^<]*</at>\s*", "", user_text).strip()

    if not user_text:
        await turn_context.send_activity(
            "Hi! I'm your FDA Regulatory Intelligence Agent. "
            "Ask me to fetch, monitor, or explain any FDA regulation. "
            "For example: *fetch 21 CFR Part 117 and summarise it*"
        )
        return

    user_id = turn_context.activity.from_property.id
    agent   = _get_agent(user_id)

    # Show typing indicator while agent works
    await turn_context.send_activity(Activity(type="typing"))

    # Run the agent — collect streamed output into a single reply
    # FDAgent.chat() prints to stdout; we capture the final response
    response_text = await asyncio.get_event_loop().run_in_executor(
        None, _run_agent_sync, agent, user_text
    )

    # Teams has a 28kb message limit — chunk if needed
    if len(response_text) > 25_000:
        chunks = [response_text[i:i+25_000] for i in range(0, len(response_text), 25_000)]
        for i, chunk in enumerate(chunks):
            prefix = f"*(part {i+1}/{len(chunks)})*\n\n" if len(chunks) > 1 else ""
            await turn_context.send_activity(prefix + chunk)
    else:
        await turn_context.send_activity(response_text)


def _run_agent_sync(agent: FDAgent, user_text: str) -> str:
    """
    Run the agent synchronously and capture the response text.
    FDAgent streams to stdout — we intercept the final assistant message.
    """
    import io, sys

    # Capture stdout (the streamed tokens)
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    try:
        agent.chat(user_text)
    except Exception as e:
        sys.stdout = old_stdout
        return f"Error running agent: {e}"
    finally:
        sys.stdout = old_stdout

    output = buffer.getvalue()

    # The agent prints tool calls + the final response mixed together.
    # Extract only the final assistant text (lines not starting with tool markers).
    lines = output.splitlines()
    response_lines = []
    skip_prefixes = ("⚙️", "↳", "🕷️", "⚠️", "[loop", "[deep_scrape", "  ")
    for line in lines:
        stripped = line.strip()
        if stripped and not any(stripped.startswith(p) for p in skip_prefixes):
            response_lines.append(line)

    return "\n".join(response_lines).strip() or "Done."


# ── HTTP server ───────────────────────────────────────────────────────────────
async def messages(req: web.Request) -> web.Response:
    if req.content_type != "application/json":
        return web.Response(status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    body    = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    try:
        await ADAPTER.process_activity(activity, auth_header, _handle_message)
        return web.Response(status=HTTPStatus.OK)
    except Exception as e:
        print(f"[TeamsBot] process_activity error: {e}")
        return web.Response(status=HTTPStatus.INTERNAL_SERVER_ERROR)


async def health(req: web.Request) -> web.Response:
    """Health check endpoint — Render.com pings this to keep the app alive."""
    return web.Response(text='{"status":"ok","bot":"FDA Regulatory Agent"}',
                        content_type="application/json")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", 3978))
    print(f"[TeamsBot] Starting on port {port}")
    print(f"[TeamsBot] Messaging endpoint: http://localhost:{port}/api/messages")
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=port)