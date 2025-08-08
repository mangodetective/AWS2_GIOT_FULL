#!/usr/bin/env bash
set -e
sudo dnf update -y
sudo dnf install -y nginx
sudo mkdir -p /var/www/app
sudo chown -R ec2-user:ec2-user /var/www/app
sudo systemctl enable nginx
