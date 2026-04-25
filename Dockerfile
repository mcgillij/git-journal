FROM python:3.12-slim

# Install git (needed by GitPython) and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    gource \
    ffmpeg \
    xvfb \
    xauth \
    && rm -rf /var/lib/apt/lists/*

# Trust all directories for git (needed when mounting host repos into container)
RUN git config --global --add safe.directory '*'

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directories for volumes
RUN mkdir -p /app/data /app/repos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
