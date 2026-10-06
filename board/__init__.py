"""WorkBuddy Board — 通用任务看板。

三层分离：
  board/   数据层（唯一写入方，SQLite）
  server/  服务层（MCP 工具 + HTTP 看板）
  web/     展示层（无构建步骤的静态页面）

所有业务语义（栏目、流转规则、自定义字段）由 config.json 提供，
代码本身不含任何领域词汇。
"""

__version__ = "1.0.0"
