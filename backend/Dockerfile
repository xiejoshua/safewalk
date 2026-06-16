FROM python:3.12-slim

WORKDIR /app

# GeoPandas runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-dev \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# Build context must be the repo root so we can bake the parquet from outputs/.
#   docker build -f backend/Dockerfile -t safewalk-api .

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ app/

# Pre-baked scored network. Generate with `python scripts/prebake.py` before
# building. If outputs/scored_segments.parquet is missing the build will fail
# loud — that's intentional, since boot would otherwise serve an empty network.
COPY outputs/scored_segments.parquet data/scored_segments.parquet

ENV SCORED_SEGMENTS_PATH=data/scored_segments.parquet

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
