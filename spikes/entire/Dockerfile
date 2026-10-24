# Isolated container for the Entire spike: the CLI comes from Entire's official Linux install script.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://entire.io/install.sh | bash
ENV PATH="/root/.local/bin:/root/.entire/bin:/usr/local/bin:${PATH}"
COPY entire-agent-vendorwake /usr/local/bin/entire-agent-vendorwake
COPY run_session.sh /usr/local/bin/run_session.sh
RUN chmod +x /usr/local/bin/entire-agent-vendorwake /usr/local/bin/run_session.sh && (entire version || true)
CMD ["run_session.sh"]
