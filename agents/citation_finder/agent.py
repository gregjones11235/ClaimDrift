"""ADK entry point (`adk web agents` / Agent Engine); the logic is claimdrift.agent_handlers.citation_finder."""
from ..common import build

root_agent = build("citation_finder")
