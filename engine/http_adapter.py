from __future__ import annotations

import json
from typing import Any

from .board import BLACK, RED, Board, Move
from .pikafish_adapter import board_to_uci_fen, parse_uci_bestmove
from .rules import legal_moves


FILES = "abcdefghi"


class HttpEngineError(Exception):
    """Raised when a custom HTTP engine cannot provide a legal move."""


def move_to_ucci(move: Move) -> str:
    return _pos_to_ucci(move.from_pos) + _pos_to_ucci(move.to_pos)


def _pos_to_ucci(pos: tuple[int, int]) -> str:
    x, y = pos
    if x < 0 or x >= len(FILES) or y < 0 or y > 9:
        raise HttpEngineError(f"坐标越界: {pos}")
    return FILES[x] + str(9 - y)


async def choose_move_http(
    *,
    board: Board,
    color: str,
    url: str,
    timeout_ms: int,
    movetime_ms: int,
    headers: dict[str, str] | None = None,
) -> tuple[Move | None, str]:
    try:
        import aiohttp
    except ImportError as exc:  # pragma: no cover - dependency should be installed by requirements.txt.
        raise HttpEngineError("aiohttp 未安装，请安装插件 requirements.txt") from exc

    endpoint = str(url or "").strip()
    if not endpoint:
        raise HttpEngineError("未配置 pikafish_http_url")

    legal = [move_to_ucci(move) for move in legal_moves(board, color)]
    if not legal:
        return None, "http engine 没有合法走法"

    payload = {
        "fen": board_to_uci_fen(board, color),
        "legal_moves": legal,
        "side": "red" if color == RED else "black" if color == BLACK else color,
        "timeout_ms": timeout_ms,
        "movetime_ms": movetime_ms,
        "source": "astrbot_plugin_xiangqi_arena",
    }
    timeout = aiohttp.ClientTimeout(total=max(1.0, timeout_ms / 1000))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(endpoint, json=payload, headers=headers or {}) as response:
            text = await response.text()
            if response.status >= 400:
                raise HttpEngineError(f"HTTP {response.status}: {text[:200]}")
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError as exc:
                raise HttpEngineError(f"HTTP 引擎返回非 JSON: {text[:200]}") from exc

    move_text = str(data.get("best_move") or data.get("move") or "").strip().lower()
    if not move_text:
        raise HttpEngineError(f"HTTP 引擎未返回 best_move: {data}")
    if move_text not in legal:
        raise HttpEngineError(f"HTTP 引擎返回非法走法 {move_text}; legal={legal[:12]}")

    move = parse_uci_bestmove(f"bestmove {move_text}", board, color)
    reason = _response_reason(data)
    return move, reason


def _response_reason(data: dict[str, Any]) -> str:
    engine = str(data.get("engine") or "http").strip() or "http"
    info = str(data.get("info") or "").strip()
    score = data.get("score")
    parts = [engine]
    if info and info.lower() != engine.lower():
        parts.append(info)
    if score is not None:
        parts.append(f"score {score}")
    return " ".join(parts)
