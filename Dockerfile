FROM nvidia/cuda:12.3.0-devel-ubuntu22.04

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libopenblas-dev \
    wget \
    gnupg2 \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for CUDA
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV PATH=/usr/local/cuda/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY database_manager.py .
COPY websocket_realtime.py .
COPY init.sql .

# Copy client directory
COPY client/ ./client/

# Create necessary directories
RUN mkdir -p examples/xiangxiang_man database logs models

# Expose ports
EXPOSE 7860 7861

# Default command (will be overridden by docker-compose)
CMD ["python", "app.py"]