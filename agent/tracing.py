"""OpenTelemetry tracing for Token Cop.

Adds custom spans around provider tool calls so we can monitor:
- Latency per provider API call
- Error rates per provider
- Token counts flowing through the agent

When deployed to AgentCore, the platform sets OTEL env vars but does NOT
run `opentelemetry-instrument` (it overrides the Dockerfile CMD). We
manually trigger the ADOT configurator to set up the TracerProvider so
Strands GenAI spans flow through ADOT -> X-Ray -> CloudWatch aws/spans.

For local dev, traces export to console if OTEL_TRACES_EXPORTER=console.
"""
import functools
import json
import logging
import os
from typing import Callable

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource

TRACER_NAME = "token-cop"
_initialized = False
_logger = logging.getLogger("bedrock_agentcore.app")


class _SessionIdSpanProcessor(SpanProcessor):
    """Injects session.id into every span from BedrockAgentCoreContext.

    The evaluation API queries aws/spans filtering on attributes.session.id.
    Strands spans don't include it natively, so this processor reads the
    session ID from the AgentCore runtime context on each span start.
    """

    def on_start(self, span, parent_context=None):
        try:
            from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
            sid = BedrockAgentCoreContext.get_session_id()
            if sid and span.is_recording():
                span.set_attribute("session.id", sid)
        except Exception:
            pass

    def on_end(self, span):
        pass


def _configure_adot():
    """Configure the ADOT TracerProvider for AgentCore deployment.

    The platform sets OTEL_PYTHON_DISTRO=aws_distro but doesn't run
    opentelemetry-instrument and omits key env vars. We fill in the gaps
    and run the AwsOpenTelemetryConfigurator manually.

    Returns True if configuration succeeded.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")

    # Enable agent observability so the LLO handler sends GenAI content
    # (prompt, completion, tool calls) to the logs pipeline. The online
    # evaluation reads from the otel-rt-logs stream in the runtime log group.
    os.environ.setdefault("AGENT_OBSERVABILITY_ENABLED", "true")

    # Fill in missing env vars the platform doesn't set
    os.environ.setdefault("OTEL_TRACES_EXPORTER", "otlp")
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        f"https://xray.{region}.amazonaws.com/v1/traces",
    )
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    # Logs exporter — LLO handler sends GenAI content here for online eval.
    # OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED must be true for
    # the configurator to set up the LoggerProvider.
    os.environ.setdefault("OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED", "true")
    os.environ.setdefault("OTEL_LOGS_EXPORTER", "otlp")
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        f"https://logs.{region}.amazonaws.com/v1/logs",
    )
    os.environ.setdefault("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "http/protobuf")

    from amazon.opentelemetry.distro.aws_opentelemetry_configurator import (
        AwsOpenTelemetryConfigurator,
    )

    AwsOpenTelemetryConfigurator().configure(auto_instrumentation_version="0.0")

    # Add session.id injection
    tp = trace.get_tracer_provider()
    if hasattr(tp, "add_span_processor"):
        tp.add_span_processor(_SessionIdSpanProcessor())

    _logger.info("ADOT configured: TracerProvider=%s", type(tp).__name__)
    return True


def init_tracing():
    """Initialize OpenTelemetry tracing.

    Priority order:
    1. If an SDK TracerProvider already exists (e.g. opentelemetry-instrument
       ran), use it as-is.
    2. If OTEL_PYTHON_DISTRO=aws_distro (AgentCore), run the ADOT configurator
       manually with X-Ray OTLP export.
    3. Otherwise, set up a basic provider for local development.
    """
    global _initialized
    if _initialized:
        return

    # 1. Check if an SDK TracerProvider is already configured
    provider = trace.get_tracer_provider()
    if hasattr(provider, "add_span_processor"):
        _initialized = True
        return

    # 2. AgentCore deployment — configure ADOT manually
    if os.environ.get("OTEL_PYTHON_DISTRO") == "aws_distro":
        try:
            _configure_adot()
            _initialized = True
            return
        except Exception as e:
            _logger.warning("ADOT configuration failed: %s", e)

    # 3. Local development fallback
    resource = Resource.create({"service.name": "token-cop"})
    local_provider = TracerProvider(resource=resource)

    if os.environ.get("OTEL_TRACES_EXPORTER") == "console":
        local_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    try:
        trace.set_tracer_provider(local_provider)
    except Exception:
        pass
    _initialized = True


def get_tracer() -> trace.Tracer:
    """Get the Token Cop tracer."""
    init_tracing()
    return trace.get_tracer(TRACER_NAME)


def traced_tool(provider_name: str) -> Callable:
    """Decorator that wraps a tool function with an OpenTelemetry span.

    Records provider name, date range, token counts, cost, and errors.

    Args:
        provider_name: Name of the provider (e.g., "bedrock", "openrouter").
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(
                f"tool.{provider_name}_usage",
                attributes={
                    "token_cop.provider": provider_name,
                    "token_cop.tool": func.__name__,
                },
            ) as span:
                try:
                    result = func(*args, **kwargs)

                    try:
                        data = json.loads(result)
                        if "error" not in data:
                            span.set_attribute("token_cop.input_tokens", data.get("total_input_tokens", 0))
                            span.set_attribute("token_cop.output_tokens", data.get("total_output_tokens", 0))
                            span.set_attribute("token_cop.total_tokens", data.get("total_tokens", 0))
                            span.set_attribute("token_cop.cost_usd", data.get("total_estimated_cost_usd", 0))
                            span.set_attribute("token_cop.requests", data.get("total_requests", 0))
                        else:
                            span.set_attribute("token_cop.error", data["error"])
                    except (json.JSONDecodeError, TypeError):
                        pass

                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as e:
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator
