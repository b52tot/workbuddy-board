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

# 配置直接复用 config.example.json —— 它是**单一事实来源**。
# 别在这里手写一段 JSON：手写的那份会缺 board.fields，于是容器里
# update_progress 直接不可用（"配置里没有任何 progress 字段"），
# 而 tools/list 照样全绿 —— 正是那种「看起来更好」的静默失效。
#
# db_path 保持模板里的 ./data/board.db：config.resolve_path 以**配置文件所在目录**
# 为基准解析相对路径，所以落在 /app/data/board.db，容器内可写（Store 会自建目录）。
# 想持久化就 -v 一个卷到 /app/data。
COPY config.example.json ./config.json

ENV WBB_CONFIG=/app/config.json
ENV PYTHONUNBUFFERED=1

# stdio 传输：不监听端口，由客户端拉起并通过标准输入输出通信。
ENTRYPOINT ["python", "-m", "server.mcp_server"]
