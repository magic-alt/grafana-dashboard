FROM docker:29-cli AS docker-cli

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# This image is intentionally local-lab only because the LEAN controller needs access to
# a Docker daemon. Cloud deployments must run LEAN as a separate workload instead of
# mounting a host Docker socket into a public service.
CMD ["python", "lean_observability_control.py"]
