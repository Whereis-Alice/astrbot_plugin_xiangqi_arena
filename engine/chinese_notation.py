from __future__ import annotations

import re

from .board import BLACK, RED, Board, Move
from .parser import ParseError
from .rules import legal_moves


CHINESE_NOTATION_PATTERN = (
    r"^\s*[车俥马馬傌相象仕士帅帥将將炮砲兵卒]"
    r"[前后後中一二三四五六七八九１２３４５６７８９1-9]"
    r"[平进進退]"
    r"[一二三四五六七八九１２３４５６７８９1-9]\s*$"
)
CHINESE_NOTATION_RE = re.compile(CHINESE_NOTATION_PATTERN)

_PIECE_KIND = {
    "车": "R",
    "俥": "R",
    "马": "N",
    "馬": "N",
    "傌": "N",
    "相": "B",
    "象": "B",
    "仕": "A",
    "士": "A",
    "帅": "K",
    "帥": "K",
    "将": "K",
    "將": "K",
    "炮": "C",
    "砲": "C",
    "兵": "P",
    "卒": "P",
}
_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "１": 1,
    "２": 2,
    "３": 3,
    "４": 4,
    "５": 5,
    "６": 6,
    "７": 7,
    "８": 8,
    "９": 9,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
}
_FRONT_BACK = {"前", "后", "後", "中"}


def parse_chinese_notation(text: str, board: Board, color: str) -> Move:
    notation = re.sub(r"\s+", "", text or "")
    if not CHINESE_NOTATION_RE.fullmatch(notation):
        raise ParseError("中文走法格式示例：马八进七、炮二平五、兵三进一")

    piece_text, origin_text, action_text, target_text = notation
    kind = _PIECE_KIND[piece_text]
    action = "进" if action_text == "進" else action_text
    target = _parse_digit(target_text)

    candidates = [
        move
        for move in legal_moves(board, color)
        if move.piece.upper() == kind
        and _origin_matches(board, color, kind, origin_text, move)
        and _action_matches(color, kind, action, target, move)
    ]
    if not candidates:
        raise ParseError(f"中文走法无法匹配合法走法：{notation}")
    if len(candidates) > 1:
        raise ParseError(f"中文走法有多个可能：{notation}。请改用 a6 b6 这类坐标走法。")
    return candidates[0]


def _parse_digit(text: str) -> int:
    try:
        return _DIGITS[text]
    except KeyError as exc:
        raise ParseError(f"中文走法数字错误：{text}") from exc


def _file_number(color: str, x: int) -> int:
    if color == RED:
        return 9 - x
    return x + 1


def _origin_matches(board: Board, color: str, kind: str, origin_text: str, move: Move) -> bool:
    if origin_text not in _FRONT_BACK:
        return _file_number(color, move.from_pos[0]) == _parse_digit(origin_text)

    ranked = _ranked_piece_positions(board, color, kind)
    if not ranked:
        return False
    if origin_text == "前":
        return move.from_pos == ranked[0]
    if origin_text in {"后", "後"}:
        return move.from_pos == ranked[-1]
    middle = len(ranked) // 2
    return len(ranked) >= 3 and move.from_pos == ranked[middle]


def _ranked_piece_positions(board: Board, color: str, kind: str) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    for y, row in enumerate(board.grid):
        for x, piece in enumerate(row):
            if piece is not None and piece.upper() == kind and _piece_color(piece) == color:
                positions.append((x, y))
    reverse = color == BLACK
    return sorted(positions, key=lambda item: item[1], reverse=reverse)


def _piece_color(piece: str) -> str:
    return RED if piece.isupper() else BLACK


def _action_matches(color: str, kind: str, action: str, target: int, move: Move) -> bool:
    fx, fy = move.from_pos
    tx, ty = move.to_pos
    if action == "平":
        return fy == ty and _file_number(color, tx) == target

    forward = ty < fy if color == RED else ty > fy
    if action == "进" and not forward:
        return False
    if action == "退" and forward:
        return False

    if kind in {"N", "B", "A"}:
        return _file_number(color, tx) == target
    return fx == tx and abs(ty - fy) == target
