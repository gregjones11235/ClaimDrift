"""ADK entry point (`adk web agents` / Agent Engine); the logic is claimdrift.agent_handlers.supervisor."""
from ..common import build

root_agent = build("supervisor")
