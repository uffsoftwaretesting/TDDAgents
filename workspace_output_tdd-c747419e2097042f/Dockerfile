# Builder stage
FROM python:3.9-slim AS builder
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM builder AS production
WORKDIR /app
# Copy application code
COPY . /app

# Default command for running the API with hot-reload
CMD ["uvicorn", "src.presentation.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]