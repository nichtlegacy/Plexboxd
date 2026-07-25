FROM python:3.12-slim

# Chromium is driven by Patchright (Node) for the Letterboxd login that mints the
# Cloudflare clearance cookie. Xvfb is required because headless Chrome gets
# challenged on letterboxd.com while headful does not.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium xvfb xauth x11-utils fonts-liberation nodejs npm \
    ca-certificates \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV NODE_PATH=/app/node_modules
ENV LETTERBOXD_BROWSER_PACKAGE=patchright
ENV LETTERBOXD_BROWSER_HEADLESS=false
ENV LETTERBOXD_BROWSER_AUTH=true
ENV LETTERBOXD_BROWSER_FALLBACK=true
ENV LETTERBOXD_MAX_RETRIES=3
ENV LETTERBOXD_BASE_BACKOFF_SECONDS=5.0

WORKDIR /app

COPY requirements.txt .
COPY package.json package-lock.json ./
# setuptools first: provides distutils for Python 3.12+
RUN pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir -r requirements.txt
# Use the system Chromium rather than downloading Patchright's own build.
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --omit=dev

COPY . .

RUN chmod +x /app/entrypoint.sh

WORKDIR /app/src
CMD ["/app/entrypoint.sh"]
