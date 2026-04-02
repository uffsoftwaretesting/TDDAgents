FROM python:3.10-slim as builder
# COPY --from=builder
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
# uvicorn

FROM python:3.10-slim