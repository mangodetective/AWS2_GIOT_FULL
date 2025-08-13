#!/bin/bash

# AWS2-GIOT-FULL Start Server Script
# 애플리케이션 서버 시작

set -e

echo "=== Start Server: 서버 시작 ==="

# 애플리케이션 디렉토리로 이동
cd /opt/aws2-giot-full

echo "현재 디렉토리: $(pwd)"

# 1. 기존 PM2 프로세스 정리 (있다면)
echo "1. 기존 PM2 프로세스 확인 및 정리 중..."
if pm2 list | grep -q "aws2-api-backend"; then
    echo "기존 백엔드 프로세스 중지 중..."
    pm2 stop aws2-api-backend || true
    pm2 delete aws2-api-backend || true
fi

# 2. 환경 변수 설정
echo "2. 환경 변수 설정 중..."
export NODE_ENV=production
export PORT=3001

# AWS 자격증명은 EC2 IAM Role 사용
# export AWS_REGION=ap-northeast-2  # 필요시 명시적으로 설정

# 3. PM2로 백엔드 시작
echo "3. PM2로 백엔드 애플리케이션 시작 중..."
if [ -f "ecosystem.config.js" ]; then
    pm2 start ecosystem.config.js
    echo "✅ 백엔드 PM2 프로세스 시작 완료"
else
    echo "❌ ecosystem.config.js를 찾을 수 없습니다."
    exit 1
fi

# 4. PM2 프로세스 상태 확인
echo "4. PM2 프로세스 상태 확인 중..."
pm2 list

# 5. PM2 자동 시작 설정 (시스템 재부팅시)
echo "5. PM2 자동 시작 설정 중..."
pm2 save
sudo pm2 startup systemd -u ec2-user --hp /home/ec2-user || echo "PM2 startup 설정 실패 (이미 설정되어 있을 수 있음)"

# 6. Nginx 시작/재시작
echo "6. Nginx 서비스 시작 중..."
sudo systemctl enable nginx
sudo systemctl start nginx || sudo systemctl restart nginx

# Nginx 상태 확인
if sudo systemctl is-active --quiet nginx; then
    echo "✅ Nginx 서비스 실행 중"
else
    echo "❌ Nginx 서비스 시작 실패"
    sudo systemctl status nginx
    exit 1
fi

# 7. 로그 파일 권한 설정
echo "7. 로그 파일 권한 설정 중..."
sudo chown -R ec2-user:ec2-user /var/log/aws2-giot-full
chmod 755 /var/log/aws2-giot-full

# 8. 서비스 시작 대기
echo "8. 서비스 시작 대기 중..."
sleep 10

# 9. 백엔드 서비스 상태 확인
echo "9. 백엔드 서비스 상태 확인 중..."
if pm2 list | grep -q "online.*aws2-api-backend"; then
    echo "✅ 백엔드 서비스가 정상적으로 실행 중입니다."
else
    echo "❌ 백엔드 서비스 시작 실패"
    echo "PM2 로그 확인:"
    pm2 logs aws2-api-backend --lines 20 || true
    exit 1
fi

# 10. 포트 확인
echo "10. 포트 사용 상태 확인 중..."
if netstat -tlnp | grep -q ":3001.*LISTEN"; then
    echo "✅ 백엔드 포트 3001이 정상적으로 열려 있습니다."
else
    echo "⚠️  백엔드 포트 3001이 아직 열리지 않았습니다."
fi

if netstat -tlnp | grep -q ":80.*LISTEN"; then
    echo "✅ Nginx 포트 80이 정상적으로 열려 있습니다."
else
    echo "⚠️  Nginx 포트 80이 아직 열리지 않았습니다."
fi

echo "=== Start Server 완료 ==="
echo ""
echo "🚀 서비스 시작 완료!"
echo "📊 서비스 상태:"
echo "  - 백엔드 API: http://localhost:3001"
echo "  - 프론트엔드: http://localhost (Nginx를 통해 서빙)"
echo "  - 챗봇 API: http://localhost/chatbot/ask"
echo ""
echo "📋 관리 명령어:"
echo "  - PM2 상태 확인: pm2 list"
echo "  - PM2 로그 확인: pm2 logs"
echo "  - Nginx 상태 확인: sudo systemctl status nginx"
echo "  - Nginx 로그 확인: sudo tail -f /var/log/nginx/aws2-giot-full-error.log"