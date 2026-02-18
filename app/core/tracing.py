"""
Request Tracing for Stori Maestro (Cursor-of-DAWs).

Provides correlation IDs and structured logging for debugging production issues.

Every request gets a trace_id that propagates through:
- Intent classification
- LLM calls
- Tool validation
- Plan execution
- Event emission

Usage:
    from app.core.tracing import get_trace_context, trace_span
    
    ctx = get_trace_context()
    with trace_span(ctx, "intent_classification") as span:
        span.set_attribute("prompt_length", len(prompt))
        result = classify_intent(prompt)
        span.set_attribute("intent", result.intent.value)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Generator

logger = logging.getLogger(__name__)


class SpanStatus(str, Enum):
    """Status of a trace span."""
    OK = "ok"
    ERROR = "error"


@dataclass
class Span:
    """A single traced operation."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    
    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value
    
    def add_event(self, name: str, attributes: Optional[dict[str, Any]] = None) -> None:
        """Add an event to the span."""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })
    
    def set_error(self, error: Exception) -> None:
        """Mark span as error."""
        self.status = SpanStatus.ERROR
        self.set_attribute("error.type", type(error).__name__)
        self.set_attribute("error.message", str(error))
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Get span duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "attributes": self.attributes,
            "events": self.events,
        }


@dataclass
class TraceContext:
    """Context for a traced request."""
    trace_id: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    spans: list[Span] = field(default_factory=list)
    current_span: Optional[Span] = None
    _span_stack: list[Span] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "spans": [s.to_dict() for s in self.spans],
        }


# Context variable for request-scoped trace context
_trace_context: ContextVar[Optional[TraceContext]] = ContextVar("trace_context", default=None)


def create_trace_context(
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> TraceContext:
    """Create a new trace context for a request."""
    ctx = TraceContext(
        trace_id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
    )
    _trace_context.set(ctx)
    return ctx


def get_trace_context() -> TraceContext:
    """Get current trace context, creating one if needed."""
    ctx = _trace_context.get()
    if ctx is None:
        ctx = create_trace_context()
    return ctx


def get_trace_id() -> str:
    """Get current trace ID."""
    return get_trace_context().trace_id


def clear_trace_context() -> None:
    """Clear trace context (for testing)."""
    _trace_context.set(None)


@contextmanager
def trace_span(
    ctx: TraceContext,
    name: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Generator[Span, None, None]:
    """
    Context manager for tracing a span.
    
    Usage:
        with trace_span(ctx, "intent_classification") as span:
            span.set_attribute("prompt", prompt)
            result = classify(prompt)
            span.set_attribute("intent", result.intent)
    """
    parent_span = ctx.current_span
    span = Span(
        name=name,
        trace_id=ctx.trace_id,
        span_id=str(uuid.uuid4())[:8],
        parent_span_id=parent_span.span_id if parent_span else None,
        start_time=time.time(),
        attributes=attributes or {},
    )
    
    ctx._span_stack.append(span)
    ctx.current_span = span
    ctx.spans.append(span)
    
    try:
        yield span
    except Exception as e:
        span.set_error(e)
        raise
    finally:
        span.end_time = time.time()
        ctx._span_stack.pop()
        ctx.current_span = ctx._span_stack[-1] if ctx._span_stack else None
        
        # Log span completion
        log_span(span)


def log_span(span: Span) -> None:
    """Log a completed span with structured data."""
    log_data = {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "span_name": span.name,
        "duration_ms": span.duration_ms,
        "status": span.status.value,
    }
    log_data.update(span.attributes)
    
    if span.status == SpanStatus.ERROR:
        logger.error(f"[{span.trace_id[:8]}] ✗ {span.name}", extra=log_data)
    else:
        logger.info(f"[{span.trace_id[:8]}] ✓ {span.name} ({span.duration_ms:.0f}ms)", extra=log_data)


# =============================================================================
# Structured Logging Helpers
# =============================================================================

def log_intent(
    trace_id: str,
    prompt: str,
    intent: str,
    confidence: float,
    sse_state: str,
    reasons: tuple[str, ...],
) -> None:
    """Log intent classification result."""
    logger.info(
        f"[{trace_id[:8]}] 🎯 Intent: {intent} → {sse_state}",
        extra={
            "trace_id": trace_id,
            "event": "intent_classified",
            "intent": intent,
            "confidence": confidence,
            "sse_state": sse_state,
            "reasons": list(reasons),
            "prompt_length": len(prompt),
        },
    )


def log_tool_call(
    trace_id: str,
    tool_name: str,
    params: dict[str, Any],
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Log tool call execution."""
    level = logging.INFO if success else logging.WARNING
    status = "✓" if success else "✗"
    
    logger.log(
        level,
        f"[{trace_id[:8]}] {status} Tool: {tool_name}",
        extra={
            "trace_id": trace_id,
            "event": "tool_call",
            "tool_name": tool_name,
            "success": success,
            "error": error,
            "params_keys": list(params.keys()),
        },
    )


def log_llm_call(
    trace_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    duration_ms: float,
    has_tool_calls: bool,
) -> None:
    """Log LLM call."""
    logger.info(
        f"[{trace_id[:8]}] 🤖 LLM: {model} ({prompt_tokens}+{completion_tokens} tokens, {duration_ms:.0f}ms)",
        extra={
            "trace_id": trace_id,
            "event": "llm_call",
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "duration_ms": duration_ms,
            "has_tool_calls": has_tool_calls,
        },
    )


def log_plan_execution(
    trace_id: str,
    total_steps: int,
    successful_steps: int,
    failed_steps: int,
    duration_ms: float,
) -> None:
    """Log plan execution summary."""
    success = failed_steps == 0
    status = "✅" if success else "⚠️"
    
    logger.info(
        f"[{trace_id[:8]}] {status} Plan: {successful_steps}/{total_steps} steps ({duration_ms:.0f}ms)",
        extra={
            "trace_id": trace_id,
            "event": "plan_execution",
            "total_steps": total_steps,
            "successful_steps": successful_steps,
            "failed_steps": failed_steps,
            "duration_ms": duration_ms,
            "success": success,
        },
    )


def log_validation_error(
    trace_id: str,
    tool_name: str,
    errors: list[str],
    suggestions: Optional[list[str]] = None,
) -> None:
    """Log validation error with suggestions."""
    logger.warning(
        f"[{trace_id[:8]}] 🚫 Validation: {tool_name}",
        extra={
            "trace_id": trace_id,
            "event": "validation_error",
            "tool_name": tool_name,
            "errors": errors,
            "suggestions": suggestions or [],
        },
    )
