FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy project
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 8000

# Default command
CMD ["uvicorn", "src.config.app:create_app", "--host", "0.0.0.0", "--port", "8000"]