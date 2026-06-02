from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..engine.board import BLACK, RED, Board, PIECE_NAMES, piece_color
from ..engine.parser import format_coord


BOARD_COLOR = "#f5d89a"
BOARD_EDGE = "#8b5a24"
LINE_COLOR = "#5f3a17"
RED_COLOR = "#bd1f2d"
BLACK_COLOR = "#3d3326"
HIGHLIGHT_COLOR = "#24745f"


def render_board(board: Board, output_path: Path, scale: int = 1) -> Path:
    scale = max(1, min(scale, 2))
    cell = 72 * scale
    margin_x = 112 * scale
    margin_y = 120 * scale
    width = margin_x * 2 + cell * 8
    footer_height = 92 * scale
    height = margin_y * 2 + cell * 9 + footer_height

    image = Image.new("RGB", (width, height), "#ead3a0")
    draw = ImageDraw.Draw(image)
    font_piece = _load_font(36 * scale)
    font_small = _load_font(20 * scale)
    font_title = _load_font(32 * scale)

    left = margin_x
    top = margin_y
    right = left + cell * 8
    bottom = top + cell * 9

    _draw_board_surface(draw, width, height, left, top, right, bottom, cell, scale)
    _draw_grid(draw, left, top, right, bottom, cell, scale)
    _draw_palaces(draw, left, top, bottom, cell, scale)
    _draw_point_marks(draw, left, top, cell, scale)
    _draw_river(draw, left, right, top, cell, font_title, scale)

    for idx in range(9):
        label = chr(ord("a") + idx)
        x = left + idx * cell
        _draw_centered(draw, (x, top - 48 * scale), label, font_small, LINE_COLOR)
        _draw_centered(draw, (x, bottom + 48 * scale), label, font_small, LINE_COLOR)
    for idx in range(10):
        y = top + idx * cell
        _draw_centered(draw, (left - 40 * scale, y), str(idx), font_small, LINE_COLOR)
        _draw_centered(draw, (right + 40 * scale, y), str(idx), font_small, LINE_COLOR)

    if board.last_move is not None:
        for pos in (board.last_move.from_pos, board.last_move.to_pos):
            _highlight_cell(draw, pos, left, top, cell)

    for y, row in enumerate(board.grid):
        for x, piece in enumerate(row):
            if piece is None:
                continue
            _draw_piece(draw, piece, (x, y), left, top, cell, font_piece)

    status = f"当前行棋: {'红方' if board.side_to_move == RED else '黑方'}"
    if board.last_move is not None:
        status += f"   最近一步: {format_coord(board.last_move.from_pos)} -> {format_coord(board.last_move.to_pos)}"
    status_y = bottom + 78 * scale
    draw.rounded_rectangle(
        (34 * scale, status_y - 18 * scale, width - 34 * scale, status_y + 22 * scale),
        radius=10 * scale,
        fill="#f7e8c3",
        outline="#c49a58",
        width=max(1, scale),
    )
    draw.text((48 * scale, status_y - 11 * scale), status, fill=LINE_COLOR, font=font_small)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path


