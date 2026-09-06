FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package as well as the compatibility entrypoints. The previous `COPY *.py`
# silently excluded subpackages, which prevented any real modularisation of the collector.
COPY . .

CMD ["python", "fetch_prices.py"]
