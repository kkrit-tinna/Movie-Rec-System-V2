# Batch image for T4.3: one image for local runs and Fargate (§3).
# arm64: built natively on Apple Silicon, run on ARM64 (Graviton) Fargate.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, read straight from pyproject.toml, so this layer is
# rebuilt only when the dependency list changes, not on every code edit.
COPY pyproject.toml .
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Source, config and the committed sample. Editable install so modules keep
# resolving config/ as <repo>/config, i.e. /app/config -- same as on a laptop.
COPY src/ src/
COPY config/ config/
COPY data/sample/ data/sample/
RUN pip install --no-deps -e .

# Non-root. The pipeline writes only artifacts/ (LocalStore) and data/raw/
# (Kaggle download), so only those are owned by the runtime user.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p artifacts data/raw \
    && chown app:app artifacts data/raw
USER app

# ECS has no task timeout; a hung run would bill until stopped. timeout
# sends SIGTERM at 45 min and exits 124. A full run takes ~20 min (§2).
ENTRYPOINT ["timeout", "45m", "python", "-m", "movierec.pipeline"]
