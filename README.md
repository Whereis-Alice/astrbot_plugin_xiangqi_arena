# astrbot_plugin_xiangqi_arena

**象棋竞技场** 是一个面向 AstrBot 的中国象棋对战插件。它保留原插件的本地规则校验、棋盘渲染和会话存档，并增强为更适合群聊和低配 Linux 服务器的多引擎版本。

本项目基于原插件 [zxx624/astrbot_plugin_xiangqi](https://github.com/zxx624/astrbot_plugin_xiangqi) 改造。原插件提供了坐标制走棋、本地象棋规则、xqwlight/builtin AI 和棋盘图片渲染；本增强分支新增 Pikafish、引擎失败冷却、简化走棋命令和默认人格回复。

## 主要特性

- 多引擎顺序：`Pikafish -> xqwlight -> builtin`
- Pikafish 常驻 UCI 子进程，适合 Linux 服务器
- 外部引擎失败后自动冷却，避免每步都等待超时
- 低配默认参数：小 Hash、单线程、固定 movetime、短台词超时
- 对局中可直接发送 `a6 b6`、`a6-b6`、`a6b6` 走棋
- Bot 走棋后默认使用当前 AstrBot 会话选择的人格生成短回复，并带最近走法/台词记忆
- LLM 回复超时后使用本地兜底台词，不影响棋局
- 本地规则层始终校验引擎返回走法，避免非法落子破坏棋局

## 命令

```text
开局          开始新局，玩家执红
执黑          开始新局，玩家执黑，Bot 先手
a6 b6        对局中直接走棋
a6-b6        同上
a6b6         同上
走 a6 b6     显式走棋命令
棋盘          查看当前棋盘
提示          获取一步建议
悔棋          撤销上一整个回合
认输          结束当前对局
状态          查看对局和引擎冷却状态
```

兼容旧命令：`象棋新局`、`象棋执黑`、`走棋`、`象棋状态`。

## 坐标说明

棋盘为 9 列 x 10 行：

- 列：`a` 到 `i`
- 行：`0` 到 `9`
- `a0` 位于图片左上角
- 红方初始在下方，黑方初始在上方
- 红方向上走，行号减小；黑方向下走，行号增大

示例：红方开局马从 `b9` 跳到 `c7`，可以直接发：

```text
b9 c7
```

## 引擎策略

默认 `engine_backend = auto`，插件会按以下顺序尝试：

1. `pikafish`：强棋力，推荐；通过 UCI 协议常驻运行
2. `xqwlight`：随包 Java 引擎，作为第二外部兜底
3. `builtin`：Python 内置搜索，棋力较弱但最稳定

任何外部引擎超时、崩溃、路径错误或返回非法走法，都会进入失败冷却，并自动尝试下一个引擎。

## Linux 安装 Pikafish

Pikafish 不随本插件打包。请从 [official-pikafish/Pikafish](https://github.com/official-pikafish/Pikafish) 下载适合你服务器架构的 Linux 可执行文件，或自行编译。

示例：

```bash
mkdir -p /opt/pikafish
# 把下载/解压后的 pikafish 可执行文件放到 /opt/pikafish/pikafish
chmod +x /opt/pikafish/pikafish
/opt/pikafish/pikafish
```

进入引擎后输入：

```text
uci
isready
quit
```

能看到 `uciok` 和 `readyok` 就说明 UCI 基本可用。

然后在 AstrBot 插件配置里设置：

```json
{
  "engine_backend": "auto",
  "enable_pikafish_engine": true,
  "pikafish_path": "/opt/pikafish/pikafish",
  "pikafish_threads": 1,
  "pikafish_hash_mb": 16,
  "pikafish_movetime_ms": 500
}
```

如果你的 Pikafish 构建需要单独 NNUE 文件，把 `pikafish_working_dir` 指向引擎目录，或在 `pikafish_eval_file` 中填写 NNUE 文件路径。

## xqwlight 优化

xqwlight 保留为兜底引擎，但它是 Java CLI，每次调用都有启动和搜索开销。低配服务器建议：

```json
{
  "xqwlight_depth": 4,
  "xqwlight_timeout_ms": 800,
  "xqwlight_failure_cooldown_seconds": 600
}
```

如果日志里出现 `UnsupportedClassVersionError`，说明当前 Java 版本低于 jar 编译版本。可以升级 Java，或者直接关闭 xqwlight：

```json
{
  "enable_xqwlight_engine": false
}
```

## 低配服务器推荐配置

如果服务器 CPU 很弱，优先保证聊天不卡：

```json
{
  "engine_backend": "auto",
  "enable_pikafish_engine": true,
  "pikafish_path": "/opt/pikafish/pikafish",
  "pikafish_threads": 1,
  "pikafish_hash_mb": 16,
  "pikafish_movetime_ms": 300,
  "pikafish_failure_cooldown_seconds": 600,
  "enable_xqwlight_engine": false,
  "ai_depth": 1,
  "llm_talk_enabled": true,
  "llm_talk_timeout": 2,
  "llm_talk_failure_cooldown_seconds": 600,
  "llm_talk_max_sentences": 1,
  "llm_talk_max_chars": 35
}
```

如果不想装 Pikafish：

```json
{
  "engine_backend": "builtin",
  "ai_depth": 1,
  "llm_talk_timeout": 2,
  "llm_talk_max_sentences": 1
}
```

## 配置说明

常用配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `engine_backend` | `auto` | `auto` / `pikafish` / `xqwlight` / `builtin` |
| `move_summary_enabled` | `true` | 是否发送“你走了...我走了...”摘要 |
| `move_summary_template` | `你走了 {player_move} 我走了 {bot_move}` | 完整回合摘要模板 |
| `player_move_summary_template` | `你走了 {player_move}` | 玩家一步终局时的摘要模板 |
| `engine_details_in_chat` | `false` | 是否把搜索评分/降级原因发到聊天；默认只进日志 |
| `pikafish_path` | `pikafish` | Pikafish 可执行文件路径或 PATH 命令 |
| `pikafish_threads` | `1` | Pikafish 线程数 |
| `pikafish_hash_mb` | `16` | Pikafish Hash 内存 |
| `pikafish_movetime_ms` | `500` | 每步思考时间 |
| `pikafish_failure_cooldown_seconds` | `600` | Pikafish 失败后的跳过时间 |
| `xqwlight_depth` | `4` | xqwlight 搜索深度 |
| `xqwlight_timeout_ms` | `800` | xqwlight 单步超时 |
| `xqwlight_failure_cooldown_seconds` | `600` | xqwlight 失败后的跳过时间 |
| `ai_depth` | `1` | builtin 搜索层数 |
| `llm_talk_enabled` | `true` | Bot 走棋后是否回复 |
| `llm_extra_prompt` | 空 | 对 AstrBot 当前人格追加额外台词约束 |
| `llm_talk_template` | `{talk}` | 台词发送模板，默认不添加 `「」` |
| `llm_talk_timeout` | `3` | 回复生成超时 |
| `llm_talk_failure_cooldown_seconds` | `600` | 回复失败后的本地兜底时间 |

走法摘要模板可用变量：

```text
{player_move}  例如 e9 -> f9
{bot_move}     例如 a0 -> b0
{player_from}  玩家起点
{player_to}    玩家终点
{bot_from}     Bot 起点
{bot_to}       Bot 终点
```

Bot 回复会记录最近几手和自己最近说过的话，并在下一次生成台词时作为上下文提供给模型。

## 数据存储

插件使用 `StarTools.get_data_dir()` 保存运行数据：

- `sessions.json`：各会话当前棋局
- `boards/`：渲染后的棋盘 PNG

不同群聊/私聊会维护独立对局，AstrBot 重启后可继续读取存档。

## 代码结构

```text
main.py                     AstrBot 插件入口、命令、引擎调度、LLM 回复
engine/board.py             棋盘状态与序列化
engine/rules.py             象棋规则和合法走法
engine/ai.py                builtin 搜索 AI
engine/pikafish_adapter.py  Pikafish UCI 常驻进程适配
engine/xqwlight_adapter.py  xqwlight Java CLI 适配
render/board_image.py       棋盘 PNG 渲染
storage/session_store.py    会话棋局存档
```

## 已知限制

- 暂未实现长将、长捉等完整专业判和规则
- builtin 只是兜底搜索，不是强棋力引擎
- Pikafish 需要用户自行下载/编译并配置路径
- 若要分发 Pikafish 二进制，请遵守 Pikafish 的 GPL-3.0 许可证
- 原插件仓库未附带单独 LICENSE；公开发布 fork 前建议补充清晰授权说明

## 与原插件的关系

原插件：[zxx624/astrbot_plugin_xiangqi](https://github.com/zxx624/astrbot_plugin_xiangqi)

本分支保留原插件的核心规则层、builtin 搜索、xqwlight 适配、棋盘渲染与会话存档，在此基础上做了以下增强：

- 插件名改为 `astrbot_plugin_xiangqi_arena`
- 主类改为 `XiangqiArenaPlugin`
- 新增 Pikafish UCI 引擎适配
- 新增外部引擎失败冷却
- 新增裸坐标走棋
- 默认使用 AstrBot 当前人格生成 Bot 走棋回复
- 重写 README 和配置说明
