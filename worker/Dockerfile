FROM python:3.11-slim

# Install system dependencies including Docker CLI
RUN apt-get update && apt-get install -y \
    curl \
    nodejs \
    npm \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install OpenCode CLI
RUN npm install -g opencode-ai@latest

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "consumer.py"]
