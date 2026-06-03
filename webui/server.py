from __future__ import annotations

import json
import secrets
import time
from typing import Any
from urllib.parse import quote

from astrbot.api import logger


try:
    from aiohttp import web
except Exception:  # pragma: no cover - handled at runtime with a clear log.
    web = None


PLUGIN_LOG_NAME = "xiangqi_arena"


class XiangqiWebServer:
    def __init__(
        self,
        plugin: Any,
        host: str,
        port: int,
        public_base_url: str = "",
        token_ttl_seconds: int = 86400,
    ):
        self.plugin = plugin
        self.host = host
        self.port = port
        self.public_base_url = public_base_url.strip().rstrip("/")
        self.token_ttl_seconds = token_ttl_seconds
        self._tokens: dict[str, tuple[str, float]] = {}
        self._runner: Any = None
        self._site: Any = None
        self._bound_port = port

    @property
    def is_running(self) -> bool:
        return self._runner is not None and self._site is not None

    async def start(self) -> None:
        if web is None:
            raise RuntimeError("aiohttp is not installed; please install plugin requirements.txt")
        if self.is_running:
            return
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/state", self._handle_state)
        app.router.add_post("/api/new", self._handle_new)
        app.router.add_post("/api/move", self._handle_move)
        app.router.add_post("/api/undo", self._handle_undo)
        app.router.add_post("/api/resign", self._handle_resign)
        app.router.add_post("/api/hint", self._handle_hint)
        app.router.add_get("/healthz", self._handle_health)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._bound_port = self._detect_bound_port()
        logger.info("%s webui listening on %s", PLUGIN_LOG_NAME, self.base_url)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url
        host = self.host or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self._bound_port}"

    def issue_url(self, session_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + self.token_ttl_seconds if self.token_ttl_seconds > 0 else 0.0
        self._tokens[token] = (session_id, expires_at)
        self._cleanup_tokens()
        return f"{self.base_url}/?token={quote(token)}"

    def _detect_bound_port(self) -> int:
        try:
            sockets = getattr(getattr(self._site, "_server", None), "sockets", None) or []
            if sockets:
                return int(sockets[0].getsockname()[1])
        except Exception:
            pass
        return self.port

    async def _handle_index(self, _request: Any) -> Any:
        return web.Response(text=WEB_HTML, content_type="text/html", charset="utf-8")

    async def _handle_health(self, _request: Any) -> Any:
        return self._json({"ok": True, "name": "xiangqi_arena_webui"})

    async def _handle_state(self, request: Any) -> Any:
        session_id, error = self._session_from_token(request.query.get("token", ""))
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json({"ok": True, "state": await self.plugin.webui_get_state(session_id)})

    async def _handle_new(self, request: Any) -> Any:
        payload = await self._read_json(request)
        session_id, error = self._session_from_payload(request, payload)
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json(
            await self.plugin.webui_start_game(
                session_id,
                str(payload.get("player_color") or "red"),
                force=bool(payload.get("force")),
            )
        )

    async def _handle_move(self, request: Any) -> Any:
        payload = await self._read_json(request)
        session_id, error = self._session_from_payload(request, payload)
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json(
            await self.plugin.webui_move(
                session_id,
                str(payload.get("from") or ""),
                str(payload.get("to") or ""),
            )
        )

    async def _handle_undo(self, request: Any) -> Any:
        payload = await self._read_json(request)
        session_id, error = self._session_from_payload(request, payload)
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json(await self.plugin.webui_undo(session_id))

    async def _handle_resign(self, request: Any) -> Any:
        payload = await self._read_json(request)
        session_id, error = self._session_from_payload(request, payload)
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json(await self.plugin.webui_resign(session_id))

    async def _handle_hint(self, request: Any) -> Any:
        payload = await self._read_json(request)
        session_id, error = self._session_from_payload(request, payload)
        if error:
            return self._json({"ok": False, "error": error}, status=403)
        return self._json(await self.plugin.webui_hint(session_id))

    async def _read_json(self, request: Any) -> dict[str, Any]:
        try:
            payload = await request.json()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _session_from_payload(self, request: Any, payload: dict[str, Any]) -> tuple[str, str]:
        token = str(payload.get("token") or request.query.get("token") or "")
        return self._session_from_token(token)

    def _session_from_token(self, token: str) -> tuple[str, str]:
        if not token:
            return "", "缺少 WebUI token，请在聊天里发送“棋局链接”重新获取。"
        record = self._tokens.get(token)
        if record is None:
            return "", "WebUI token 无效或已随插件重启失效，请重新获取链接。"
        session_id, expires_at = record
        if expires_at and expires_at < time.time():
            self._tokens.pop(token, None)
            return "", "WebUI token 已过期，请重新获取链接。"
        return session_id, ""

    def _cleanup_tokens(self) -> None:
        now = time.time()
        expired = [token for token, (_session_id, expires_at) in self._tokens.items() if expires_at and expires_at < now]
        for token in expired:
            self._tokens.pop(token, None)

    def _json(self, payload: dict[str, Any], status: int = 200) -> Any:
        return web.json_response(payload, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False))


