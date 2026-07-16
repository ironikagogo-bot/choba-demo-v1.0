# 帳場 クラウド配信用イメージ(Hugging Face Spaces / Render / Railway / Fly いずれも可)
FROM python:3.12-slim

WORKDIR /app

# http-ece(pywebpush依存)のビルド失敗を避ける
ENV SETUPTOOLS_USE_DISTUTILS=stdlib \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 見せる用のダミー顧客を自動投入(実顧客データはクラウドに載せない)
ENV CHOUBA_DEMO=1

# HF Spaces は 7860 を期待。Render/Railway/Fly は $PORT を渡してくるのでそれを優先。
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
