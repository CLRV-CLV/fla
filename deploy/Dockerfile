# FLA (FYX Lesson All) - 生产镜像 (Python + LibreOffice + ffmpeg + 中文字体)
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 TZ=Asia/Hong_Kong

# apt: 官方源失败(DNS/网络)时自动换镜像重试
RUN set -e; PKGS="libreoffice-impress libreoffice-writer libreoffice-calc fontconfig tzdata \
      fonts-noto-cjk fonts-liberation fonts-liberation2 fonts-lxgw-wenkai fonts-crosextra-carlito fonts-crosextra-caladea ffmpeg"; \
    ok=0; for M in deb.debian.org mirrors.aliyun.com mirrors.tuna.tsinghua.edu.cn mirrors.ustc.edu.cn; do \
      sed -i -E "s#https?://[^/ ]+/debian#http://$M/debian#g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      echo ">> apt 源: $M"; \
      if apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=20 update && \
         apt-get install -y --no-install-recommends -o Acquire::Retries=3 $PKGS; then ok=1; break; fi; \
    done; [ "$ok" = 1 ]; rm -rf /var/lib/apt/lists/*
# v1.26: ffmpeg — 非浏览器通用视频格式(mkv/wmv/avi/mov...)自动转码 mp4 (开源离线)

# 常见商业字体 -> 开源字体替换规则(保证不出现方框)
COPY deploy/fonts/30-edu-substitutions.conf /etc/fonts/conf.d/30-edu-substitutions.conf

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 || pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY server ./server
COPY web ./web
COPY desktop ./desktop

ENV FLA_DATA_DIR=/data PORT=8306
RUN mkdir -p /data
VOLUME /data
EXPOSE 8306 8000

HEALTHCHECK --interval=60s --timeout=10s --retries=5 \
  CMD python -c "import urllib.request, os; p=os.environ.get('PORT', '8306'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health', timeout=8)" || exit 1

# v1.27: 端口从环境变量 PORT 读取 (默认 8306)
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port \"${PORT:-8306}\" --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
