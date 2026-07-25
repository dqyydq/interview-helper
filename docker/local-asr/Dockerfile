FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPYCACHEPREFIX=/tmp/pycache \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/local-asr \
    HF_HUB_OFFLINE=1 \
    MODELSCOPE_OFFLINE=1 \
    MODELSCOPE_HOME=/tmp/modelscope \
    LOCAL_ASR_DEVICE=cpu \
    LOCAL_ASR_MODEL_ID=sensevoice-small \
    LOCAL_ASR_MODEL_DIR=/models/installs/sensevoice-small/43d0ed61231c41f8393fa347b838a1f6e2d264f6 \
    LOCAL_ASR_ACTIVE_MARKER=/state/active/sensevoice-small.json \
    LOCAL_ASR_MODEL_REVISION=43d0ed61231c41f8393fa347b838a1f6e2d264f6 \
    LOCAL_ASR_MAX_AUDIO_BYTES=33554432 \
    LOCAL_ASR_MAX_AUDIO_SECONDS=90 \
    LOCAL_ASR_MAX_CONCURRENT_REQUESTS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 local-asr \
    && useradd --uid 10001 --gid local-asr --create-home --home-dir /tmp/local-asr --shell /usr/sbin/nologin local-asr

COPY requirements.lock /app/requirements.lock
RUN python -m pip install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --requirement /app/requirements.lock

COPY app.py /app/app.py
RUN chown local-asr:local-asr /app/app.py

USER local-asr

EXPOSE 8011

ENTRYPOINT ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