def _draw_board_surface(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
    cell: int,
    scale: int,
) -> None:
    draw.rounded_rectangle(
        (18 * scale, 18 * scale, width - 18 * scale, height - 18 * scale),
        radius=24 * scale,
        fill="#f1d7a3",
        outline=BOARD_EDGE,
        width=4 * scale,
    )
    board_box = (
        left - int(cell * 0.55),
        top - int(cell * 0.72),
        right + int(cell * 0.55),
        bottom + int(cell * 0.72),
    )
    draw.rounded_rectangle(board_box, radius=18 * scale, fill=BOARD_COLOR, outline="#b98235", width=2 * scale)
    for offset in range(0, board_box[2] - board_box[0], max(16 * scale, 1)):
        color = "#f8dfaa" if (offset // max(16 * scale, 1)) % 2 == 0 else "#edcb84"
        x = board_box[0] + offset
        draw.rectangle((x, board_box[1], min(x + 10 * scale, board_box[2]), board_box[3]), fill=color)
    draw.rounded_rectangle(board_box, radius=18 * scale, outline="#b98235", width=2 * scale)


def _draw_grid(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int, cell: int, scale: int) -> None:
    line_w = max(2 * scale, 1)
    heavy_w = max(3 * scale, line_w)
    for y in range(10):
        py = top + y * cell
        draw.line((left, py, right, py), fill=LINE_COLOR, width=heavy_w if y in {0, 9} else line_w)
    for x in range(9):
        px = left + x * cell
        width = heavy_w if x in {0, 8} else line_w
        draw.line((px, top, px, top + cell * 4), fill=LINE_COLOR, width=width)
        draw.line((px, top + cell * 5, px, bottom), fill=LINE_COLOR, width=width)


def _draw_palaces(draw: ImageDraw.ImageDraw, left: int, top: int, bottom: int, cell: int, scale: int) -> None:
    line_w = max(2 * scale, 1)
    draw.line((left + 3 * cell, top, left + 5 * cell, top + 2 * cell), fill=LINE_COLOR, width=line_w)
    draw.line((left + 5 * cell, top, left + 3 * cell, top + 2 * cell), fill=LINE_COLOR, width=line_w)
    draw.line((left + 3 * cell, bottom, left + 5 * cell, bottom - 2 * cell), fill=LINE_COLOR, width=line_w)
    draw.line((left + 5 * cell, bottom, left + 3 * cell, bottom - 2 * cell), fill=LINE_COLOR, width=line_w)


def _draw_river(
    draw: ImageDraw.ImageDraw,
    left: int,
    right: int,
    top: int,
    cell: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    scale: int,
) -> None:
    river_y = top + cell * 4 + cell // 2
    draw.rectangle((left + 2 * scale, top + cell * 4 + 3 * scale, right - 2 * scale, top + cell * 5 - 3 * scale), fill="#f7d998")
    _draw_centered(draw, (left + cell * 2, river_y), "楚 河", font, "#7a4519")
    _draw_centered(draw, (right - cell * 2, river_y), "汉 界", font, "#7a4519")


def _draw_point_marks(draw: ImageDraw.ImageDraw, left: int, top: int, cell: int, scale: int) -> None:
    for x, y in (
        (1, 2), (7, 2), (1, 7), (7, 7),
        (0, 3), (2, 3), (4, 3), (6, 3), (8, 3),
        (0, 6), (2, 6), (4, 6), (6, 6), (8, 6),
    ):
        _draw_mark(draw, left + x * cell, top + y * cell, x, scale)


def _draw_mark(draw: ImageDraw.ImageDraw, cx: int, cy: int, board_x: int, scale: int) -> None:
    gap = 8 * scale
    arm = 14 * scale
    line_w = max(2 * scale, 1)
    directions = []
    if board_x > 0:
        directions.extend([(-1, -1), (-1, 1)])
    if board_x < 8:
        directions.extend([(1, -1), (1, 1)])
    for sx, sy in directions:
        x0 = cx + sx * gap
        y0 = cy + sy * gap
        draw.line((x0, y0, x0 + sx * arm, y0), fill=LINE_COLOR, width=line_w)
        draw.line((x0, y0, x0, y0 + sy * arm), fill=LINE_COLOR, width=line_w)


def _draw_piece(draw: ImageDraw.ImageDraw, piece: str, pos: tuple[int, int], left: int, top: int, cell: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> None:
    x, y = pos
    cx = left + x * cell
    cy = top + y * cell
    radius = int(cell * 0.38)
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    shadow = (bbox[0] + 3, bbox[1] + 4, bbox[2] + 3, bbox[3] + 4)
    draw.ellipse(shadow, fill="#b28952")
    fill = "#fff4d8"
    outline = RED_COLOR if piece_color(piece) == RED else BLACK_COLOR
    text_color = outline
    draw.ellipse(bbox, fill="#d6ad67", outline="#8a6230", width=2)
    inner = (bbox[0] + 4, bbox[1] + 4, bbox[2] - 4, bbox[3] - 4)
    draw.ellipse(inner, fill=fill, outline=outline, width=3)
    ring = (bbox[0] + 10, bbox[1] + 10, bbox[2] - 10, bbox[3] - 10)
    draw.ellipse(ring, outline=outline, width=1)
    _draw_centered(draw, (cx, cy), PIECE_NAMES[piece], font, text_color)


def _highlight_cell(draw: ImageDraw.ImageDraw, pos: tuple[int, int], left: int, top: int, cell: int) -> None:
    x, y = pos
    cx = left + x * cell
    cy = top + y * cell
    span = int(cell * 0.44)
    draw.rounded_rectangle((cx - span, cy - span, cx + span, cy + span), radius=int(cell * 0.14), outline=HIGHLIGHT_COLOR, width=3)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (center[0] - (bbox[0] + bbox[2]) / 2, center[1] - (bbox[1] + bbox[3]) / 2),
        text,
        fill=fill,
        font=font,
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()
