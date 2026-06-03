# Changelog

## 3.4.2 - 2026-06-03

### Fixed

- 旧配置中已保存的 `先这样走一步。` 会自动视为旧默认值，运行时改用新的内置多句玩家台词模板组。
- 已写入对局时间线的旧默认玩家台词，在 WebUI 展示时也会替换为新的内置台词。

### Changed

- 玩家默认台词组参考 Bot 兜底台词风格重写为 8 条中性短句，并支持按走法稳定选择。
- 聊天棋盘 PNG 改为 2 倍内部抗锯齿渲染后缩放输出，线条、文字和棋子边缘更顺滑。
- 聊天棋盘 PNG 棋子外圈、阴影、内环和字体权重进一步增强，视觉变化更明显。

## 3.4.1 - 2026-06-03

### Fixed

- 修复 WebUI 夜间页面主题下，棋盘空交点按钮被暗色按钮背景染成大圆点的问题。
- WebUI 回合提示中的“Bot 正在思考”改为使用 `webui_bot_name` 配置的显示名。

### Changed

- WebUI 回合面板收紧留白，信息区更紧凑。
- `webui_player_talk_template` 默认改为多句模板组，玩家时间线台词会按走法稳定选择一条。
- 聊天棋盘 PNG 棋子改为更柔和的渐变棋具样式，缩小文字并增加轻微描边提升观感。

## 3.4.0 - 2026-06-03

### Added

- WebUI 棋局记录改为走法和台词合并时间线，按每一手展示执方、玩家/Bot 名、中文棋谱、坐标和台词。
- 新增 `webui_player_name`、`webui_bot_name`、`webui_player_talk_template`，可配置网页时间线里的显示名和玩家默认台词。
- WebUI 页面主题和棋盘皮肤拆分为两个独立控制，网页明暗模式不再强制绑定棋盘皮肤。

### Changed

- WebUI 棋盘几何改为左右对称居中，行列坐标按棋盘线动态对齐。
- WebUI 顶部当前行棋与回合进度改为棋盘上方居中纵向排布。
- WebUI 棋子文字缩小、半透明并微调垂直居中，夜战棋盘的棋子配色更柔和。
- WebUI 落子音效在用户点击操作时主动解锁音频上下文，并提高合成音量。

## 3.3.0 - 2026-06-03

### Added

- WebUI 新增经典、青玉、夜战、宣纸四套皮肤，并会记住浏览器本地选择。
- WebUI 新增落子/吃子音效，使用浏览器 Web Audio 合成，不依赖外部音频文件。
- WebUI 新增顶部当前行棋、Bot 思考状态和回合进度条。
- 聊天棋盘 PNG 新增 `board_image_theme` 配置，支持 `classic`、`jade`、`dark`、`paper`。

### Changed

- WebUI 走棋改为即时反馈：玩家落子后立即保存并刷新棋盘，Bot 引擎搜索和台词生成改为后台完成。
- WebUI 最近走法和 Bot 台词改为卡片式全量本局滚动历史。
- WebUI 棋盘布局、选中棋子动效和落子动效升级，整体更接近可长期游玩的棋盘界面。
- WebUI 后台 Bot 任务会在重开、悔棋、认输和插件停止时自动取消，避免旧任务误落子。

## 3.2.0 - 2026-06-02

### Changed

- WebUI 棋盘改为真正的中国象棋线盘：新增楚河汉界、九宫斜线、兵炮位标记和交点落子布局。
- WebUI 棋子改为更有棋具质感的双圈木质棋子，并优化选中、可走点和最近一步标记。
- 聊天棋盘 PNG 渲染升级为传统线盘风格，增加楚河汉界、兵炮位、九宫、棋子阴影和状态栏。
- Pikafish HTTP 服务启动提示改为显示 `/bestmove` 接口地址，减少和 `/health` 检查地址混淆。

## 3.1.4 - 2026-06-02

