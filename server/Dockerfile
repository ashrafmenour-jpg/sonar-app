# Sonar server: the Sonar web app running 24/7 in headless Chromium + watchdog + dashboard
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 TZ=Africa/Cairo
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tzdata fonts-noto-core fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.49.1 && playwright install --with-deps chromium
WORKDIR /app
COPY runner.py smoke_test.py ./
ARG REPO_URL=https://github.com/ashrafmenour-jpg/sonar-app.git
RUN git clone --depth 50 ${REPO_URL} /app/repo
VOLUME ["/data"]
EXPOSE 8090
HEALTHCHECK --interval=2m --timeout=10s --start-period=5m CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=8).status==200 else 1)"
CMD ["python", "runner.py"]
