#!/bin/bash

# AWS2-GIOT-FULL Before Install Script
# 시스템 환경 준비 및 필요한 소프트웨어 설치

set -e

echo "=== Before Install: 시스템 환경 준비 시작 ==="

# 패키지 업데이트
echo "패키지 목록 업데이트 중..."
yum update -y

# Node.js 18.x 설치 (Amazon Linux 2023)
if ! command -v node &> /dev/null; then
    echo "Node.js 설치 중..."
    curl -fsSL https://rpm.nodesource.com/setup_18.x | bash -
    yum install -y nodejs
else
    echo "Node.js가 이미 설치되어 있습니다: $(node --version)"
fi

# Python 3.11+ 확인 및 pip 설치
echo "Python 환경 확인 중..."
if ! command -v python3 &> /dev/null; then
    echo "Python3 설치 중..."
    yum install -y python3 python3-pip
else
    echo "Python3가 이미 설치되어 있습니다: $(python3 --version)"
fi

# pip 업그레이드
pip3 install --upgrade pip

# PM2 전역 설치 (프로덕션 프로세스 관리용)
if ! command -v pm2 &> /dev/null; then
    echo "PM2 설치 중..."
    npm install -g pm2
else
    echo "PM2가 이미 설치되어 있습니다: $(pm2 --version)"
fi

# 필요한 시스템 패키지 설치
echo "추가 시스템 패키지 설치 중..."
yum install -y \
    git \
    curl \
    wget \
    unzip \
    htop \
    nginx

# 기존 애플리케이션 디렉토리 백업 및 정리
if [ -d "/opt/aws2-giot-full" ]; then
    echo "기존 애플리케이션 백업 중..."
    BACKUP_DIR="/opt/backup/aws2-giot-full-$(date +%Y%m%d-%H%M%S)"
    mkdir -p /opt/backup
    mv /opt/aws2-giot-full $BACKUP_DIR
    echo "기존 애플리케이션을 $BACKUP_DIR 로 백업했습니다."
fi

# 애플리케이션 디렉토리 생성
echo "애플리케이션 디렉토리 생성 중..."
mkdir -p /opt/aws2-giot-full
chown ec2-user:ec2-user /opt/aws2-giot-full

# 로그 디렉토리 생성
echo "로그 디렉토리 생성 중..."
mkdir -p /var/log/aws2-giot-full
chown ec2-user:ec2-user /var/log/aws2-giot-full

# Nginx 설정을 위한 디렉토리 준비
mkdir -p /etc/nginx/conf.d

echo "=== Before Install 완료 ==="