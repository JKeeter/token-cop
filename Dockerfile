FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables for OTEL auto-instrumentation
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV AGENT_OBSERVABILITY_ENABLED=true
ENV PYTHONPATH=/app
ENV AWS_REGION=us-east-1
ENV AWS_DEFAULT_REGION=us-east-1

# AgentCore provides ADOT sidecar for tracing
CMD ["python", "-m", "agent.app"]
