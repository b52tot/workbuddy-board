-- WorkBuddy Board — 数据库结构
-- 设计原则：只存「事实」，业务语义全部由 config.json 提供。
-- 这样同一份 schema 可以承载任意业务（下载、构建、审批、评测…）。

PRAGMA journal_mode = WAL;      -- 允许「一写多读」：MCP 写入时不阻塞看板读取
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;     -- 并发写时最多等 5s，避免直接抛 SQLITE_BUSY

-- ---------------------------------------------------------------- 栏目定义
-- 栏目（看板上的列）由配置驱动，落库是为了让看板能读到「显示名/顺序/颜色」。
-- 配置是唯一真相源；本表是配置的物化视图，启动时同步。
CREATE TABLE IF NOT EXISTS columns (
    id          TEXT PRIMARY KEY,      -- 稳定标识，如 todo / doing / done
    title       TEXT NOT NULL,         -- 显示名
    position    INTEGER NOT NULL,      -- 顺序（小的在左）
    color       TEXT,                  -- 前端用的色相标识，如 blue / amber
    wip_limit   INTEGER,               -- 在制品上限，NULL = 不限
    is_terminal INTEGER NOT NULL DEFAULT 0  -- 是否为终态（终态列不计 WIP）
);

-- ---------------------------------------------------------------- 任务
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,      -- 调用方生成的稳定 ID（建议 uuid4）
    title       TEXT NOT NULL,
    description TEXT,                  -- 可选的详细说明
    column_id   TEXT NOT NULL,         -- 当前所在列
    position    REAL NOT NULL,         -- 列内排序；用 REAL 便于「插入到两者之间」不必重排
    priority    INTEGER NOT NULL DEFAULT 0,  -- 越大越优先
    tags        TEXT,                  -- JSON 数组，如 ["nightly","gpu"]
    fields      TEXT,                  -- JSON 对象，承载 config.board.fields 定义的自定义字段
    created_at  TEXT NOT NULL,         -- ISO8601
    updated_at  TEXT NOT NULL,
    started_at  TEXT,                  -- 首次进入「进行中类」列的时间
    finished_at TEXT,                  -- 进入终态列的时间
    archived_at TEXT,                  -- 归档时间；NULL = 在册（详见 Store.archive_done）
    FOREIGN KEY (column_id) REFERENCES columns(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_column ON tasks(column_id, position);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);
-- 归档筛选每次都走（list_tasks / snapshot），必须有索引，
-- 否则任务一多就退化成全表扫。
CREATE INDEX IF NOT EXISTS idx_tasks_archived ON tasks(archived_at);

-- ---------------------------------------------------------------- 事件流水
-- 追加写、不修改、不删除 —— 这是审计与「进度回放」的唯一可信来源。
-- 看板本身的实时状态从 tasks 读；要复盘「什么时候从哪到哪」就读 events。
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT,                   -- 允许 NULL（如 board 级别的操作）
    kind       TEXT NOT NULL,          -- create / move / update / delete / comment
    from_col   TEXT,
    to_col     TEXT,
    actor      TEXT,                   -- 谁改的：agent / user / system
    detail     TEXT,                   -- JSON，存放变更明细
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at DESC);

-- ---------------------------------------------------------------- 元信息
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
