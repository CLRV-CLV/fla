#!/bin/sh
# FLA nginx 入口: 按 FLA_MODE 选择配置; 打印模式并校验配置, 便于诊断
set -e
rm -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/*.conf
case "${FLA_MODE:-full}" in
  lite) cp /etc/nginx/fla-available/lite.conf /etc/nginx/conf.d/default.conf ;;
  *)    cp /etc/nginx/fla-available/full.conf /etc/nginx/conf.d/default.conf ;;
esac
echo "[nginx] FLA_MODE=${FLA_MODE:-full} | 配置: /etc/nginx/conf.d/default.conf"
nginx -t
exec nginx -g 'daemon off;'
