"""
base_agent.py

Abstract Agent class + Tool registry.
All specialist agents inherit from BaseAgent.

Pattern: Plan → Execute (tool call) → Observe → Respond (max MAX_STEPS)
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable
import json
import logging


class Tool:
    """Wraps any Python function as an agent-callable tool."""

    def __init__(self, name: str, description: str, fn: Callable, schema: dict):
        self.name = name
        self.description = description
        self.fn = fn
        self.schema = schema  # JSON Schema for parameters

    def call(self, **kwargs) -> Any:
        return self.fn(**kwargs)


class BaseAgent(ABC):
    """
    Abstract base for all SourceIQ agents.

    Subclasses:
      1. Define tools in __init__ and pass to super().__init__(tools)
      2. Implement run(input, context) -> dict

    Tool schemas follow OpenAI function-calling format so any subclass
    can be exposed directly to an LLM via _tool_schemas().
    """

    MAX_STEPS = 5

    def __init__(self, tools: Optional[List[Tool]] = None):
        self.tools: Dict[str, Tool] = {t.name: t for t in (tools or [])}
        self.logger = logging.getLogger(self.__class__.__name__)

    def register_tool(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def _tool_schemas(self) -> List[dict]:
        """Return all registered tools in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in self.tools.values()
        ]

    @abstractmethod
    def run(self, input: Any, context: Optional[Dict] = None) -> Dict:
        """Execute the agent. Must return a structured dict result."""
        pass