### Fixed

- 修复 `astrbot_plugin_chess_arena` 平台 FEN 使用 `h/e` 表示马/象时，Pikafish HTTP 服务原样转发导致 Pikafish 进程退出的问题；现在会自动转换为 Pikafish 兼容的 `n/b`。
- 启动 Pikafish HTTP 服务的聊天提示改为显示 `/bestmove` 接口地址，不再把内部 `/health` 检查地址作为主链接发出。

## 3.1.3 - 2026-06-02

### Changed

- 插件托管启动 Pikafish HTTP 服务后，会在 `pikafish_http_service_startup_wait_seconds` 时间内反复等待 `/health` 可访问，减少低配服务器上“服务已启动但健康检查暂未通过”的误报。
- `Pikafish服务` 状态命令在健康检查失败时会附带最近服务日志，方便判断端口未监听、进程退出或依赖缺失。

### Added

- 新增配置 `pikafish_http_service_startup_wait_seconds`，默认 6 秒，低配服务器可调到 10-15 秒。

## 3.1.2 - 2026-06-02

### Fixed

- 修复 Pikafish HTTP 服务在 `TimeoutError()` 等异常下返回 `{"error": ""}` 的问题；现在会返回明确的错误类型、FEN、合法走法样例和超时上下文。
- 修复 Pikafish HTTP 服务等待 `readyok` 的错误处理，避免空异常吞掉真正原因。

### Changed

- Pikafish HTTP 服务的 422 响应和服务日志增加更多排错信息，方便 `astrbot_plugin_chess_arena` 自定义 HTTP 引擎定位失败原因。

## 3.1.1 - 2026-06-02

### Added

- 新增插件托管 Pikafish HTTP 服务命令：`启动Pikafish服务`、`停止Pikafish服务`、`重启Pikafish服务`、`Pikafish服务`。
- 新增 `pikafish_http_service_auto_start`，可在插件初始化时自动拉起 `tools/pikafish_http_service/` 独立服务。
- 新增托管服务配置：`pikafish_http_service_python`、`pikafish_http_service_pikafish_path`、`pikafish_http_service_log_level`。

### Changed

- 插件关闭时会停止自己托管启动的 Pikafish HTTP 服务；不会停止 systemd 或用户手动启动的外部服务。

## 3.1.0 - 2026-06-02

### Added

- 新增 `tools/pikafish_http_service/` 独立 Pikafish HTTP 服务，可让多个象棋插件共享同一个常驻 Pikafish 子进程。
- 新增本插件可选引擎后端 `pikafish_http`，支持通过 `POST /bestmove` 调用共享服务。
- 新增配置：`enable_pikafish_http_engine`、`pikafish_http_url`、`pikafish_http_timeout_ms`、`pikafish_http_movetime_ms`、`pikafish_http_headers`、`pikafish_http_failure_cooldown_seconds`。
- README 增加 `astrbot_plugin_chess_arena` 使用共享服务的 `custom_http` 配置示例。

### Changed

- `auto` 引擎链在配置 HTTP 服务后会优先尝试 `pikafish_http -> pikafish -> xqwlight -> builtin`；默认不启用 HTTP 服务，不影响现有配置。

## 3.0.11 - 2026-06-02

### Fixed

- 修复 Bot 走棋台词可能读不到 AstrBot 人格 prompt 的问题；现在会按 AstrBot 的会话级人格、当前对话分支人格、全局默认人格顺序解析。
- 修复 `Personality` 为 dict 风格对象时旧逻辑用属性读取导致人格为空的问题。

### Changed

- 收敛走棋台词 prompt，明确“人格优先，棋局只是内容来源”，避免默认诱导成嘴硬、挑衅、竞技解说口吻。
- 本地兜底台词改得更中性，减少与用户设置人格冲突。

## 3.0.10 - 2026-06-02

### Added

