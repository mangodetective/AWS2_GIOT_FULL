#!/usr/bin/env bash
set -euo pipefail

cd /opt/app/frontend

# Node.js 없으면 설치 (AL2023)
if ! command -v node >/dev/null 2>&1; then
  # 기본 리포지토리 시도
  sudo dnf install -y nodejs npm || {
    # 실패하면 NodeSource로
    curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -
    sudo dnf install -y nodejs
  }
fi

# 의존성 설치 & 빌드
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi

npm run build   # Vite: dist/, CRA: build/
