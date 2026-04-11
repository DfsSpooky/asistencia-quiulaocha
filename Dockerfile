# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV TZ=America/Lima

# Set work directory
WORKDIR /app

# Install system dependencies
# AGREGAMOS: libpango, libharfbuzz, libopenjp2, libffi para WeasyPrint
# tzdata para zona horaria
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    libcairo2-dev \
    pkg-config \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libjpeg-dev \
    libopenjp2-7-dev \
    libffi-dev \
    python3-dev \
    tzdata \
    postgresql-client \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project
COPY . /app/

# Expose port
EXPOSE 8020

# Command to run the application
CMD ["gunicorn", "qr_asistencia.wsgi:application", "--bind", "0.0.0.0:8020"]
