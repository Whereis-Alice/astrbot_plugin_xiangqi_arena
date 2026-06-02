# astrbot_plugin_xiangqi_arena

**象棋竞技场** 是一个面向 AstrBot 的中国象棋对战插件。它保留原插件的本地规则校验、棋盘渲染和会话存档，并增强为更适合群聊和低配 Linux 服务器的多引擎版本。

本项目基于原插件 [zxx624/astrbot_plugin_xiangqi](https://github.com/zxx624/astrbot_plugin_xiangqi) 改造。原插件提供了坐标制走棋、本地象棋规则、xqwlight/builtin AI 和棋盘图片渲染；本增强分支新增 Pikafish、引擎失败冷却、简化走棋命令和默认人格回复。

## 主要特性

- 多引擎顺序：`pikafish_http -> Pikafish -> xqwlight -> builtin`
- Pikafish 常驻 UCI 子进程，适合 Linux 服务器
- 可选独立 Pikafish HTTP 服务，让多个象棋插件共享同一个引擎进程
- 外部引擎失败后自动冷却，避免每步都等待超时
- 低配默认参数：小 Hash、单线程、固定 movetime、短台词超时
- 对局中可直接发送 `a6 b6`、`a6-b6`、`a6b6` 走棋
- 可选中文棋谱走法：`马八进七`、`炮二平五`、`兵三进一`
- 独立 WebUI：通过 `网页下棋` 生成地址，在浏览器里点击棋盘走棋
- WebUI 与聊天共用同一个会话棋局，网页走棋可主动同步回聊天
- Bot 走棋后默认使用当前 AstrBot 会话实际生效的人格生成短回复，并带最近走法/台词记忆
- 普通聊天时也会临时注入当前棋局上下文，让 Bot 知道局势和自己之前说过什么
- LLM 回复超时后使用本地兜底台词，不影响棋局
- 本地规则层始终校验引擎返回走法，避免非法落子破坏棋局

## 命令

```text
开局          开始新局，玩家执红
执黑          开始新局，玩家执黑，Bot 先手
重开          强制重置当前会话棋局，玩家执红
重开执黑      强制重置当前会话棋局，玩家执黑
a6 b6        对局中直接走棋
a6-b6        同上
a6b6         同上
马八进七      中文棋谱走法
炮二平五      中文棋谱走法
走 a6 b6     显式走棋命令
走 马八进七   显式中文棋谱走法
棋盘          查看当前棋盘
提示          获取一步建议
悔棋          撤销上一整个回合
认输          结束当前对局
状态          查看对局和引擎冷却状态
网页下棋      生成当前会话的 WebUI 棋盘链接
Pikafish服务  查看 Pikafish HTTP 服务状态
启动Pikafish服务  由插件拉起独立 Pikafish HTTP 服务
停止Pikafish服务  停止插件自己拉起的 Pikafish HTTP 服务
重启Pikafish服务  重启插件自己拉起的 Pikafish HTTP 服务
```

兼容旧命令：`象棋新局`、`象棋执黑`、`走棋`、`象棋状态`。WebUI 链接命令也兼容 `棋局链接`、`象棋网页`、`webui`、`web`。

默认开启未结束棋局保护：同一个群/私聊已有对局时，普通 `开局` 或 `执黑` 不会覆盖当前棋局；需要明确发送 `重开` 或 `重开执黑` 才会强制重置。

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

## 中文棋谱走法

开启 `enable_chinese_notation` 后，可以直接发送传统中文棋谱走法：

```text
马八进七
炮二平五
兵三进一
```

也可以加显式命令：

```text
走 马八进七
```

中文棋谱里的 `一` 到 `九` 不是图片上的 `a-i / 0-9` 坐标，而是按当前走棋方自己的视角数“第几路”。插件会根据你执红还是执黑自动换算，再交给规则层校验。

示例：玩家执红时，红方开局左马在坐标 `b9`，传统棋谱叫 `马八`，所以：

```text
马八进七
```

等价于坐标走法：

```text
b9 c7
```

目前支持 `车/马/相/仕/帅/炮/兵` 和 `将/士/象/卒` 的常见写法，数字兼容中文数字、半角数字和全角数字。无法唯一判断或不合法时，插件会提示改用坐标走法。

注意：马、相、仕这类斜走棋子通常使用 `进/退`，例如 `马八进七`；`马八平九` 不是标准合法马走法。

## 独立 WebUI

本插件提供独立 aiohttp WebUI，不使用 AstrBot 内置 Pages。插件启动后会按配置监听一个 HTTP 地址；在聊天里发送：

```text
网页下棋
```

Bot 会返回一个带 token 的链接。打开后可以直接点击棋子和目标格走棋，也可以在网页里开局、执黑开局、重开、提示、悔棋和认输。

WebUI 与聊天端使用同一个 `session_id` 存档：

- 聊天里走棋后，网页刷新或自动轮询会看到最新局面
- 网页里走棋后，默认会主动把摘要、Bot 台词和棋盘图同步回原聊天
- 普通聊天局势注入仍然生效，Bot 在没走棋的聊天里也能知道当前局势

默认配置只监听本机：

```json
{
  "webui_enabled": true,
  "webui_host": "127.0.0.1",
  "webui_port": 8787,
  "webui_token_ttl_seconds": 86400,
  "webui_notify_chat": true,
  "webui_notify_board": true
}
```

如果你的 AstrBot 跑在 Linux 服务器上，并希望外部浏览器访问，建议用反向代理提供 HTTPS，然后填写对外地址：

```json
{
  "webui_host": "127.0.0.1",
  "webui_port": 8787,
  "webui_public_base_url": "https://example.com/xiangqi"
}
```

如果不用反代而是直接暴露端口，可把 `webui_host` 改成 `0.0.0.0`，并务必用防火墙限制来源。链接 token 默认 1 天过期；插件重启后需要重新发送 `网页下棋` 生成新链接。

## 引擎策略

默认 `engine_backend = auto`，插件会按以下顺序尝试：

1. `pikafish_http`：已配置时优先调用独立 Pikafish HTTP 服务，适合多插件共享引擎
2. `pikafish`：强棋力，推荐；通过 UCI 协议在本插件内常驻运行
3. `xqwlight`：随包 Java 引擎，作为第二外部兜底
4. `builtin`：Python 内置搜索，棋力较弱但最稳定

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

## 共享 Pikafish HTTP 服务

仓库内提供了一个可选独立服务：

```text
tools/pikafish_http_service/
```

它会维护一个常驻 Pikafish 子进程，并提供 `POST /bestmove`。这样本插件和其它支持自定义 HTTP 引擎的象棋插件可以共用同一个 Pikafish，避免每个插件各自拉起子进程。

启动示例：

```bash
cd /path/to/AstrBot/data/plugins/astrbot_plugin_xiangqi_arena/tools/pikafish_http_service
python3 -m pip install -r requirements.txt

PIKAFISH_PATH=/opt/pikafish/pikafish \
PIKAFISH_THREADS=1 \
PIKAFISH_HASH_MB=16 \
PIKAFISH_MOVETIME_MS=500 \
python3 pikafish_http_service.py
```

本插件使用共享服务：

```json
{
  "engine_backend": "auto",
  "enable_pikafish_http_engine": true,
  "pikafish_http_url": "http://127.0.0.1:8788/bestmove",
  "pikafish_http_timeout_ms": 8000,
  "pikafish_http_movetime_ms": 500
}
```

也可以让插件自己拉起这个服务。配置好 `pikafish_http_url` 和 `pikafish_path` 后，在聊天里发送：

```text
启动Pikafish服务
```

查看状态：

```text
Pikafish服务
```

停止或重启：

```text
停止Pikafish服务
重启Pikafish服务
```

如果希望插件启动时自动拉起服务：

```json
{
  "pikafish_http_service_auto_start": true,
  "pikafish_http_url": "http://127.0.0.1:8788/bestmove",
  "pikafish_path": "/opt/pikafish/pikafish"
}
```

插件只会停止自己托管启动的服务；如果你用 systemd 或手动命令启动共享服务，插件不会去停止那个外部进程。

如果要让 `zxx624/astrbot_plugin_chess_arena` 使用同一个服务，在棋擂台插件里配置：

```json
{
  "engine_mode": "custom_http",
  "custom_engine_http_url": "http://127.0.0.1:8788/bestmove",
  "engine_timeout_sec": 8
}
```

服务默认监听 `127.0.0.1:8788`，建议只给同机插件访问，不要直接暴露到公网。详细接口和 systemd 示例见 [tools/pikafish_http_service/README.md](tools/pikafish_http_service/README.md)。

## xqwlight 优化

xqwlight 保留为兜底引擎，但它是 Java CLI，每次调用都有启动和搜索开销。低配服务器建议：

```json
{
  "xqwlight_depth": 4,
  "xqwlight_timeout_ms": 800,
  "xqwlight_failure_cooldown_seconds": 0
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
  "pikafish_failure_cooldown_seconds": 0,
  "enable_xqwlight_engine": false,
  "ai_depth": 1,
  "llm_talk_enabled": true,
  "llm_talk_timeout": 2,
  "llm_talk_failure_cooldown_seconds": 0,
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
| `engine_backend` | `auto` | `auto` / `pikafish_http` / `pikafish` / `xqwlight` / `builtin` |
| `move_summary_enabled` | `true` | 是否发送“你走了...我走了...”摘要 |
| `move_summary_template` | `你走了 {player_move} 我走了 {bot_move}` | 完整回合摘要模板 |
| `player_move_summary_template` | `你走了 {player_move}` | 玩家一步终局时的摘要模板 |
| `protect_active_game_reset` | `true` | 同一会话已有未结束棋局时，普通 `开局/执黑` 是否禁止覆盖 |
| `enable_chinese_notation` | `true` | 是否启用 `马八进七` 这类中文棋谱走法 |
| `engine_details_in_chat` | `false` | 是否把搜索评分/降级原因发到聊天；默认只进日志 |
| `chat_board_context_enabled` | `true` | 普通聊天时是否临时注入当前棋局上下文 |
| `chat_board_context_max_items` | `5` | 普通聊天上下文最多附带最近几手和几句台词 |
| `webui_enabled` | `true` | 是否启动独立 WebUI |
| `webui_host` | `127.0.0.1` | WebUI 监听地址 |
| `webui_port` | `8787` | WebUI 监听端口 |
| `webui_public_base_url` | 空 | 反代或公网访问时用于生成链接的对外地址 |
| `webui_token_ttl_seconds` | `86400` | WebUI 链接有效期；0 表示插件运行期间不过期 |
| `webui_notify_chat` | `true` | 网页操作是否主动同步回原聊天 |
| `webui_notify_board` | `true` | 网页同步聊天时是否附带棋盘图 |
| `enable_pikafish_http_engine` | `false` | 是否启用独立 Pikafish HTTP 服务 |
| `pikafish_http_url` | 空 | HTTP 服务地址，例如 `http://127.0.0.1:8788/bestmove` |
| `pikafish_http_timeout_ms` | `8000` | HTTP 引擎请求总超时 |
| `pikafish_http_movetime_ms` | `500` | 传给 HTTP 服务的 Pikafish 思考时间 |
| `pikafish_http_failure_cooldown_seconds` | `0` | HTTP 引擎失败后的跳过时间；0 为关闭冷却 |
| `pikafish_http_service_auto_start` | `false` | 插件启动时是否自动拉起共享 HTTP 服务 |
| `pikafish_http_service_python` | 空 | 托管服务使用的 Python；留空使用 AstrBot 当前 Python |
| `pikafish_http_service_pikafish_path` | 空 | 托管服务使用的 Pikafish 路径；留空复用 `pikafish_path` |
| `pikafish_path` | `pikafish` | Pikafish 可执行文件路径或 PATH 命令 |
| `pikafish_threads` | `1` | Pikafish 线程数 |
| `pikafish_hash_mb` | `16` | Pikafish Hash 内存 |
| `pikafish_movetime_ms` | `500` | 每步思考时间 |
| `pikafish_failure_cooldown_seconds` | `0` | Pikafish 失败后的跳过时间；0 为关闭冷却 |
| `xqwlight_depth` | `4` | xqwlight 搜索深度 |
| `xqwlight_timeout_ms` | `800` | xqwlight 单步超时 |
| `xqwlight_failure_cooldown_seconds` | `0` | xqwlight 失败后的跳过时间；0 为关闭冷却 |
| `ai_depth` | `1` | builtin 搜索层数 |
| `llm_talk_enabled` | `true` | Bot 走棋后是否回复 |
| `llm_extra_prompt` | 空 | 对 AstrBot 当前人格追加额外台词约束 |
| `llm_talk_template` | `{talk}` | 台词发送模板，默认不添加 `「」` |
| `llm_talk_timeout` | `3` | 回复生成超时；有效范围 1-300，上游较慢可设 60-120 |
| `llm_talk_failure_cooldown_seconds` | `0` | 回复失败后的本地兜底时间；0 为关闭冷却 |

走法摘要模板可用变量：

```text
{player_move}  例如 e9 -> f9
{bot_move}     例如 a0 -> b0
{player_from}  玩家起点
{player_to}    玩家终点
{bot_from}     Bot 起点
{bot_to}       Bot 终点
```

Bot 回复会记录最近几手和自己最近说过的话，并在下一次生成台词时作为上下文提供给模型。台词生成会按 AstrBot 的会话级人格、当前对话分支人格、全局默认人格顺序解析，且默认提示词会要求人格优先，棋局只是回复内容来源。

普通聊天局势注入会在当前会话有未结束棋局时生效。插件会在 LLM 请求前附加一段临时上下文，包含双方执色、当前轮次、最近一步、最近几手、Bot 之前说过的话和 ASCII 棋盘。它不会作为可见聊天内容发送；如果用户聊的话题和棋局无关，提示词也会要求 Bot 不要生硬转回象棋。

## 数据存储

插件使用 `StarTools.get_data_dir()` 保存运行数据：

- `sessions.json`：各会话当前棋局
- `boards/`：渲染后的棋盘 PNG
- WebUI token 仅保存在内存中，插件重启后重新生成即可

不同群聊/私聊会维护独立对局，AstrBot 重启后可继续读取存档。

## 代码结构

```text
main.py                     AstrBot 插件入口、命令、引擎调度、LLM 回复
engine/board.py             棋盘状态与序列化
engine/chinese_notation.py  中文棋谱走法解析
engine/rules.py             象棋规则和合法走法
engine/ai.py                builtin 搜索 AI
engine/http_adapter.py      可选 HTTP 引擎适配
engine/pikafish_adapter.py  Pikafish UCI 常驻进程适配
engine/xqwlight_adapter.py  xqwlight Java CLI 适配
render/board_image.py       棋盘 PNG 渲染
storage/session_store.py    会话棋局存档
webui/server.py             独立 aiohttp WebUI 服务和前端页面
tools/pikafish_http_service 独立 Pikafish HTTP 共享服务
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
- 新增独立 WebUI，支持点击棋盘下棋并同步聊天
- 默认使用 AstrBot 当前人格生成 Bot 走棋回复
- 重写 README 和配置说明
