# Throwaway image for M0 spikes: Python 3.12 plus the sponsor SDKs at pinned versions.
FROM python:3.12-slim
RUN pip install --no-cache-dir \
    httpx==0.28.1 \
    pydantic==2.11.7 \
    sentry-sdk==2.73.0 \
    auth0-ai==1.0.2 \
    backboard-sdk==1.5.19 \
    temporalio==1.34.0 \
    browserbase==1.21.0 \
    serpapi==1.1.2
WORKDIR /w
