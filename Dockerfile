# Run the whole pipeline in a container:
#   docker build -t spendsentinel .
#   docker run --rm -v "$PWD/out:/app/_site" spendsentinel all
#   docker run --rm -v "$PWD/out:/app/_site" spendsentinel site
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY sql ./sql
COPY config ./config
COPY reports ./reports
RUN pip install -e .

# Downloads, the database and generated output live here; mount volumes to keep them.
VOLUME ["/app/data", "/app/exports", "/app/_site"]

ENTRYPOINT ["spendsentinel"]
CMD ["all"]
