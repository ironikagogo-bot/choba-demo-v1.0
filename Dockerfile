FROM python:3.12-slim
WORKDIR /app
ENV SETUPTOOLS_USE_DISTUTILS=stdlib PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV CHOUBA_DEMO=1 CHOUBA_WATCHER=sim
EXPOSE 7860
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
