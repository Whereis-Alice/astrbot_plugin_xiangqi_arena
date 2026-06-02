# Pikafish HTTP Service

这是一个可选的独立 Pikafish HTTP 引擎服务，用来让多个象棋插件共享同一个常驻 Pikafish 子进程。

它不会随 AstrBot 自动启动，也不依赖 AstrBot Pages。你可以把它单独跑在 Linux 服务器上，然后让：

- `astrbot_plugin_xiangqi_arena` 使用 `engine_backend = pikafish_http`
- `zxx624/astrbot_plugin_chess_arena` 使用 `engine_mode = custom_http`

如果你不想手动跑命令，也可以在 `astrbot_plugin_xiangqi_arena` 里发送 `启动Pikafish服务`，让插件托管启动本服务。

## 安装

```bash
cd /path/to/AstrBot/data/plugins/astrbot_plugin_xiangqi_arena/tools/pikafish_http_service
python3 -m pip install -r requirements.txt
```

下载或编译 Pikafish 后放到 `/opt/pikafish/pikafish`：

```bash
chmod +x /opt/pikafish/pikafish
```

## 启动

低配服务器推荐：

```bash
PIKAFISH_PATH=/opt/pikafish/pikafish \
PIKAFISH_THREADS=1 \
PIKAFISH_HASH_MB=16 \
PIKAFISH_MOVETIME_MS=500 \
python3 pikafish_http_service.py
```

默认监听：

```text
http://127.0.0.1:8788
```

只建议监听 `127.0.0.1`，让同机插件调用。不要直接公网暴露这个服务。

## 接口

### `GET /health`

返回服务状态。

### `POST /bestmove`

请求：

```json
{
  "fen": "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR r - - 0 1",
  "legal_moves": ["b0c2", "h0g2"],
  "side": "red",
  "timeout_ms": 8000,
  "movetime_ms": 500
}
```

响应：

```json
{
  "engine": "pikafish",
  "best_move": "b0c2",
  "move": "b0c2",
  "score": 32,
  "info": "pikafish movetime 500ms"
}
```

`legal_moves` 使用 UCCI 坐标：`0` 是红方底线，`9` 是黑方底线。棋擂台插件下发的 `legal_moves` 就是这种坐标，直接转发即可。

如果 Pikafish 超时、崩溃、路径错误，或者返回不在 `legal_moves` 里的走法，服务会返回 `422` JSON，并在服务日志里记录原因。

## 给 astrbot_plugin_chess_arena 使用

在棋擂台插件配置里填写：

```json
{
  "engine_mode": "custom_http",
  "custom_engine_http_url": "http://127.0.0.1:8788/bestmove",
  "engine_timeout_sec": 8
}
```

建议 `engine_mode` 直接使用 `custom_http`。如果使用 `auto`，HTTP 服务失败后棋擂台插件还会继续尝试自己的 `local_xqwlight`；某些 Node 环境会把 `analyze.js` 当 ES module，出现 `require is not defined in ES module scope`，那是棋擂台插件本地 xqwlight 兜底的问题。

棋擂台插件仍会校验：

```text
best_move in legal_moves
```

所以服务异常不会破坏棋局，最多回退到棋擂台插件自己的后续引擎或随机合法走法。

如果返回 HTTP 422，请看响应里的 `error` 和 `error_type`。新版服务会把超时、Pikafish 路径错误、`uciok/readyok` 失败、非法走法等原因写进 JSON 和服务日志。

## 给 astrbot_plugin_xiangqi_arena 使用

在本插件配置里填写：

```json
{
  "engine_backend": "pikafish_http",
  "enable_pikafish_http_engine": true,
  "pikafish_http_url": "http://127.0.0.1:8788/bestmove",
  "pikafish_http_timeout_ms": 8000,
  "pikafish_http_movetime_ms": 500
}
```

然后可以发送：

```text
启动Pikafish服务
Pikafish服务
停止Pikafish服务
重启Pikafish服务
```

也可以开启自动启动：

```json
{
  "pikafish_http_service_auto_start": true,
  "pikafish_http_service_startup_wait_seconds": 6
}
```

如果低配服务器提示健康检查暂未通过，可把 `pikafish_http_service_startup_wait_seconds` 调到 `10` 或 `15`，然后发送 `Pikafish服务` 查看最近服务日志。

如果你还想保留本插件内置的本地 Pikafish 作为兜底，可以使用：

```json
{
  "engine_backend": "auto",
  "enable_pikafish_http_engine": true,
  "pikafish_http_url": "http://127.0.0.1:8788/bestmove"
}
```

此时顺序为：

```text
pikafish_http -> pikafish -> xqwlight -> builtin
```

## systemd 示例

`/etc/systemd/system/pikafish-http.service`：

```ini
[Unit]
Description=Pikafish HTTP xiangqi engine
After=network.target

[Service]
WorkingDirectory=/path/to/AstrBot/data/plugins/astrbot_plugin_xiangqi_arena/tools/pikafish_http_service
Environment=PIKAFISH_PATH=/opt/pikafish/pikafish
Environment=PIKAFISH_HOST=127.0.0.1
Environment=PIKAFISH_PORT=8788
Environment=PIKAFISH_THREADS=1
Environment=PIKAFISH_HASH_MB=16
Environment=PIKAFISH_MOVETIME_MS=500
ExecStart=/usr/bin/python3 pikafish_http_service.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pikafish-http.service
sudo systemctl status pikafish-http.service
```
