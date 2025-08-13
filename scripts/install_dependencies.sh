#!/bin/bash

# AWS2-GIOT-FULL Install Dependencies Script
# 애플리케이션 의존성 설치 및 빌드

set -e

echo "=== Install Dependencies: 의존성 설치 시작 ==="

# 애플리케이션 디렉토리로 이동
cd /opt/aws2-giot-full

echo "현재 디렉토리: $(pwd)"
echo "파일 목록: $(ls -la)"

# 1. 루트 레벨 의존성 설치 (concurrently)
echo "1. 루트 의존성 설치 중..."
if [ -f "package.json" ]; then
    npm install --production
    echo "루트 의존성 설치 완료"
else
    echo "⚠️  루트 package.json을 찾을 수 없습니다."
fi

# 2. 백엔드 의존성 설치 (aws2-api)
echo "2. 백엔드 의존성 설치 중..."
if [ -d "aws2-api" ]; then
    cd aws2-api
    echo "백엔드 디렉토리로 이동: $(pwd)"
    
    # 백엔드 npm 의존성 설치
    npm install --production
    
    # NestJS 빌드
    echo "NestJS 애플리케이션 빌드 중..."
    npm run build
    
    # Python 의존성 설치
    if [ -f "python-scripts/requirements.txt" ]; then
        echo "Python 의존성 설치 중..."
        pip3 install -r python-scripts/requirements.txt
        echo "Python 의존성 설치 완료"
    else
        echo "⚠️  requirements.txt를 찾을 수 없습니다."
    fi
    
    # Python 스크립트 실행 권한 부여
    if [ -f "python-scripts/api_wrapper.py" ]; then
        chmod +x python-scripts/api_wrapper.py
        echo "Python 스크립트 실행 권한 부여 완료"
    fi
    
    cd ..
    echo "백엔드 의존성 설치 및 빌드 완료"
else
    echo "❌ aws2-api 디렉토리를 찾을 수 없습니다."
    exit 1
fi

# 3. 프론트엔드 의존성 설치 및 빌드 (frontend_backup)
echo "3. 프론트엔드 의존성 설치 및 빌드 중..."
if [ -d "frontend_backup" ]; then
    cd frontend_backup
    echo "프론트엔드 디렉토리로 이동: $(pwd)"
    
    # 프론트엔드 npm 의존성 설치
    npm install --production
    
    # React 앱 빌드 (프로덕션용)
    echo "React 애플리케이션 빌드 중..."
    npm run build
    
    # 빌드 결과 확인
    if [ -d "build" ]; then
        echo "✅ React 빌드 완료. 파일 수: $(find build -type f | wc -l)"
    else
        echo "❌ React 빌드 실패"
        exit 1
    fi
    
    cd ..
    echo "프론트엔드 빌드 완료"
else
    echo "❌ frontend_backup 디렉토리를 찾을 수 없습니다."
    exit 1
fi

# 4. PM2 ecosystem 파일 생성
echo "4. PM2 설정 파일 생성 중..."
cat > ecosystem.config.js << EOF
module.exports = {
  apps: [
    {
      name: 'aws2-api-backend',
      script: 'aws2-api/dist/main.js',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      env: {
        NODE_ENV: 'production',
        PORT: 3001
      },
      log_file: '/var/log/aws2-giot-full/backend.log',
      out_file: '/var/log/aws2-giot-full/backend-out.log',
      error_file: '/var/log/aws2-giot-full/backend-error.log'
    }
  ]
};
EOF

echo "PM2 ecosystem 설정 완료"

# 5. Nginx 설정 파일 생성 (정적 파일 서빙용)
echo "5. Nginx 설정 파일 생성 중..."
sudo tee /etc/nginx/conf.d/aws2-giot-full.conf > /dev/null << EOF
# AWS2-GIOT-FULL Nginx Configuration
server {
    listen 80;
    server_name localhost;
    
    # React 정적 파일 서빙
    location / {
        root /opt/aws2-giot-full/frontend_backup/build;
        index index.html index.htm;
        try_files \$uri \$uri/ /index.html;
        
        # 캐시 설정
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }
    
    # API 프록시 (백엔드)
    location /api/ {
        proxy_pass http://localhost:3001/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 86400;
    }
    
    # 챗봇 API 직접 접근
    location /chatbot/ {
        proxy_pass http://localhost:3001/chatbot/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 86400;
    }
    
    # 로그 설정
    access_log /var/log/nginx/aws2-giot-full-access.log;
    error_log /var/log/nginx/aws2-giot-full-error.log;
}
EOF

echo "Nginx 설정 완료"

# 6. 권한 설정
echo "6. 파일 권한 설정 중..."
chown -R ec2-user:ec2-user /opt/aws2-giot-full
chmod -R 755 /opt/aws2-giot-full

# 실행 파일 권한 설정
find /opt/aws2-giot-full -name "*.sh" -exec chmod +x {} \;

echo "=== Install Dependencies 완료 ==="
echo "설치된 구성 요소:"
echo "- Node.js: $(node --version)"
echo "- npm: $(npm --version)"
echo "- Python: $(python3 --version)"
echo "- PM2: $(pm2 --version)"
echo "- 백엔드 빌드: ✅"
echo "- 프론트엔드 빌드: ✅"
echo "- 설정 파일: ✅"