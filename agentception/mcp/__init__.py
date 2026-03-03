"""AgentCeption MCP package.

Provides Model Context Protocol (JSON-RPC 2.0) tool definitions and
dispatchers for the plan-step-v2 pipeline.  All tools are pure async
functions that operate within the ``agentception/`` boundary — zero imports
from maestro, muse, kly, or storpheus.
"""
from __future__ import annotations