- 新增未结束棋局保护：同一会话已有对局时，普通 `开局/执黑` 不再覆盖当前棋局。
- 新增强制重置命令：`重开`、`重开执黑`，用于明确重置当前会话棋局。
- WebUI 新增 `重开`、`重开执黑` 按钮；普通 `开局/执黑` 同样遵守未结束棋局保护。
- 新增配置 `protect_active_game_reset`，默认开启。

## 3.0.9 - 2026-06-02

### Added

- 新增独立 aiohttp WebUI，不依赖 AstrBot Pages，可通过 `网页下棋` / `棋局链接` 生成会话专属 token 链接。
- WebUI 支持点击棋盘走棋、新局、执黑开局、提示、悔棋和认输。
- WebUI 与聊天端共用同一个会话存档；网页操作默认会主动同步摘要、Bot 台词和棋盘图到原聊天。
- 新增 WebUI 配置：`webui_enabled`、`webui_host`、`webui_port`、`webui_public_base_url`、`webui_token_ttl_seconds`、`webui_notify_chat`、`webui_notify_board`。

### Changed

- 聊天走棋流程抽取为共享回合逻辑，后续聊天命令和 WebUI 均复用同一套规则校验、引擎降级、LLM 台词和存档更新。

## 3.0.8 - 2026-06-02

### Added

- 新增可选中文棋谱走法：支持 `马八进七`、`炮二平五`、`兵三进一`、`走 马八进七` 等输入。
- 新增 `enable_chinese_notation`，可关闭中文棋谱走法，只保留坐标走法。
- README 增加中文棋谱说明，解释中文路数和 `a-i / 0-9` 坐标的区别。

## 3.0.7 - 2026-06-02

### Fixed

- 将 `llm_talk_timeout` 的有效上限从 15 秒放宽到 300 秒；现在配置为 `120` 会真正等待 120 秒。

## 3.0.6 - 2026-06-02

### Changed

- 外部引擎失败冷却和台词失败冷却默认改为 `0`，即默认不冷却。

### Fixed

- 修复整数/浮点配置读取会把 `0` 当作未填写的问题；现在手动把冷却填为 `0` 会真正关闭冷却，不会回退到 600 秒。

## 3.0.5 - 2026-06-02

### Fixed

- 修正棋子文字垂直居中算法，避免中文棋子名看起来偏下。
- 调整顶部列坐标位置，使其与底部列坐标到棋盘边缘的距离一致。

## 3.0.4 - 2026-06-02

### Added

- 新增普通聊天棋局上下文注入：当前会话有未结束棋局时，LLM 请求会临时获得双方执色、当前轮次、最近一步、最近几手、Bot 之前说过的话和 ASCII 棋盘。
- 新增 `chat_board_context_enabled`，可关闭普通聊天局势注入。
- 新增 `chat_board_context_max_items`，可配置普通聊天上下文带入的最近走法/台词条数。

## 3.0.3 - 2026-06-02

### Added

- 新增走法摘要开关和模板：默认文案为 `你走了 {player_move} 我走了 {bot_move}`。
- 新增台词发送模板：默认 `{talk}`，不再自动添加 `「」`。
- 新增对局记忆：保存最近几手和 Bot 最近说过的话，生成下一句台词时会放入 prompt。

### Changed

- 引擎搜索评分、失败降级原因默认只写入 AstrBot 日志，不再出现在聊天回复里。
- 顶部坐标上移并增加棋盘上边距，避免顶端棋子遮挡 a-i 坐标。
- 棋盘渲染增加 Windows 常见中文字体候选，本地预览不再容易出现方块字。

## 3.0.2 - 2026-06-02

### Changed

- 外部引擎失败日志更详细：Pikafish/xqwlight 失败时会记录引擎名、冷却秒数、关键配置和异常原因，方便在 AstrBot 日志中排查。

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
- 新增 `engine_details_in_chat`，可控制是否在对局回复中展示引擎细节。
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
