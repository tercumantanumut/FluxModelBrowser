# Use the official Python slim image.
FROM python:3.10-slim

# Set environment variables.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
	HUGGINGFACE_TOKEN=


# Create a working directory.
WORKDIR /app

# Copy requirements file first to leverage Docker cache.
COPY requirements.txt .

# Install dependencies.
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code.
COPY . .

# Expose port 8080.
EXPOSE 8080

# Run the app using gunicorn.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]