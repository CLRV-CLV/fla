# FLA - 统一入口 nginx (配置打包进镜像, 避免 SELinux bind-mount 问题)
# v1.27: 两份配置放在 fla-available/ (不在 conf.d/), 只有启用的那份被复制成
#        conf.d/default.conf — 修: 此前两份都在 conf.d/ 里被 nginx 通配 include,
#        产生重复 server 块, 精简模式下 /ds/ 路由仍指向不存在的 documentserver
FROM nginx:alpine
RUN mkdir -p /etc/nginx/fla-available
COPY deploy/nginx-app.conf /etc/nginx/fla-available/full.conf
COPY deploy/nginx-app.lite.conf /etc/nginx/fla-available/lite.conf
COPY deploy/nginx-entrypoint.sh /entrypoint-fla.sh
RUN chmod +x /entrypoint-fla.sh && rm -f /etc/nginx/conf.d/default.conf
ENTRYPOINT ["/entrypoint-fla.sh"]
