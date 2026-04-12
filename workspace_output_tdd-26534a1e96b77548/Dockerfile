FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure Python can find our source modules
ENV PYTHONPATH=/app/src

# Run the FastAPI app with Uvicorn
CMD ["uvicorn", "core.app:app", "--host", "0.0.0.0", "--port", "8000"]