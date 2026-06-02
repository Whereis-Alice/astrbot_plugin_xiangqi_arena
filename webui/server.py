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
        return self._json(await self.plugin.webui_start_game(session_id, str(payload.get("player_color") or "red")))

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
      --bg: #f5f7f4;
      --panel: #ffffff;
      --line: #26302a;
      --muted: #66746b;
      --text: #18211b;
      --red: #b8262f;
      --black: #263238;
      --accent: #1f7a63;
      --accent-weak: #dceee8;
      --warn: #b45b26;
      --grid: min(8.3vw, 54px);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", system-ui, sans-serif;
    }

    button {
      font: inherit;
      color: inherit;
      border: 1px solid #c8d2cb;
      background: #fff;
      border-radius: 6px;
      min-height: 38px;
      padding: 0 12px;
      cursor: pointer;
    }

    button:hover { border-color: var(--accent); }
    button:disabled { color: #9aa49e; cursor: not-allowed; background: #f1f4f2; }

    .app {
      width: min(1180px, calc(100vw - 28px));
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

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 18px;
      align-items: start;
    }

    .board-wrap {
      background: #fffdf7;
      border: 1px solid #d9c9a7;
      border-radius: 8px;
      padding: 14px;
      overflow: auto;
    }

    .board-shell {
      width: calc(var(--grid) * 11);
      min-width: calc(var(--grid) * 11);
      margin: 0 auto;
      display: grid;
      grid-template-columns: var(--grid) repeat(9, var(--grid)) var(--grid);
      grid-template-rows: 26px repeat(10, var(--grid)) 26px;
      user-select: none;
    }

    .coord {
      display: grid;
      place-items: center;
      color: #755d32;
      font-size: 13px;
      line-height: 1;
    }

    .cell {
      width: var(--grid);
      height: var(--grid);
      border: 0;
      border-radius: 0;
      padding: 0;
      position: relative;
      display: grid;
      place-items: center;
      background:
        linear-gradient(#b98b42, #b98b42) center / 100% 1px no-repeat,
        linear-gradient(90deg, #b98b42, #b98b42) center / 1px 100% no-repeat,
        #f4d68f;
    }

    .cell:nth-child(odd) { background-color: #f7dfaa; }
    .cell.selected { background-color: #d9ebdf; }
    .cell.legal::after {
      content: "";
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--accent);
      opacity: 0.78;
      position: absolute;
    }
    .cell.last-from, .cell.last-to { box-shadow: inset 0 0 0 3px rgba(31, 122, 99, .45); }

    .piece {
      width: calc(var(--grid) - 8px);
      height: calc(var(--grid) - 8px);
      border-radius: 50%;
      display: grid;
      place-items: center;
      border: 2px solid currentColor;
      background: #fffaf0;
      font-size: clamp(18px, 4vw, 30px);
      font-weight: 700;
      line-height: 1;
      z-index: 1;
      box-shadow: 0 2px 4px rgba(72, 54, 24, .18);
    }

    .piece.red { color: var(--red); }
    .piece.black { color: var(--black); }
    .piece.dim { opacity: .42; }

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
    }

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
      gap: 7px;
      max-height: 216px;
      overflow: auto;
      padding-right: 2px;
      color: #36443b;
      font-size: 13px;
      line-height: 1.35;
    }

    .log div {
      border-bottom: 1px solid #eef1ef;
      padding-bottom: 7px;
    }

    @media (max-width: 900px) {
      :root { --grid: min(9.1vw, 48px); }
      .layout { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .status { text-align: left; }
      .side { grid-template-columns: 1fr; }
    }

    @media (max-width: 560px) {
      .app { width: calc(100vw - 14px); padding-top: 10px; }
      .board-wrap { padding: 8px; }
      .controls { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="app">
    <div class="topbar">
      <h1>象棋竞技场</h1>
      <div class="status" id="status">连接中</div>
    </div>
    <section class="layout">
      <div class="board-wrap">
        <div class="board-shell" id="board"></div>
      </div>
      <aside class="side">
        <section class="panel">
          <h2>操作</h2>
          <div class="controls">
            <button class="primary" data-action="new-red">开局</button>
            <button data-action="new-black">执黑</button>
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
          <h2>最近走法</h2>
          <div class="log" id="moveLog"></div>
        </section>
        <section class="panel">
          <h2>Bot 台词</h2>
          <div class="log" id="talkLog"></div>
        </section>
      </aside>
    </section>
  </main>
  <script>
    const token = new URLSearchParams(location.search).get("token") || "";
    const boardEl = document.getElementById("board");
    const statusEl = document.getElementById("status");
    const messageEl = document.getElementById("message");
    const moveLogEl = document.getElementById("moveLog");
    const talkLogEl = document.getElementById("talkLog");
    let state = null;
    let selected = null;
    let busy = false;

    const files = ["a","b","c","d","e","f","g","h","i"];
    const ranks = ["0","1","2","3","4","5","6","7","8","9"];

    function coord(x, y) { return files[x] + ranks[y]; }

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
      const legal = selected ? legalTargets(selected) : new Set();
      const last = state.last_move || {};

      addCorner();
      files.forEach(file => addCoord(file));
      addCorner();
      state.grid.forEach((row, y) => {
        addCoord(String(y));
        row.forEach((piece, x) => addCell(piece, x, y, legal, last));
        addCoord(String(y));
      });
      addCorner();
      files.forEach(file => addCoord(file));
      addCorner();

      renderLog(moveLogEl, state.move_log);
      renderLog(talkLogEl, state.talk_log);
      document.querySelector('[data-action="undo"]').disabled = busy || !state.can_undo;
      document.querySelector('[data-action="hint"]').disabled = busy || !state.game_active || state.turn_owner !== "player";
      document.querySelector('[data-action="resign"]').disabled = busy || !state.game_active;
    }

    function addCorner() {
      const node = document.createElement("div");
      node.className = "coord";
      boardEl.appendChild(node);
    }

    function addCoord(text) {
      const node = document.createElement("div");
      node.className = "coord";
      node.textContent = text;
      boardEl.appendChild(node);
    }

    function addCell(piece, x, y, legal, last) {
      const c = coord(x, y);
      const node = document.createElement("button");
      node.className = "cell";
      node.dataset.coord = c;
      if (selected === c) node.classList.add("selected");
      if (legal.has(c)) node.classList.add("legal");
      if (last.from === c) node.classList.add("last-from");
      if (last.to === c) node.classList.add("last-to");
      node.addEventListener("click", () => onCellClick(c, piece));
      if (piece) {
        const pieceNode = document.createElement("div");
        pieceNode.className = "piece " + piece.color + (isOwnPiece(piece) ? "" : " dim");
        pieceNode.textContent = piece.name;
        node.appendChild(pieceNode);
      }
      boardEl.appendChild(node);
    }

    function renderLog(target, items) {
      target.innerHTML = "";
      (items || []).slice().reverse().forEach(item => {
        const node = document.createElement("div");
        node.textContent = item;
        target.appendChild(node);
      });
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
        if (data.state) state = data.state;
        if (!data.ok) {
          setMessage(data.error || "操作失败。", true);
        } else {
          const talk = Array.isArray(data.talk) && data.talk.length ? "\n" + data.talk.join("\n") : "";
          setMessage((data.message || "") + talk);
        }
      } catch (err) {
        setMessage(String(err), true);
      } finally {
        busy = false;
        render();
      }
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
        state = data.state;
        if (firstLoad || !state.game_active) selected = null;
        render();
      } catch (err) {
        setMessage(String(err), true);
      }
    }

    document.querySelector('[data-action="new-red"]').addEventListener("click", () => post("api/new", { player_color: "red" }));
    document.querySelector('[data-action="new-black"]').addEventListener("click", () => post("api/new", { player_color: "black" }));
    document.querySelector('[data-action="hint"]').addEventListener("click", () => post("api/hint"));
    document.querySelector('[data-action="undo"]').addEventListener("click", () => post("api/undo"));
    document.querySelector('[data-action="resign"]').addEventListener("click", () => post("api/resign"));

    loadState();
    setInterval(loadState, 5000);
  </script>
</body>
</html>
"""
