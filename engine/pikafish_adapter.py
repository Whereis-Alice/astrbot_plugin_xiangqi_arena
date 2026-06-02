from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from .board import BLACK, RED, Board, Move, piece_color
from .rules import legal_moves


FILES = "abcdefghi"


class PikafishError(Exception):
    """Raised when the Pikafish UCI engine cannot provide a legal move."""


def board_to_uci_fen(board: Board, color: str) -> str:
    rows: list[str] = []
    for row in board.grid:
        empty = 0
        fen_row: list[str] = []
        for piece in row:
            if piece is None:
                empty += 1
                continue
            if empty:
                fen_row.append(str(empty))
                empty = 0
            fen_row.append(piece)
        if empty:
            fen_row.append(str(empty))
        rows.append("".join(fen_row))
    side = "w" if color == RED else "b"
    return "/".join(rows) + f" {side} - - 0 1"


def parse_uci_bestmove(text: str, board: Board, color: str) -> Move:
    move_text = _extract_coord_move(text)
    if move_text is None:
        raise PikafishError(f"Pikafish 没有返回有效走法: {text.strip() or '<empty>'}")

    from_pos = _coord_to_pos(move_text[:2])
    to_pos = _coord_to_pos(move_text[2:])
    piece = board.get_piece(from_pos)
    if piece is None:
        raise PikafishError(f"Pikafish 走法起点无棋子: {move_text}")
    if piece_color(piece) != color:
        raise PikafishError(f"Pikafish 走了错误颜色棋子: {move_text}")

    for move in legal_moves(board, color):
        if move.from_pos == from_pos and move.to_pos == to_pos:
            return move
    raise PikafishError(f"Pikafish 返回非法走法: {move_text}")


class PikafishEngine:
    def __init__(
        self,
        executable: str | Path | None = None,
        working_dir: str | Path | None = None,
        eval_file: str | Path | None = None,
        threads: int = 1,
        hash_mb: int = 16,
        movetime_ms: int = 500,
        startup_timeout: float = 5.0,
        move_overhead_ms: int = 30,
    ) -> None:
        self.executable = str(executable or "pikafish").strip() or "pikafish"
        self.working_dir = Path(working_dir).expanduser() if working_dir else None
        self.eval_file = str(eval_file or "").strip()
        self.threads = max(1, min(int(threads or 1), 8))
        self.hash_mb = max(8, min(int(hash_mb or 16), 1024))
        self.movetime_ms = max(50, min(int(movetime_ms or 500), 10000))
        self.startup_timeout = max(1.0, min(float(startup_timeout or 5.0), 30.0))
        self.move_overhead_ms = max(0, min(int(move_overhead_ms or 30), 1000))
        self.proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task | None = None

    async def choose_move(self, board: Board, color: str) -> tuple[Move | None, str]:
        async with self._lock:
            try:
                await self._ensure_ready()
                fen = board_to_uci_fen(board, color)
                await self._send(f"position fen {fen}")
                await self._send(f"go movetime {self.movetime_ms}")
                line = await self._read_until(
                    lambda item: item.startswith("bestmove"),
                    timeout=self.movetime_ms / 1000 + 2.0,
                )
                if "bestmove (none)" in line:
                    return None, "Pikafish 没有合法走法"
                move = parse_uci_bestmove(line, board, color)
                return move, f"pikafish movetime {self.movetime_ms}ms"
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
            await self._read_until(lambda item: item == "readyok", timeout=2.0)
            return
        await self._start()

    async def _start(self) -> None:
        executable = self._resolve_executable()
        cwd = str(self.working_dir) if self.working_dir else None
        self.proc = await asyncio.create_subprocess_exec(
            executable,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._send("uci")
        await self._read_until(lambda item: item == "uciok", timeout=self.startup_timeout)
        await self._setoption("Threads", str(self.threads))
        await self._setoption("Hash", str(self.hash_mb))
        await self._setoption("Move Overhead", str(self.move_overhead_ms))
        if self.eval_file:
            await self._setoption("EvalFile", self.eval_file)
        await self._send("isready")
        await self._read_until(lambda item: item == "readyok", timeout=self.startup_timeout)

    def _resolve_executable(self) -> str:
        value = self.executable
        candidate = Path(value).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = shutil.which(value)
        if resolved:
            return resolved
        raise PikafishError(f"找不到 Pikafish 可执行文件: {value}")

    async def _setoption(self, name: str, value: str) -> None:
        await self._send(f"setoption name {name} value {value}")

    async def _send(self, command: str) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            raise PikafishError("Pikafish 进程未运行")
        await self._send_to(proc, command)

    async def _send_to(self, proc: asyncio.subprocess.Process, command: str) -> None:
        if proc.stdin is None:
            raise PikafishError("Pikafish stdin 不可用")
        proc.stdin.write((command + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _read_until(self, predicate, timeout: float) -> str:
        proc = self.proc
        if proc is None or proc.stdout is None:
            raise PikafishError("Pikafish stdout 不可用")
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            if not raw:
                raise PikafishError("Pikafish 进程已退出")
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            lines.append(line)
            if predicate(line):
                return line
        tail = " | ".join(lines[-5:])
        raise PikafishError(f"Pikafish 响应超时: {tail or '<no output>'}")

    async def _drain_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    return
        except asyncio.CancelledError:
            return


def _extract_coord_move(text: str) -> str | None:
    for token in text.replace("\r", "\n").split():
        token = token.strip().lower()
        if token == "bestmove":
            continue
        if len(token) == 4 and token[0] in FILES and token[2] in FILES and token[1].isdigit() and token[3].isdigit():
            if 0 <= int(token[1]) <= 9 and 0 <= int(token[3]) <= 9:
                return token
    return None


def _coord_to_pos(coord: str) -> tuple[int, int]:
    if len(coord) != 2 or coord[0] not in FILES or not coord[1].isdigit():
        raise PikafishError(f"坐标格式错误: {coord}")
    x = FILES.index(coord[0])
    y = int(coord[1])
    if y < 0 or y > 9:
        raise PikafishError(f"坐标越界: {coord}")
    return x, y
