FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp/model-loader \
    MODELSCOPE_HOME=/tmp/modelscope-home \
    MODELSCOPE_CACHE=/var/lib/model-loader/cache

WORKDIR /app

RUN groupadd --gid 10001 model-loader \
    && useradd --uid 10001 --gid model-loader --create-home --home-dir /tmp/model-loader --shell /usr/sbin/nologin model-loader \
    && install --directory --owner=model-loader --group=model-loader --mode=0750 /models /state /offline /var/lib/model-loader/cache \
    && touch /models/.volume-owner /state/.volume-owner \
    && chown model-loader:model-loader /models/.volume-owner /state/.volume-owner

COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --requirement /app/requirements.lock

COPY entrypoint.py /app/entrypoint.py
RUN chown model-loader:model-loader /app/entrypoint.py

USER model-loader

ENTRYPOINT ["python", "/app/entrypoint.py"]
