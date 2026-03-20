FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge/ bridge/
# Ontology lib — synced from trust-layer/libs/ontology (131 YAML safety rules).
# Enables ActionGate full rule check instead of 6-rule fallback.
# To update: cp -r ../trust-layer/libs/ontology libs/ontology
COPY libs/ libs/
ENV TRUST_LAYER_LIBS=/app/libs
EXPOSE 8080
CMD ["python", "-m", "bridge.main"]
