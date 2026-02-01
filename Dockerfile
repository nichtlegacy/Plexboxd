FROM python:3.12-slim

# Abhängigkeiten installieren
RUN apt-get update && apt-get install -y \
    chromium-driver chromium xvfb \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROME_DRIVER=/usr/bin/chromedriver

# Arbeitsverzeichnis
WORKDIR /app

# requirements.txt kopieren und installieren
COPY requirements.txt .
# Install setuptools first (provides distutils for Python 3.12+)
RUN pip install --no-cache-dir setuptools
RUN pip install --no-cache-dir -r requirements.txt

# Kopieren des gesamten Projektinhalts
COPY . .

# Startbefehl
CMD ["xvfb-run", "--auto-servernum", "--server-num=1", "--server-args=-screen 0 1920x1080x24", "python", "src/plex_bot.py"]