WEB_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>象棋竞技场</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef3f0;
      --panel: #fbfcfa;
      --line: #4e3318;
      --muted: #65736c;
      --text: #17211c;
      --red: #b91f33;
      --black: #3a3127;
      --accent: #1f7a63;
      --accent-weak: #dceee8;
      --warn: #a95525;
      --board: #f3d38d;
      --board-deep: #b77f32;
      --board-outer: #f4d99f;
      --board-outer-edge: #9a6a2d;
      --board-panel: #f7d994;
      --board-panel-edge: #b98637;
      --board-paper-a: #f8dfaa;
      --board-paper-b: #f0ca7d;
      --board-paper-c: #f7e0ad;
      --board-river: #f7d994;
      --board-line: #5b3718;
      --board-text: #764719;
      --piece-fill: #fff6dc;
      --piece-base: #d6ad67;
      --piece-shadow: rgba(72, 54, 24, .26);
      --grid: min(7.2vw, 62px);
    }

    body[data-board-theme="jade"] {
      --board-outer: #dcebdc;
      --board-outer-edge: #2d6b55;
      --board-panel: #d8e4bd;
      --board-panel-edge: #6f9b67;
      --board-paper-a: #e9f1ca;
      --board-paper-b: #cddda8;
      --board-paper-c: #eef4d2;
      --board-river: #dfe9bf;
      --board-line: #284936;
      --board-text: #2d6b55;
      --piece-base: #b8c88b;
    }

    body[data-theme="dark"] {
      color-scheme: dark;
      --bg: #101820;
      --panel: #17232c;
      --line: #d0a867;
      --muted: #9cafb6;
      --text: #e8edf0;
      --accent: #63cdb6;
      --accent-weak: rgba(99, 205, 182, .16);
      --warn: #f0a66e;
    }

    body[data-board-theme="dark"] {
      --red: rgba(228, 64, 82, .74);
      --black: rgba(64, 55, 45, .72);
      --board-outer: #1b2632;
      --board-outer-edge: #9f7943;
      --board-panel: #23313a;
      --board-panel-edge: #a78049;
      --board-paper-a: #263a42;
      --board-paper-b: #202e36;
      --board-paper-c: #314850;
      --board-river: #2b3f43;
      --board-line: #d0a867;
      --board-text: #e5bd77;
      --piece-fill: #f3e1bd;
      --piece-base: #a77a4f;
      --piece-shadow: rgba(0, 0, 0, .45);
    }

    body[data-board-theme="paper"] {
      --board-outer: #f6ecd2;
      --board-outer-edge: #7c5b32;
      --board-panel: #eee0bd;
      --board-panel-edge: #a47b48;
      --board-paper-a: #f7efdc;
      --board-paper-b: #e5d2aa;
      --board-paper-c: #fbf2dc;
      --board-river: #f1dfb8;
      --board-line: #4f3a21;
      --board-text: #76512b;
      --piece-base: #c7a66c;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, rgba(31, 122, 99, .08), transparent 220px),
        var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif;
    }

    button, select {
      font: inherit;
      color: inherit;
      border: 1px solid #c8d2cb;
      background: linear-gradient(180deg, #ffffff, #f8faf8);
      border-radius: 6px;
      min-height: 38px;
      padding: 0 12px;
      cursor: pointer;
    }

    body[data-theme="dark"] button,
    body[data-theme="dark"] select {
      border-color: #334653;
      background: linear-gradient(180deg, #22313d, #192630);
    }

    button:hover, select:hover { border-color: var(--accent); }
    button:disabled { color: #9aa49e; cursor: not-allowed; background: #f1f4f2; }
    body[data-theme="dark"] button:disabled { color: #667780; background: #18242d; }

    .app {
      width: min(1480px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 18px 0 22px;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 14px;
    }

    h1 {
      margin: 0;
      font-size: 24px;
      line-height: 1.2;
      font-weight: 700;
      letter-spacing: 0;
    }

    .status {
      color: var(--muted);
      font-size: 14px;
      text-align: right;
      line-height: 1.4;
    }

    .top-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-left: auto;
    }

    .theme-control {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }

    .theme-control select {
      min-height: 34px;
      padding: 0 30px 0 10px;
    }

    .sound-toggle {
      min-height: 34px;
      min-width: 42px;
      padding: 0 10px;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(560px, 1fr) minmax(340px, 420px);
      gap: 18px;
      align-items: start;
    }

    .board-wrap {
      background:
        linear-gradient(180deg, #fffefa, #f8f2e3);
      border: 1px solid #d5bd8b;
      border-radius: 8px;
      padding: 14px;
      overflow: hidden;
      box-shadow: 0 12px 34px rgba(57, 43, 22, .08);
      display: grid;
      gap: 10px;
      justify-items: center;
    }

    body[data-theme="dark"] .board-wrap {
      background: linear-gradient(180deg, #16232d, #111b23);
      border-color: #35495a;
    }

    .board-shell {
      width: min(100%, 900px, calc((100vh - 142px) * 1000 / 1120));
      aspect-ratio: 1000 / 1120;
      margin: 0 auto;
      position: relative;
      user-select: none;
      min-width: 420px;
    }

    .board-head {
      width: min(100%, 900px, calc((100vh - 142px) * 1000 / 1120));
      min-width: 420px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 7px;
      justify-items: center;
      align-items: center;
      text-align: center;
      color: var(--text);
    }

    .turn-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 0 12px;
      border-radius: 999px;
      background: var(--accent-weak);
      color: var(--accent);
      font-size: 14px;
      font-weight: 700;
      white-space: nowrap;
    }

    .turn-pill.thinking::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
      box-shadow: 0 0 0 0 rgba(31, 122, 99, .35);
      animation: pulse 1.2s ease-out infinite;
    }

    .progress-line {
      display: grid;
      gap: 5px;
      min-width: 0;
      width: min(100%, 420px);
      justify-items: center;
    }

    .progress-text {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: center;
    }

    .progress-track {
      height: 5px;
      width: 100%;
      border-radius: 999px;
      background: rgba(88, 73, 48, .16);
      overflow: hidden;
    }

    .progress-fill {
      height: 100%;
      width: 0;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #d49a42);
      transition: width .28s ease;
    }

    .board-svg {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      display: block;
      filter: drop-shadow(0 10px 14px rgba(93, 60, 20, .10));
    }

    .coord {
      position: absolute;
      transform: translate(-50%, -50%);
      color: var(--board-text);
      font-size: clamp(12px, 1.2vw, 15px);
      line-height: 1;
      font-weight: 600;
      z-index: 2;
      pointer-events: none;
    }

    .intersection {
      width: 7.8%;
      aspect-ratio: 1;
      border: 0;
      border-radius: 50%;
      padding: 0;
      position: absolute;
      transform: translate(-50%, -50%);
      display: grid;
      place-items: center;
      background: transparent;
      z-index: 4;
    }

    .intersection,
    .intersection:disabled {
      border: 0;
      background: transparent;
    }

    .intersection.selected {
      background: rgba(31, 122, 99, .12);
      box-shadow: 0 0 0 3px rgba(31, 122, 99, .42);
      animation: selectedBreath 1.35s ease-in-out infinite;
    }

    .intersection.legal::after {
      content: "";
      width: 15px;
      height: 15px;
      border-radius: 50%;
      background: var(--accent);
      opacity: 0.86;
      position: absolute;
      box-shadow: 0 0 0 5px rgba(31, 122, 99, .12);
    }
    .intersection.last-from, .intersection.last-to { box-shadow: 0 0 0 3px rgba(31, 122, 99, .45); }
    .intersection.last-to .piece { animation: pieceDrop .28s cubic-bezier(.2, 1.4, .35, 1); }

    .piece {
      width: 82%;
      height: 82%;
      border-radius: 50%;
      display: grid;
      place-items: center;
      border: 2px solid currentColor;
      background:
        radial-gradient(circle at 32% 26%, #fffaf0 0 22%, var(--piece-fill) 52%, var(--piece-base) 100%);
      font-size: clamp(17px, 2.9vw, 28px);
      font-weight: 700;
      line-height: 1;
      z-index: 1;
      box-shadow:
        inset 0 0 0 4px rgba(255, 255, 255, .42),
        inset 0 0 0 9px rgba(121, 78, 24, .10),
        0 4px 10px var(--piece-shadow);
      transition: transform .16s ease, opacity .16s ease, filter .16s ease;
    }

    .intersection:hover .piece { transform: translateY(-1px); }
    .intersection.selected .piece { transform: translateY(-2px) scale(1.06); filter: saturate(1.12); }
    .piece-text {
      display: block;
      line-height: 1;
      opacity: .68;
      transform: translateY(-0.035em);
    }
    .piece.red { color: var(--red); }
    .piece.black { color: var(--black); }
    .piece.dim { opacity: .68; }

    .side {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid #d9e0dc;
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 8px 18px rgba(25, 37, 30, .04);
    }

    body[data-theme="dark"] .panel { border-color: #2c3d49; }

    .panel h2 {
      margin: 0 0 10px;
      font-size: 15px;
      line-height: 1.2;
    }

    .controls {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }

    .primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }

    .danger:hover { border-color: var(--warn); color: var(--warn); }

    .message {
      min-height: 36px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .message.error { color: #9a2f2f; }

    .log {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 360px;
      overflow: auto;
      padding-right: 4px;
      color: #36443b;
      font-size: 13px;
      line-height: 1.35;
      scrollbar-width: thin;
    }

    body[data-theme="dark"] .log { color: #d7e1e4; }

    .log-card {
      border: 1px solid rgba(110, 125, 116, .18);
      border-radius: 8px;
      padding: 10px 11px;
      background: rgba(255, 255, 255, .58);
      display: grid;
      gap: 7px;
    }

    body[data-theme="dark"] .log-card {
      background: rgba(255, 255, 255, .035);
      border-color: rgba(255, 255, 255, .08);
    }

    .log-title {
      font-weight: 700;
      color: var(--text);
    }

    .timeline-meta {
      display: flex;
      align-items: baseline;
      gap: 7px;
      font-size: 13px;
      font-weight: 700;
      color: var(--text);
    }

    .timeline-side.red { color: var(--red); }
    .timeline-side.black { color: var(--black); }
    body[data-theme="dark"] .timeline-side.black { color: #d8c08a; }

    .timeline-actor {
      color: var(--muted);
      font-weight: 600;
    }

    .timeline-move {
      color: var(--text);
      font-size: 14px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .timeline-coord {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 12px;
    }

    .timeline-talk {
      color: var(--muted);
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .log-row {
      color: var(--muted);
      overflow-wrap: anywhere;
    }

    .move-chip {
      display: inline-flex;
      align-items: center;
      min-height: 20px;
      padding: 0 6px;
      margin-right: 6px;
      border-radius: 4px;
      background: rgba(31, 122, 99, .10);
      color: var(--accent);
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 12px;
    }

    @keyframes selectedBreath {
      0%, 100% { box-shadow: 0 0 0 3px rgba(31, 122, 99, .36), 0 0 0 0 rgba(31, 122, 99, .20); }
      50% { box-shadow: 0 0 0 3px rgba(31, 122, 99, .58), 0 0 0 9px rgba(31, 122, 99, .08); }
    }

    @keyframes pieceDrop {
      0% { transform: translateY(-18%) scale(.92); opacity: .72; }
      70% { transform: translateY(3%) scale(1.04); opacity: 1; }
      100% { transform: translateY(0) scale(1); }
    }

    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(31, 122, 99, .35); }
      100% { box-shadow: 0 0 0 10px rgba(31, 122, 99, 0); }
    }

    @media (max-width: 900px) {
      :root { --grid: min(8.6vw, 52px); }
      .layout { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .top-actions { margin-left: 0; }
      .status { text-align: left; }
      .side { grid-template-columns: 1fr; }
    }

    @media (max-width: 560px) {
      .app { width: calc(100vw - 14px); padding-top: 10px; }
      .board-wrap { padding: 8px; }
      .board-shell, .board-head { min-width: 340px; }
      .board-head { grid-template-columns: 1fr; }
      .controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="app">
    <div class="topbar">
      <h1>象棋竞技场</h1>
      <div class="top-actions">
        <label class="theme-control">
          <span>页面</span>
          <select id="pageThemeSelect" aria-label="页面主题">
            <option value="light">日间</option>
            <option value="dark">夜间</option>
          </select>
        </label>
        <label class="theme-control">
          <span>棋盘</span>
          <select id="boardThemeSelect" aria-label="棋盘皮肤">
            <option value="classic">经典</option>
            <option value="jade">青玉</option>
            <option value="dark">夜战</option>
            <option value="paper">宣纸</option>
          </select>
        </label>
        <button class="sound-toggle" id="soundToggle" type="button" title="落子音效">声</button>
      </div>
      <div class="status" id="status">连接中</div>
    </div>
    <section class="layout">
      <div class="board-wrap">
        <div class="board-head">
          <div class="turn-pill" id="turnPill">等待棋局</div>
          <div class="progress-line">
            <div class="progress-text" id="progressText"></div>
            <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
          </div>
        </div>
        <div class="board-shell" id="board"></div>
      </div>
      <aside class="side">
        <section class="panel">
          <h2>操作</h2>
          <div class="controls">
            <button class="primary" data-action="new-red">开局</button>
            <button data-action="new-black">执黑</button>
            <button data-action="restart-red">重开</button>
            <button data-action="restart-black">重开执黑</button>
            <button data-action="hint">提示</button>
            <button data-action="undo">悔棋</button>
            <button class="danger" data-action="resign">认输</button>
          </div>
        </section>
        <section class="panel">
          <h2>回合</h2>
          <div class="message" id="message"></div>
        </section>
        <section class="panel">
          <h2>棋局记录</h2>
          <div class="log" id="timelineLog"></div>
        </section>
      </aside>
    </section>
  </main>
  <script>
    const token = new URLSearchParams(location.search).get("token") || "";
    const boardEl = document.getElementById("board");
    const statusEl = document.getElementById("status");
    const messageEl = document.getElementById("message");
    const timelineLogEl = document.getElementById("timelineLog");
    const pageThemeSelect = document.getElementById("pageThemeSelect");
    const boardThemeSelect = document.getElementById("boardThemeSelect");
    const soundToggle = document.getElementById("soundToggle");
    const turnPillEl = document.getElementById("turnPill");
    const progressTextEl = document.getElementById("progressText");
    const progressFillEl = document.getElementById("progressFill");
    let state = null;
    let selected = null;
    let busy = false;
    let audioCtx = null;
    let soundEnabled = localStorage.getItem("xiangqi_sound") !== "off";
    let lastMoveKey = "";
    let pollTimer = 0;

    const files = ["a","b","c","d","e","f","g","h","i"];
    const ranks = ["0","1","2","3","4","5","6","7","8","9"];
    const boardView = { w: 1000, h: 1120, left: 172, top: 130, cell: 82 };

    function coord(x, y) { return files[x] + ranks[y]; }
    function pointX(x) { return boardView.left + x * boardView.cell; }
    function pointY(y) { return boardView.top + y * boardView.cell; }
    function pctX(value) { return (value / boardView.w * 100) + "%"; }
    function pctY(value) { return (value / boardView.h * 100) + "%"; }

    function applyPageTheme(theme) {
      const selectedTheme = ["light", "dark"].includes(theme) ? theme : "light";
      document.body.dataset.theme = selectedTheme;
      pageThemeSelect.value = selectedTheme;
      localStorage.setItem("xiangqi_page_theme", selectedTheme);
    }

    function applyBoardTheme(theme) {
      const selectedTheme = ["classic", "jade", "dark", "paper"].includes(theme) ? theme : "classic";
      document.body.dataset.boardTheme = selectedTheme;
      boardThemeSelect.value = selectedTheme;
      localStorage.setItem("xiangqi_board_theme", selectedTheme);
    }

    function updateSoundButton() {
      soundToggle.textContent = soundEnabled ? "声" : "静";
      soundToggle.classList.toggle("primary", soundEnabled);
      soundToggle.title = soundEnabled ? "关闭落子音效" : "开启落子音效";
    }

    function ensureAudio() {
      if (!soundEnabled) return null;
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) return null;
      if (!audioCtx) audioCtx = new Audio();
      if (audioCtx.state === "suspended") void audioCtx.resume();
      return audioCtx;
    }

    function unlockAudio() {
      const ctx = ensureAudio();
      if (!ctx) return;
      if (ctx.state === "suspended") void ctx.resume();
    }

    function playTone(start, freq, duration, gainValue, type = "triangle") {
      const ctx = ensureAudio();
      if (!ctx) return;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, start);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(gainValue, start + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + duration + 0.02);
    }

    function playSound(kind) {
      const ctx = ensureAudio();
      if (!ctx) return;
      const now = ctx.currentTime;
      if (kind === "capture") {
        playTone(now, 220, .08, .13, "square");
        playTone(now + .055, 150, .10, .11, "triangle");
      } else {
        playTone(now, 180, .075, .10, "triangle");
        playTone(now + .045, 260, .06, .065, "sine");
      }
    }

    function moveKey(move) {
      if (!move) return "";
      const captured = move.captured && move.captured.code ? move.captured.code : "";
      return [move.from, move.to, move.piece && move.piece.code, captured].join(":");
    }

    function setMessage(text, error = false) {
      messageEl.textContent = text || "";
      messageEl.classList.toggle("error", error);
    }

    function legalTargets(from) {
      if (!state) return new Set();
      return new Set((state.legal_moves || []).filter(m => m.from === from).map(m => m.to));
    }

    function isOwnPiece(piece) {
      return piece && state && state.game_active && state.turn_owner === "player" && piece.color === state.player_color;
    }

    function render() {
      boardEl.innerHTML = "";
      if (!state) return;
      statusEl.textContent = state.status + "  " + state.session;
      const turnText = state.processing_label || (state.game_active ? "当前行棋：" + state.side_to_move_label : state.status);
      turnPillEl.textContent = turnText;
      turnPillEl.classList.toggle("thinking", !!state.processing);
      progressTextEl.textContent = state.progress ? state.progress.label : "";
      const ply = state.progress ? state.progress.ply : 0;
      progressFillEl.style.width = (ply <= 0 ? 0 : Math.min(100, Math.max(6, ply * 4))) + "%";
      const legal = selected ? legalTargets(selected) : new Set();
      const last = state.last_move || {};

      boardEl.appendChild(renderBoardSvg());
      addCoordinates();
      state.grid.forEach((row, y) => {
        row.forEach((piece, x) => addIntersection(piece, x, y, legal, last));
      });

      renderTimeline(timelineLogEl, state.timeline);
      document.querySelector('[data-action="undo"]').disabled = busy || !state.can_undo;
      document.querySelector('[data-action="hint"]').disabled = busy || !state.game_active || state.turn_owner !== "player";
      document.querySelector('[data-action="resign"]').disabled = busy || !state.game_active;
    }

    function renderBoardSvg() {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", `0 0 ${boardView.w} ${boardView.h}`);
      svg.setAttribute("class", "board-svg");
      svg.setAttribute("aria-hidden", "true");
      const left = boardView.left;
      const top = boardView.top;
      const cell = boardView.cell;
      const right = pointX(8);
      const bottom = pointY(9);
      const riverTop = pointY(4);
      const riverBottom = pointY(5);
      const markup = [];
      markup.push(`<defs>
        <linearGradient id="boardPaper" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stop-color="var(--board-paper-a)"/>
          <stop offset=".55" stop-color="var(--board-paper-b)"/>
          <stop offset="1" stop-color="var(--board-paper-c)"/>
        </linearGradient>
      </defs>`);
      markup.push(`<rect x="38" y="38" width="924" height="972" rx="28" fill="var(--board-outer)" stroke="var(--board-outer-edge)" stroke-width="5"/>`);
      markup.push(`<rect x="74" y="76" width="852" height="858" rx="18" fill="url(#boardPaper)" stroke="var(--board-panel-edge)" stroke-width="3"/>`);
      markup.push(`<rect x="${left}" y="${riverTop + 3}" width="${right - left}" height="${riverBottom - riverTop - 6}" fill="var(--board-river)" opacity=".96"/>`);
      for (let y = 0; y < 10; y += 1) {
        const py = pointY(y);
        const width = y === 0 || y === 9 ? 3.2 : 2.1;
        markup.push(`<line x1="${left}" y1="${py}" x2="${right}" y2="${py}" stroke="var(--board-line)" stroke-width="${width}" stroke-linecap="square"/>`);
      }
      for (let x = 0; x < 9; x += 1) {
        const px = pointX(x);
        const width = x === 0 || x === 8 ? 3.2 : 2.1;
        markup.push(`<line x1="${px}" y1="${top}" x2="${px}" y2="${riverTop}" stroke="var(--board-line)" stroke-width="${width}" stroke-linecap="square"/>`);
        markup.push(`<line x1="${px}" y1="${riverBottom}" x2="${px}" y2="${bottom}" stroke="var(--board-line)" stroke-width="${width}" stroke-linecap="square"/>`);
      }
      markup.push(`<line x1="${pointX(3)}" y1="${top}" x2="${pointX(5)}" y2="${pointY(2)}" stroke="var(--board-line)" stroke-width="2.2"/>`);
      markup.push(`<line x1="${pointX(5)}" y1="${top}" x2="${pointX(3)}" y2="${pointY(2)}" stroke="var(--board-line)" stroke-width="2.2"/>`);
      markup.push(`<line x1="${pointX(3)}" y1="${bottom}" x2="${pointX(5)}" y2="${pointY(7)}" stroke="var(--board-line)" stroke-width="2.2"/>`);
      markup.push(`<line x1="${pointX(5)}" y1="${bottom}" x2="${pointX(3)}" y2="${pointY(7)}" stroke="var(--board-line)" stroke-width="2.2"/>`);
      [[1,2],[7,2],[1,7],[7,7],[0,3],[2,3],[4,3],[6,3],[8,3],[0,6],[2,6],[4,6],[6,6],[8,6]].forEach(([x, y]) => {
        markup.push(markerMarkup(pointX(x), pointY(y), x));
      });
      markup.push(`<text x="${pointX(2)}" y="${riverTop + cell * .62}" text-anchor="middle" font-size="38" font-weight="700" fill="var(--board-text)" font-family="KaiTi, STKaiti, SimSun, serif">楚 河</text>`);
      markup.push(`<text x="${pointX(6)}" y="${riverTop + cell * .62}" text-anchor="middle" font-size="38" font-weight="700" fill="var(--board-text)" font-family="KaiTi, STKaiti, SimSun, serif">汉 界</text>`);
      svg.innerHTML = markup.join("");
      return svg;
    }

    function markerMarkup(cx, cy, boardX) {
      const gap = 11;
      const arm = 18;
      const parts = [];
      const dirs = [];
      if (boardX > 0) dirs.push([-1, -1], [-1, 1]);
      if (boardX < 8) dirs.push([1, -1], [1, 1]);
      dirs.forEach(([sx, sy]) => {
        const x = cx + sx * gap;
        const y = cy + sy * gap;
        parts.push(`<path d="M ${x} ${y} h ${sx * arm} M ${x} ${y} v ${sy * arm}" stroke="var(--board-line)" stroke-width="2" fill="none" stroke-linecap="square"/>`);
      });
      return parts.join("");
    }

    function addCoordinates() {
      files.forEach((file, x) => {
        addCoord(file, pointX(x), pointY(0) - 58);
        addCoord(file, pointX(x), pointY(9) + 58);
      });
      ranks.forEach((rank, y) => {
        addCoord(rank, pointX(0) - 58, pointY(y));
        addCoord(rank, pointX(8) + 58, pointY(y));
      });
    }

    function addCoord(text, x, y) {
      const node = document.createElement("span");
      node.className = "coord";
      node.textContent = text;
      node.style.left = pctX(x);
      node.style.top = pctY(y);
      boardEl.appendChild(node);
    }

    function addIntersection(piece, x, y, legal, last) {
      const c = coord(x, y);
      const node = document.createElement("button");
      node.className = "intersection";
      node.dataset.coord = c;
      node.style.left = pctX(pointX(x));
      node.style.top = pctY(pointY(y));
      node.setAttribute("aria-label", c + (piece ? " " + piece.name : ""));
      if (selected === c) node.classList.add("selected");
      if (legal.has(c)) node.classList.add("legal");
      if (last.from === c) node.classList.add("last-from");
      if (last.to === c) node.classList.add("last-to");
      node.addEventListener("click", () => onCellClick(c, piece));
      if (piece) {
        const pieceNode = document.createElement("div");
        pieceNode.className = "piece " + piece.color + (isOwnPiece(piece) ? "" : " dim");
        const textNode = document.createElement("span");
        textNode.className = "piece-text";
        textNode.textContent = piece.name;
        pieceNode.appendChild(textNode);
        node.appendChild(pieceNode);
      }
      boardEl.appendChild(node);
    }

    function renderTimeline(target, items) {
      target.innerHTML = "";
      const list = items || [];
      list.slice().reverse().forEach((item, offset) => {
        const card = document.createElement("div");
        card.className = "log-card";
        const meta = document.createElement("div");
        meta.className = "timeline-meta";
        const sideClass = item.side === "red" ? "red" : item.side === "black" ? "black" : "";
        const indexNode = document.createElement("span");
        indexNode.textContent = "#" + (item.index || (list.length - offset));
        const sideNode = document.createElement("span");
        sideNode.className = "timeline-side " + sideClass;
        sideNode.textContent = item.side_short || item.side_label || "";
        const actorNode = document.createElement("span");
        actorNode.className = "timeline-actor";
        actorNode.textContent = item.name || "";
        meta.appendChild(indexNode);
        meta.appendChild(sideNode);
        meta.appendChild(actorNode);
        const move = document.createElement("div");
        move.className = "timeline-move";
        move.textContent = item.notation || (item.move && item.move.text) || "";
        const coord = item.coord_compact || item.coord || "";
        if (coord) {
          const coordNode = document.createElement("span");
          coordNode.className = "timeline-coord";
          coordNode.textContent = "（" + coord + "）";
          move.appendChild(document.createTextNode(" "));
          move.appendChild(coordNode);
        }
        const talk = document.createElement("div");
        talk.className = "timeline-talk";
        talk.textContent = item.talk || "";
        card.appendChild(meta);
        card.appendChild(move);
        if (talk.textContent) card.appendChild(talk);
        target.appendChild(card);
      });
      if (!items || !items.length) {
        const empty = document.createElement("div");
        empty.className = "log-card";
        empty.textContent = "暂无记录";
        target.appendChild(empty);
      }
    }

    async function onCellClick(c, piece) {
      if (busy || !state || !state.game_active || state.turn_owner !== "player") return;
      if (!selected) {
        if (isOwnPiece(piece)) {
          selected = c;
          setMessage("已选 " + c);
          render();
        }
        return;
      }
      if (selected === c) {
        selected = null;
        setMessage("");
        render();
        return;
      }
      if (isOwnPiece(piece)) {
        selected = c;
        setMessage("已选 " + c);
        render();
        return;
      }
      if (legalTargets(selected).has(c)) {
        const from = selected;
        selected = null;
        await post("api/move", { from, to: c });
        return;
      }
      setMessage("这一步不合法。", true);
      selected = null;
      render();
    }

    async function post(path, body = {}) {
      unlockAudio();
      if (!token) {
        setMessage("缺少 token，请在聊天里发送 棋局链接。", true);
        return;
      }
      busy = true;
      render();
      try {
        const resp = await fetch(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token, ...body })
        });
        const data = await resp.json();
        if (data.sound) playSound(data.sound);
        if (data.state) {
          applyServerState(data.state, { playServerMove: false });
          lastMoveKey = moveKey(data.state.last_move);
        }
        if (!data.ok) {
          setMessage(data.error || "操作失败。", true);
        } else {
          const talk = Array.isArray(data.talk) && data.talk.length ? "\n" + data.talk.join("\n") : "";
          const suffix = data.pending_bot ? "\nBot 正在思考..." : "";
          setMessage((data.message || "") + talk + suffix);
        }
      } catch (err) {
        setMessage(String(err), true);
      } finally {
        busy = false;
        render();
        schedulePoll();
      }
    }

    function applyServerState(nextState, options = {}) {
      const previousKey = moveKey(state && state.last_move);
      state = nextState;
      const nextKey = moveKey(state && state.last_move);
      if (options.playServerMove && nextKey && nextKey !== previousKey && nextKey !== lastMoveKey) {
        playSound(state.last_move && state.last_move.captured ? "capture" : "move");
        lastMoveKey = nextKey;
      }
      if (!state.processing && messageEl.textContent.includes("Bot 正在思考")) {
        setMessage("");
      }
      render();
    }

    function schedulePoll() {
      clearTimeout(pollTimer);
      const delay = state && state.processing ? 850 : 5000;
      pollTimer = setTimeout(loadState, delay);
    }

    async function loadState() {
      if (!token) {
        statusEl.textContent = "未绑定棋局";
        setMessage("请在聊天里发送 棋局链接 获取地址。", true);
        return;
      }
      try {
        const resp = await fetch("api/state?token=" + encodeURIComponent(token));
        const data = await resp.json();
        if (!data.ok) {
          setMessage(data.error || "无法载入棋局。", true);
          return;
        }
        const firstLoad = state === null;
        if (firstLoad || !data.state.game_active || data.state.turn_owner !== "player") selected = null;
        applyServerState(data.state, { playServerMove: !firstLoad });
      } catch (err) {
        setMessage(String(err), true);
      } finally {
        schedulePoll();
      }
    }

    document.querySelector('[data-action="new-red"]').addEventListener("click", () => post("api/new", { player_color: "red" }));
    document.querySelector('[data-action="new-black"]').addEventListener("click", () => post("api/new", { player_color: "black" }));
    document.querySelector('[data-action="restart-red"]').addEventListener("click", () => post("api/new", { player_color: "red", force: true }));
    document.querySelector('[data-action="restart-black"]').addEventListener("click", () => post("api/new", { player_color: "black", force: true }));
    document.querySelector('[data-action="hint"]').addEventListener("click", () => post("api/hint"));
    document.querySelector('[data-action="undo"]').addEventListener("click", () => post("api/undo"));
    document.querySelector('[data-action="resign"]').addEventListener("click", () => post("api/resign"));
    pageThemeSelect.addEventListener("change", () => applyPageTheme(pageThemeSelect.value));
    boardThemeSelect.addEventListener("change", () => applyBoardTheme(boardThemeSelect.value));
    soundToggle.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      localStorage.setItem("xiangqi_sound", soundEnabled ? "on" : "off");
      updateSoundButton();
      if (soundEnabled) playSound("move");
    });

    applyPageTheme(localStorage.getItem("xiangqi_page_theme") || "light");
    applyBoardTheme(localStorage.getItem("xiangqi_board_theme") || localStorage.getItem("xiangqi_theme") || "classic");
    updateSoundButton();
    loadState();
  </script>
</body>
</html>
"""
