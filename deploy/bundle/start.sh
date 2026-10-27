#!/usr/bin/env bash
# Start Клинический компас from the bundle: load the images (first run), create .env with random
# secrets (first run), start the stack, create the first doctor account (first run).
#   ./start.sh                       # site on http://127.0.0.1:8080
#   KOMPAS_WEB_PORT=9000 ./start.sh  # another port (first run only; later edit .env)
set -euo pipefail
cd "$(dirname "$0")"

docker info >/dev/null 2>&1 || { echo "Docker is not running or not accessible (try: sudo usermod -aG docker \$USER)"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required (docker compose ...)"; exit 1; }

image=$(cat images/IMAGE_TAG)
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "Loading images (one time, a few minutes)..."
  for f in images/*.tar.gz; do gunzip -c "$f" | docker load; done
fi

secret() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
if [ ! -f .env ]; then
  model=keywords
  [ -d hf/hub/models--Qwen--Qwen3-4B ] && model=llm
  cat > .env <<EOF
POSTGRES_PASSWORD=$(secret)
KOMPAS_JWT_SECRET=$(secret)
KOMPAS_INVITE_SECRET=$(secret)
KOMPAS_IMAGE=$image
KOMPAS_TEXT_MODEL=$model
KOMPAS_WEB_BIND=${KOMPAS_WEB_BIND:-127.0.0.1}
KOMPAS_WEB_PORT=${KOMPAS_WEB_PORT:-8080}
EOF
  chmod 600 .env
  echo "Created .env (diary text model: $model)"
  first_run=1
fi

docker compose up -d --wait

if [ "${first_run:-0}" = 1 ]; then
  echo "Creating the first doctor account:"
  docker compose exec -T api kompas create-doctor doctor@kompas-demo.ru "Демо Врач" --password demo-password-1
fi
port=$(grep '^KOMPAS_WEB_PORT=' .env | cut -d= -f2)
bind=$(grep '^KOMPAS_WEB_BIND=' .env | cut -d= -f2)
echo
echo "Ready: http://${bind/0.0.0.0/localhost}:${port}/login   (doctor@kompas-demo.ru / demo-password-1)"
