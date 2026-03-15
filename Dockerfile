FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create dirs the app writes to
RUN mkdir -p config logs results

# Non-root user
RUN useradd -m -u 1001 mailmind && chown -R mailmind /app
USER mailmind

# Webhook port
EXPOSE 8000

# Default: run both agent + webhook server
CMD ["python", "main.py"]
