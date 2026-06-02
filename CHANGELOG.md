# Changelog

## 3.0.1 - 2026-06-02

### Fixed

- 修复 Pikafish UCI 坐标与插件棋盘坐标 y 轴相反的问题，避免合法走法被误判为非法。
- 棋盘图片底部增加状态栏空间，避免“当前行棋 / 最近一步”遮挡底部坐标。

## 3.0.0 - 2026-06-02

### Changed

- 插件唯一名改为 `astrbot_plugin_xiangqi_arena`。
- 展示名改为“象棋竞技场”。
- 主插件类改为 `XiangqiArenaPlugin`。
- 默认命令改为更短的 `开局`、`执黑`、`走`、`棋盘`、`提示`、`悔棋`、`认输`、`状态`。
- 对局中可直接发送 `a6 b6`、`a6-b6`、`a6b6` 走棋。
- README 重写为增强分支文档，并说明与原插件的关系。
- 默认不再使用插件内置固定人格提示词，改为优先使用 AstrBot 当前会话选择的人格。

### Added

- 新增 Pikafish UCI 引擎适配。
- 新增 Pikafish 常驻子进程复用，避免每步重复启动引擎。
- 新增 `engine_backend`，支持 `auto` / `pikafish` / `xqwlight` / `builtin`。
- 新增 Pikafish 配置：路径、工作目录、NNUE、线程数、Hash、movetime、Move Overhead、启动超时。
- 新增外部引擎失败冷却：Pikafish/xqwlight 失败后会自动跳过一段时间并尝试后续引擎。
- 新增 `show_engine_details`，可控制是否在对局回复中展示引擎细节。
- 新增 `llm_extra_prompt`，用于在 AstrBot 默认人格基础上追加台词约束。
- 新增 LLM 台词失败冷却，模型超时后自动使用本地兜底台词。

### Fixed

- 修复无可用 LLM Provider 时台词兜底路径可能引用未初始化 `sentence_count` 的问题。
- 改善 xqwlight 频繁超时时的体验，避免每步都重复等待 Java 引擎超时。
- Pikafish UCI 交互异常后会关闭旧进程，避免残留输出污染下一步。

### Notes

- 本分支基于 `zxx624/astrbot_plugin_xiangqi` 改造。
- 原插件未附带单独 LICENSE；公开发布 fork 前建议补充清晰授权说明。
- 若分发 Pikafish 二进制，请遵守 Pikafish 的 GPL-3.0 许可证。
