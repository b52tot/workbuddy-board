# WorkBuddy Board —— MCP server 的容器化定义。
#
# 用途：给 Glama（以及任何想直接跑它的环境）一个可复现的启动方式。
# Glama 的检查只做三件事：
#   1. 用这个文件构建镜像
#   2. 启动容器
#   3. 通过 stdio 发 MCP 的 initialize / tools/list，看响应是否正常
#
# 所以这个镜像只需要「能起来、能应答」——不需要 Web UI、不需要暴露端口。
#
# 本项目**零第三方依赖**（只用 Python 标准库），所以没有 pip install 这一步。

FROM python:3.12-slim

WORKDIR /app

COPY board/ ./board/
COPY server/ ./server/

# MCP server 的配置从 config.json 读（看板库位置等）。
# 容器里单独给一个可写目录，避免写到只读的镜像层。
RUN mkdir -p /data \
 && printf '%s\n' \
      '{' \
      '  "board": { "db_path": "/data/board.db" },' \
      '  "web": { "allow_write": false }' \
      '}' > /app/config.json

ENV WBB_CONFIG=/app/config.json
ENV PYTHONUNBUFFERED=1

# stdio 传输：不监听端口，由客户端拉起并通过标准输入输出通信。
ENTRYPOINT ["python", "-m", "server.mcp_server"]
