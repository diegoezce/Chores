#!/bin/sh
# Si hay commits nuevos en origin/main, los baja y reconstruye el contenedor.
# Pensado para correr desde cron en la mini PC (ver README).
cd "$(dirname "$0")" || exit 1
git fetch -q origin main || exit 1
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "$(date '+%F %T') actualizando a $(git rev-parse --short origin/main)"
  git pull -q --ff-only origin main && docker compose up -d --build
fi
