#!/usr/bin/env sh
# Compose health gate: run the in-python health check used by the Dockerfile
# HEALTHCHECK. Passes through the exit code.
exec python health_check.py "$@"