FROM python:3.11-slim

LABEL maintainer="Anvil P-02 Submission"
LABEL description="Persistent Context Engine for AI SRE - Reproducible Evaluation"

# No external dependencies - pure Python stdlib
WORKDIR /app

# Copy all source files
COPY adapter.py schema.py generator.py harness.py metrics.py run.py self_check.py ./
COPY adapters/ ./adapters/
COPY bench/ ./bench/
COPY validate_worked_example.py ./

# Make bench scripts executable
RUN chmod +x bench/run.sh

# Default: run the full benchmark
ENTRYPOINT ["python", "self_check.py", "--adapter", "adapters.engine:Engine"]
CMD []
