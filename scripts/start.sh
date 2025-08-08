#!/usr/bin/env bash
set -e
sudo cp -r /opt/app/frontend/build/* /var/www/app/
sudo cp /opt/app/nginx/app.conf /etc/nginx/conf.d/app.conf
sudo nginx -t
sudo systemctl restart nginx
