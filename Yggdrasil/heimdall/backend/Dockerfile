# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# System dependencies for cryptography — rarely change, so keep this layer
# above the code copy for maximum build-cache reuse.
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached unless requirements.txt changes) so a plain
# code edit doesn't trigger a full pip reinstall on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy the application code (see .dockerignore — secrets, caches, backups
# and __pycache__ are excluded from the image).
COPY . /app

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Run app.py when the container launches
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
