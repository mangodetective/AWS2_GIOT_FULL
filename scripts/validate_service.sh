#!/bin/bash

# AWS2-GIOT-FULL Validate Service Script
# 배포된 서비스의 정상 동작 검증

set -e

echo "=== Validate Service: 서비스 검증 시작 ==="

# 검증 결과 추적
VALIDATION_FAILED=0

# 1. PM2 프로세스 상태 검증
echo "1. PM2 프로세스 상태 검증 중..."
if pm2 list | grep -q "online.*aws2-api-backend"; then
    echo "✅ PM2 백엔드 프로세스가 정상 실행 중입니다."
else
    echo "❌ PM2 백엔드 프로세스가 실행되지 않고 있습니다."
    pm2 list
    VALIDATION_FAILED=1
fi

# 2. 포트 리스닝 상태 검증
echo "2. 포트 리스닝 상태 검증 중..."

# 백엔드 포트 (3001)
if netstat -tlnp | grep -q ":3001.*LISTEN"; then
    echo "✅ 백엔드 포트 3001이 정상적으로 열려 있습니다."
else
    echo "❌ 백엔드 포트 3001이 열려 있지 않습니다."
    VALIDATION_FAILED=1
fi

# Nginx 포트 (80)
if netstat -tlnp | grep -q ":80.*LISTEN"; then
    echo "✅ Nginx 포트 80이 정상적으로 열려 있습니다."
else
    echo "❌ Nginx 포트 80이 열려 있지 않습니다."
    VALIDATION_FAILED=1
fi

# 3. 백엔드 API 헬스체크
echo "3. 백엔드 API 헬스체크 중..."
MAX_RETRIES=10
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -f -s -o /dev/null http://localhost:3001/healthz; then
        echo "✅ 백엔드 헬스체크 성공"
        break
    else
        echo "⏳ 백엔드 응답 대기 중... (시도 $((RETRY_COUNT + 1))/$MAX_RETRIES)"
        RETRY_COUNT=$((RETRY_COUNT + 1))
        sleep 5
    fi
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "❌ 백엔드 헬스체크 실패"
    echo "백엔드 로그 확인:"
    pm2 logs aws2-api-backend --lines 10 || true
    VALIDATION_FAILED=1
fi

# 4. 챗봇 API 헬스체크
echo "4. 챗봇 API 헬스체크 중..."
if curl -f -s http://localhost:3001/chatbot/health | grep -q '"status":"healthy"'; then
    echo "✅ 챗봇 API 헬스체크 성공"
else
    echo "❌ 챗봇 API 헬스체크 실패"
    echo "챗봇 헬스체크 응답:"
    curl -s http://localhost:3001/chatbot/health || echo "API 요청 실패"
    VALIDATION_FAILED=1
fi

# 5. 프론트엔드 정적 파일 검증 (Nginx를 통해)
echo "5. 프론트엔드 정적 파일 검증 중..."
if curl -f -s -o /dev/null http://localhost/; then
    echo "✅ 프론트엔드 페이지가 정상적으로 서빙되고 있습니다."
else
    echo "❌ 프론트엔드 페이지 접근 실패"
    echo "Nginx 상태 확인:"
    sudo systemctl status nginx || true
    echo "Nginx 에러 로그:"
    sudo tail -n 10 /var/log/nginx/aws2-giot-full-error.log || true
    VALIDATION_FAILED=1
fi

# 6. 중요 파일 존재 확인
echo "6. 중요 파일 존재 확인 중..."

# 백엔드 빌드 파일
if [ -f "/opt/aws2-giot-full/aws2-api/dist/main.js" ]; then
    echo "✅ 백엔드 빌드 파일 존재"
else
    echo "❌ 백엔드 빌드 파일이 존재하지 않습니다."
    VALIDATION_FAILED=1
fi

# 프론트엔드 빌드 파일
if [ -f "/opt/aws2-giot-full/frontend_backup/build/index.html" ]; then
    echo "✅ 프론트엔드 빌드 파일 존재"
else
    echo "❌ 프론트엔드 빌드 파일이 존재하지 않습니다."
    VALIDATION_FAILED=1
fi

# Python 챗봇 스크립트
if [ -f "/opt/aws2-giot-full/aws2-api/python-scripts/api_wrapper.py" ]; then
    echo "✅ Python 챗봇 스크립트 존재"
else
    echo "❌ Python 챗봇 스크립트가 존재하지 않습니다."
    VALIDATION_FAILED=1
fi

# 7. 로그 파일 권한 및 생성 확인
echo "7. 로그 파일 확인 중..."
if [ -d "/var/log/aws2-giot-full" ]; then
    echo "✅ 로그 디렉토리가 존재합니다."
    echo "로그 파일 목록:"
    ls -la /var/log/aws2-giot-full/ || true
else
    echo "❌ 로그 디렉토리가 존재하지 않습니다."
    VALIDATION_FAILED=1
fi

# 8. 시스템 리소스 확인
echo "8. 시스템 리소스 확인 중..."
echo "메모리 사용량:"
free -h

echo "디스크 사용량:"
df -h /opt/aws2-giot-full

echo "CPU 사용량:"
top -bn1 | head -3

# 9. 통합 API 테스트 (선택적)
echo "9. 통합 API 테스트 중..."

# 간단한 챗봇 질문 테스트
CHATBOT_TEST_RESPONSE=$(curl -s -X POST http://localhost:3001/chatbot/ask \
    -H "Content-Type: application/json" \
    -d '{"query": "안녕하세요"}' | jq -r '.answer' 2>/dev/null || echo "API 테스트 실패")

if [ "$CHATBOT_TEST_RESPONSE" != "API 테스트 실패" ] && [ "$CHATBOT_TEST_RESPONSE" != "null" ]; then
    echo "✅ 챗봇 API 통합 테스트 성공"
    echo "   응답 길이: ${#CHATBOT_TEST_RESPONSE} 문자"
else
    echo "❌ 챗봇 API 통합 테스트 실패"
    echo "   응답: $CHATBOT_TEST_RESPONSE"
    VALIDATION_FAILED=1
fi

# 10. 검증 결과 종합
echo ""
echo "=== Validate Service 결과 ==="

if [ $VALIDATION_FAILED -eq 0 ]; then
    echo "🎉 모든 검증이 성공적으로 완료되었습니다!"
    echo ""
    echo "🌐 서비스 접근 정보:"
    echo "  - 웹 애플리케이션: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)/"
    echo "  - API 엔드포인트: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)/api/"
    echo "  - 챗봇 API: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)/chatbot/"
    echo ""
    echo "📋 모니터링 명령어:"
    echo "  - PM2 모니터링: pm2 monit"
    echo "  - 백엔드 로그: pm2 logs aws2-api-backend"
    echo "  - Nginx 로그: sudo tail -f /var/log/nginx/aws2-giot-full-access.log"
    
    exit 0
else
    echo "❌ 검증 과정에서 $VALIDATION_FAILED개의 문제가 발견되었습니다."
    echo ""
    echo "🔍 문제 해결을 위한 로그 확인:"
    echo "  - PM2 로그: pm2 logs --lines 20"
    echo "  - Nginx 에러 로그: sudo tail -20 /var/log/nginx/aws2-giot-full-error.log"
    echo "  - 시스템 로그: sudo journalctl -u nginx -n 20"
    
    exit 1
fi