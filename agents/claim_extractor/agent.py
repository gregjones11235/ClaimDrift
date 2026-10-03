"""ADK entry point (`adk web agents` / Agent Engine); the logic is claimdrift.agent_handlers.claim_extractor."""
from ..common import build

root_agent = build("claim_extractor")
