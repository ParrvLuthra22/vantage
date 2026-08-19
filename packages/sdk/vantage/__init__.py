"""Vantage — instrumentation for LLM agent applications."""

from vantage.client import VantageClient, get_client, init
from vantage.decorators import span, trace
from vantage.models import SpanCreate

__version__ = "0.1.0"

__all__ = [
    "VantageClient",
    "init",
    "get_client",
    "span",
    "trace",
    "SpanCreate",
    "__version__",
]
