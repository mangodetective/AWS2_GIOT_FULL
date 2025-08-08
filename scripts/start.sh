#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=/opt/app
FRONT_DIR=""

# front / frontend / web / client 중 존재하는 첫 폴더 사용
for d in front frontend web client; do
  if [ -d "$APP_ROOT/$d" ]; then FRONT_DIR="$APP_ROOT/$d"; break; fi
done

if [ -z "$FRONT_DIR" ]; then
  echo "No frontend directory under $APP_ROOT" >&2
  exit 1
fi

# 빌드 산출물 찾기 (Vite=dist, CRA=build)
ARTIFACT=""
if   [ -d "$FRONT_DIR/dist"  ]; then ARTIFACT="$FRONT_DIR/dist"
elif [ -d "$FRONT_DIR/build" ]; then ARTIFACT="$FRONT_DIR/build"
else
  echo "No build artifacts found (dist/ or build/) in $FRONT_DIR" >&2
  exit 1
fi

sudo mkdir -p /var/www/app
if command -v rsync >/dev/null 2>&1; then
  sudo rsync -a --delete "$ARTIFACT/"/ /var/www/app/
else
  sudo rm -rf /var/www/app/*
  sudo cp -r "$ARTIFACT"/. /var/www/app/
fi

sudo nginx -t
sudo systemctl restart nginx
