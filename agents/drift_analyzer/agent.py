"""ADK entry point (`adk web agents` / Agent Engine); the logic is claimdrift.agent_handlers.drift_analyzer."""
from ..common import build

root_agent = build("drift_analyzer")
