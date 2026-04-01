"""
base_agent.py

Abstract Agent class + Tool registry.
All specialist agents inherit from BaseAgent.

Pattern: Plan → Execute (tool call) → Observe → Respond (max MAX_STEPS)

v3.1 additions:
  - Retry logic with exponential backoff for LLM calls
  - Configurable timeout per call
  - Structured error wrapper so callers always get a dict back
"""
import time
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


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
    MAX_RETRIES = 3          # maximum LLM call retries
    RETRY_BACKOFF = 1.5      # seconds multiplier between retries
    LLM_TIMEOUT = 60         # seconds before a single LLM call is abandoned

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

    def _call_with_retry(self, fn: Callable, *args, **kwargs) -> Any:
        """
        Call fn(*args, **kwargs) with exponential backoff on transient errors.
        Raises the last exception if all retries are exhausted.

        Retries on: RateLimitError, APIConnectionError, Timeout.
        Does NOT retry on: AuthenticationError, InvalidRequestError.
        """
        import openai

        retryable = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        )
        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except retryable as e:
                last_exc = e
                wait = self.RETRY_BACKOFF * (2 ** attempt)
                self.logger.warning(
                    "%s: retryable error (attempt %d/%d) — waiting %.1fs: %s",
                    self.__class__.__name__, attempt + 1, self.MAX_RETRIES, wait, e,
                )
                time.sleep(wait)
            except Exception as e:
                # Non-retryable — raise immediately
                raise
        raise last_exc  # type: ignore[misc]

    def _safe_run(self, input: Any, context: Optional[Dict] = None) -> Dict:
        """
        Wraps run() in a try/except so callers always receive a structured dict.
        Use this in schedulers and background tasks where unhandled exceptions
        would silently swallow errors.
        """
        try:
            return self.run(input, context)
        except Exception as e:
            self.logger.error("%s.run() failed: %s", self.__class__.__name__, e)
            return {
                "error": True,
                "agent": self.__class__.__name__,
                "message": str(e),
            }

    @abstractmethod
    def run(self, input: Any, context: Optional[Dict] = None) -> Dict:
        """Execute the agent. Must return a structured dict result."""
        pass
