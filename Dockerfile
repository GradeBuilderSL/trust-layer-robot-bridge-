FROM python:3.11-slim
WORKDIR /app
# PyPI during build: pass --build-arg HTTP_PROXY=/HTTPS_PROXY=... OR pre-fill ./wheels/*.whl
# (see scripts/download_wheels.ps1 on PC) so pip uses --no-index and needs no DNS.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
COPY requirements.txt .
COPY wheels/ /wheels/
RUN sh -c 'set -e; \
  if find /wheels -maxdepth 1 -name "*.whl" -print -quit | grep -q .; then \
    echo "pip: using local wheels/"; \
    pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt; \
  else \
    echo "pip: using PyPI (set HTTP_PROXY/HTTPS_PROXY build-args if needed)"; \
    HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" NO_PROXY="${NO_PROXY}" \
      pip install --no-cache-dir -r requirements.txt; \
  fi'
COPY bridge/ bridge/
# Ontology lib — synced from trust-layer/libs/ontology (131 YAML safety rules).
# Enables ActionGate full rule check instead of 6-rule fallback.
# To update: cp -r ../trust-layer/libs/ontology libs/ontology
COPY libs/ libs/
ENV TRUST_LAYER_LIBS=/app/libs
EXPOSE 8080
CMD ["python", "-m", "bridge.main"]
