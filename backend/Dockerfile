# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py ./
COPY assets/ ./assets/

# Local reports directory (Cloud Run is ephemeral; GCS is source of truth)
RUN mkdir -p /app/reports

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV REPORT_LOCAL_DIR=/app/reports

# Expose port
EXPOSE 8080

# Run the application with gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app
