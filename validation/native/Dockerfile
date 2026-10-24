FROM ghcr.io/sallyn0225/eidolon-sc-backend@sha256:da4e28295073c482bfd689720c5f1a939b5d045493cee91f0e8d01eb210be52f
COPY backend/src /app/src
COPY backend/tests /app/tests
COPY validation/native /app/validation/native
ENV PYTHONPATH=/app:/app/src
