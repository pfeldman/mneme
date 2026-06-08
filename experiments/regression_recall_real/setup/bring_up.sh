#!/usr/bin/env bash
# Bring Conduit (RealWorld reference Medium-clone) up locally for the Phase 2
# real-app regression-recall experiment. ADR-0016 sec 1 C1: this script
# (image pull included) must exit zero in under 30 minutes on a commodity
# developer laptop; cold-cache wall-clock on a 100 Mbit link is typically
# under 10 minutes for the two reference images.
#
# Usage:
#   bash bring_up.sh                # bring backend + frontend up + wait healthy
#   bash bring_up.sh --teardown     # stop + remove the compose stack
#   bash bring_up.sh --check        # only verify the API is responding (idempotent probe)
#
# Exit codes:
#   0   stack is up and the API responds 200 on /api/tags within the ceiling.
#   2   docker is missing or unable to start the stack.
#   3   compose started but health gate timed out (>30 minutes wall clock).
#
# This script is read by `tests/test_conduit_bringup.py`; the test is GATED
# behind `PRAXIS_RUN_CONDUIT_BRINGUP=1` so the default `bash verify.sh` stays
# fast. CI / Pablo runs the slow gate explicitly.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.yml"

# Hard 30-minute ceiling for the bring-up step per ADR-0016 C1. The poll loop
# below converts that into wall-clock seconds.
DEADLINE_SECONDS="${PRAXIS_CONDUIT_DEADLINE_SECONDS:-1800}"
POLL_INTERVAL="${PRAXIS_CONDUIT_POLL_INTERVAL:-5}"

log() { printf "[bring_up] %s\n" "$*"; }

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "docker not on PATH; install Docker Desktop or the docker CLI first"
    exit 2
  fi
  if ! docker info >/dev/null 2>&1; then
    log "docker daemon not reachable (is Docker Desktop running?)"
    exit 2
  fi
}

compose() {
  # Prefer `docker compose` (V2) over the legacy `docker-compose` binary.
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${COMPOSE_FILE}" "$@"
  else
    docker-compose -f "${COMPOSE_FILE}" "$@"
  fi
}

wait_for_api() {
  local start
  start=$(date +%s)
  while true; do
    local now elapsed
    now=$(date +%s)
    elapsed=$((now - start))
    if [ "${elapsed}" -ge "${DEADLINE_SECONDS}" ]; then
      log "TIMEOUT: API did not respond within ${DEADLINE_SECONDS}s"
      return 3
    fi
    if curl -sf --max-time 3 http://localhost:3000/api/tags >/dev/null 2>&1; then
      log "API healthy on /api/tags after ${elapsed}s"
      return 0
    fi
    sleep "${POLL_INTERVAL}"
  done
}

teardown() {
  require_docker
  log "tearing down Conduit stack"
  compose down --remove-orphans
}

check_only() {
  if curl -sf --max-time 3 http://localhost:3000/api/tags >/dev/null 2>&1; then
    log "API responding"
    exit 0
  fi
  log "API not responding on http://localhost:3000/api/tags"
  exit 3
}

bring_up() {
  require_docker
  log "starting Conduit stack via docker compose"
  if ! compose up -d; then
    log "compose up failed"
    exit 2
  fi
  log "waiting for /api/tags to return 200 (deadline ${DEADLINE_SECONDS}s)"
  if ! wait_for_api; then
    exit 3
  fi
  log "Conduit is up: backend http://localhost:3000  frontend http://localhost:4100"
}

main() {
  case "${1:-}" in
    --teardown) teardown ;;
    --check)    check_only ;;
    "")         bring_up ;;
    *)          log "usage: $0 [--teardown | --check]"; exit 2 ;;
  esac
}

main "$@"
