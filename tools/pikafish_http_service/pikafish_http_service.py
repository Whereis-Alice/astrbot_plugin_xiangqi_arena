from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from aiohttp import web


FILES = "abcdefghi"
MOVE_RE = re.compile(r"^[a-i][0-9][a-i][0-9]$")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788


class PikafishServiceError(RuntimeError):
    pass


class PikafishUciEngine:
    def __init__(
        self,
        *,
        executable: str,
        working_dir: str = "",
        eval_file: str = "",
        threads: int = 1,
        hash_mb: int = 16,
        movetime_ms: int = 500,
        startup_timeout: float = 5.0,
        move_overhead_ms: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self.executable = executable.strip() or "pikafish"
        self.working_dir = Path(working_dir).expanduser() if working_dir else None
        self.eval_file = eval_file.strip()
        self.threads = clamp_int(threads, 1, 1, 8)
        self.hash_mb = clamp_int(hash_mb, 16, 8, 1024)
        self.movetime_ms = clamp_int(movetime_ms, 500, 50, 10000)
        self.startup_timeout = clamp_float(startup_timeout, 5.0, 1.0, 30.0)
        self.move_overhead_ms = clamp_int(move_overhead_ms, 30, 0, 1000)
        self.logger = logger or logging.getLogger(__name__)
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task | None = None

    async def bestmove(
        self,
        *,
        fen: str,
        legal_moves: list[str],
        movetime_ms: int | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            try:
                await self._ensure_ready()
                effective_timeout = clamp_int(timeout_ms, self.movetime_ms + 2000, 100, 60000)
                effective_movetime = clamp_int(movetime_ms, self.movetime_ms, 50, 10000)
                if effective_timeout > 200:
                    effective_movetime = min(effective_movetime, max(50, effective_timeout - 200))

                await self._send(f"position fen {fen}")
                await self._send(f"go movetime {effective_movetime}")
                best_line, lines = await self._read_until(
                    lambda line: line.startswith("bestmove"),
                    timeout=effective_timeout / 1000,
                    label="bestmove",
                )
                move = extract_bestmove(best_line)
                if not move:
                    raise PikafishServiceError(f"Pikafish did not return a move: {best_line}")
                if legal_moves and move not in legal_moves:
                    raise PikafishServiceError(
                        f"Pikafish returned illegal move {move}; legal sample={legal_moves[:12]}"
                    )
                score = extract_score(lines)
                return {
                    "engine": "pikafish",
                    "best_move": move,
                    "move": move,
                    "score": score,
                    "info": f"pikafish movetime {effective_movetime}ms",
                    "raw_bestmove": best_line,
                }
            except Exception:
                await self.close()
                raise

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        if proc is None:
            return
        try:
            await self._send_to(proc, "quit")
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception:
            proc.kill()
            await proc.wait()

    async def _ensure_ready(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            await self._send("isready")
            await self._read_until(lambda line: line == "readyok", timeout=2.0, label="readyok")
            return
        await self._start()

    async def _start(self) -> None:
        executable = self._resolve_executable()
        cwd = str(self.working_dir) if self.working_dir else None
        self.logger.info(
            "starting Pikafish: executable=%r cwd=%r threads=%s hash_mb=%s movetime_ms=%s",
            executable,
            cwd,
            self.threads,
            self.hash_mb,
            self.movetime_ms,
        )
        self.proc = await asyncio.create_subprocess_exec(
            executable,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._send("uci")
        await self._read_until(lambda line: line == "uciok", timeout=self.startup_timeout, label="uciok")
        await self._setoption("Threads", str(self.threads))
        await self._setoption("Hash", str(self.hash_mb))
        await self._setoption("Move Overhead", str(self.move_overhead_ms))
        if self.eval_file:
            await self._setoption("EvalFile", self.eval_file)
        await self._send("isready")
        await self._read_until(lambda line: line == "readyok", timeout=self.startup_timeout, label="startup readyok")

    def _resolve_executable(self) -> str:
        candidate = Path(self.executable).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = shutil.which(self.executable)
        if resolved:
            return resolved
        raise PikafishServiceError(f"Pikafish executable not found: {self.executable}")

    async def _setoption(self, name: str, value: str) -> None:
        await self._send(f"setoption name {name} value {value}")

    async def _send(self, command: str) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            raise PikafishServiceError("Pikafish process is not running")
        await self._send_to(proc, command)

    async def _send_to(self, proc: asyncio.subprocess.Process, command: str) -> None:
        if proc.stdin is None:
            raise PikafishServiceError("Pikafish stdin is unavailable")
        proc.stdin.write((command + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _read_until(self, predicate, timeout: float, label: str = "response") -> tuple[str, list[str]]:
        proc = self.proc
        if proc is None or proc.stdout is None:
            raise PikafishServiceError("Pikafish stdout is unavailable")
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                tail = " | ".join(lines[-8:])
                raise PikafishServiceError(
                    f"Pikafish timed out waiting for {label} after {timeout:.1f}s: {tail or '<no output>'}"
                ) from exc
            if not raw:
                raise PikafishServiceError("Pikafish process exited")
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            lines.append(line)
            if predicate(line):
                return line, lines
        tail = " | ".join(lines[-8:])
        raise PikafishServiceError(f"Pikafish timed out waiting for {label} after {timeout:.1f}s: {tail or '<no output>'}")

    async def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    return
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    self.logger.warning("pikafish stderr: %s", text)
        except asyncio.CancelledError:
            return


def normalize_fen(fen: str, side: str = "") -> str:
    value = str(fen or "").strip()
    if not value:
        raise PikafishServiceError("missing fen")
    parts = value.split()
    if len(parts) == 1:
        side_part = normalize_side(side) or "w"
        parts.extend([side_part, "-", "-", "0", "1"])
    else:
        parts[1] = normalize_side(parts[1]) or normalize_side(side) or "w"
        if len(parts) == 2:
            parts.extend(["-", "-", "0", "1"])
    return " ".join(parts)


def normalize_side(side: str) -> str:
    value = str(side or "").strip().lower()
    if value in {"w", "r", "red", "white"}:
        return "w"
    if value in {"b", "black"}:
        return "b"
    return ""


def normalize_legal_moves(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    moves: list[str] = []
    for item in value:
        move = str(item or "").strip().lower()
        if MOVE_RE.match(move):
            moves.append(move)
    return moves


def extract_bestmove(text: str) -> str:
    for token in text.replace("\r", "\n").split():
        move = token.strip().lower()
        if MOVE_RE.match(move):
            return move
    return ""


def extract_score(lines: list[str]) -> int | None:
    for line in reversed(lines):
        tokens = line.split()
        for index, token in enumerate(tokens[:-1]):
            if token == "score" and tokens[index + 1] == "cp" and index + 2 < len(tokens):
                try:
                    return int(tokens[index + 2])
                except ValueError:
                    return None
            if token == "score" and tokens[index + 1] == "mate" and index + 2 < len(tokens):
                try:
                    mate = int(tokens[index + 2])
                except ValueError:
                    return None
                return 100000 if mate > 0 else -100000
    return None


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    return clamp_int(os.getenv(name), default, minimum, maximum)


def env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    return clamp_float(os.getenv(name), default, minimum, maximum)


def create_app(engine: PikafishUciEngine, logger: logging.Logger) -> web.Application:
    app = web.Application()
    app["engine"] = engine
    app["logger"] = logger

    async def cleanup(_app: web.Application):
        await engine.close()

    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/bestmove", handle_bestmove)
    return app


async def handle_health(request: web.Request) -> web.Response:
    engine: PikafishUciEngine = request.app["engine"]
    status = "running" if engine.proc is not None and engine.proc.returncode is None else "idle"
    return web.json_response({"ok": True, "engine": "pikafish", "status": status})


async def handle_bestmove(request: web.Request) -> web.Response:
    engine: PikafishUciEngine = request.app["engine"]
    logger: logging.Logger = request.app["logger"]
    payload: dict[str, Any] = {}
    fen = ""
    legal_moves: list[str] = []
    timeout_ms = 0
    movetime_ms = 0
    try:
        raw_payload = await request.json()
        if not isinstance(raw_payload, dict):
            raise PikafishServiceError("request body must be a JSON object")
        payload = raw_payload
        fen = normalize_fen(str(payload.get("fen") or ""), str(payload.get("side") or payload.get("turn") or ""))
        legal_moves = normalize_legal_moves(payload.get("legal_moves") or payload.get("legalMoves") or [])
        timeout_ms = clamp_int(payload.get("timeout_ms"), 8000, 100, 60000)
        movetime_ms = clamp_int(payload.get("movetime_ms"), engine.movetime_ms, 50, 10000)
        result = await engine.bestmove(
            fen=fen,
            legal_moves=legal_moves,
            movetime_ms=movetime_ms,
            timeout_ms=timeout_ms,
        )
        result["fen"] = fen
        return web.json_response(result)
    except Exception as exc:  # noqa: BLE001 - HTTP services should return JSON errors.
        error_text = format_error(exc)
        logger.warning(
            "bestmove failed: %s; fen=%r legal_sample=%s timeout_ms=%s movetime_ms=%s payload_keys=%s",
            error_text,
            fen,
            legal_moves[:12],
            timeout_ms,
            movetime_ms,
            sorted(payload.keys()),
            exc_info=True,
        )
        return web.json_response(
            {
                "ok": False,
                "engine": "pikafish",
                "error": error_text,
                "error_type": type(exc).__name__,
                "fen": fen,
                "legal_moves_count": len(legal_moves),
                "legal_moves_sample": legal_moves[:12],
            },
            status=422,
        )


def format_error(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return repr(exc) or type(exc).__name__


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pikafish HTTP service for xiangqi engines.")
    parser.add_argument("--host", default=os.getenv("PIKAFISH_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=env_int("PIKAFISH_PORT", DEFAULT_PORT, 1, 65535))
    parser.add_argument("--pikafish", default=os.getenv("PIKAFISH_PATH", "pikafish"))
    parser.add_argument("--working-dir", default=os.getenv("PIKAFISH_WORKING_DIR", ""))
    parser.add_argument("--eval-file", default=os.getenv("PIKAFISH_EVAL_FILE", ""))
    parser.add_argument("--threads", type=int, default=env_int("PIKAFISH_THREADS", 1, 1, 8))
    parser.add_argument("--hash-mb", type=int, default=env_int("PIKAFISH_HASH_MB", 16, 8, 1024))
    parser.add_argument("--movetime-ms", type=int, default=env_int("PIKAFISH_MOVETIME_MS", 500, 50, 10000))
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=env_float("PIKAFISH_STARTUP_TIMEOUT", 5.0, 1.0, 30.0),
    )
    parser.add_argument(
        "--move-overhead-ms",
        type=int,
        default=env_int("PIKAFISH_MOVE_OVERHEAD_MS", 30, 0, 1000),
    )
    parser.add_argument("--log-level", default=os.getenv("PIKAFISH_LOG_LEVEL", "INFO"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("pikafish_http_service")
    engine = PikafishUciEngine(
        executable=args.pikafish,
        working_dir=args.working_dir,
        eval_file=args.eval_file,
        threads=args.threads,
        hash_mb=args.hash_mb,
        movetime_ms=args.movetime_ms,
        startup_timeout=args.startup_timeout,
        move_overhead_ms=args.move_overhead_ms,
        logger=logger,
    )
    app = create_app(engine, logger)
    logger.info("listening on http://%s:%s", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
