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

THEMES: dict[str, dict[str, str]] = {
    "classic": {
        "page": "#ead3a0",
        "outer": "#f1d7a3",
        "outer_edge": BOARD_EDGE,
        "board": BOARD_COLOR,
        "board_edge": "#b98235",
        "stripe_a": "#f8dfaa",
        "stripe_b": "#edcb84",
        "river": "#f7d998",
        "line": LINE_COLOR,
        "river_text": "#7a4519",
        "red": "#c43846",
        "black": "#3b3024",
        "piece_fill": "#fff6dd",
        "piece_base": "#d1aa68",
        "piece_shadow": "#9c7a4a",
        "status_fill": "#f7e8c3",
        "highlight": HIGHLIGHT_COLOR,
    },
    "jade": {
        "page": "#d7e5d9",
        "outer": "#dcebdc",
        "outer_edge": "#2d6b55",
        "board": "#d8e4bd",
        "board_edge": "#6f9b67",
        "stripe_a": "#e9f1ca",
        "stripe_b": "#cddda8",
        "river": "#dfe9bf",
        "line": "#284936",
        "river_text": "#2d6b55",
        "red": "#cf3448",
        "black": "#2c4638",
        "piece_fill": "#fffbe8",
        "piece_base": "#b6ca8e",
        "piece_shadow": "#80956d",
        "status_fill": "#eef5dc",
        "highlight": "#237a68",
    },
    "dark": {
        "page": "#111820",
        "outer": "#1b2632",
        "outer_edge": "#9f7943",
        "board": "#23313a",
        "board_edge": "#a78049",
        "stripe_a": "#263a42",
        "stripe_b": "#202e36",
        "river": "#2b3f43",
        "line": "#d0a867",
        "river_text": "#e5bd77",
        "red": "#ff6d80",
        "black": "#213d34",
        "piece_fill": "#ead8b6",
        "piece_base": "#9c7a4c",
        "piece_shadow": "#05080b",
        "black_piece_fill": "#d9d8bd",
        "black_piece_base": "#667a58",
        "black_piece_shadow": "#020405",
        "status_fill": "#1f2d36",
        "highlight": "#69d5bd",
    },
    "paper": {
        "page": "#efe7d3",
        "outer": "#f6ecd2",
        "outer_edge": "#7c5b32",
        "board": "#eee0bd",
        "board_edge": "#a47b48",
        "stripe_a": "#f7efdc",
        "stripe_b": "#e5d2aa",
        "river": "#f1dfb8",
        "line": "#4f3a21",
        "river_text": "#76512b",
        "red": "#b43642",
        "black": "#3b3026",
        "piece_fill": "#fff9e9",
        "piece_base": "#d7bd84",
        "piece_shadow": "#ad9668",
        "status_fill": "#fbf3df",
        "highlight": "#2e7565",
    },
}


def _theme(name: str | None) -> dict[str, str]:
    return THEMES.get((name or "classic").lower(), THEMES["classic"])


def render_board(board: Board, output_path: Path, scale: int = 1, theme: str = "classic") -> Path:
    scale = max(1, min(scale, 2))
    output_scale = scale
    scale *= 2
    colors = _theme(theme)
    cell = 72 * scale
    margin_x = 112 * scale
    margin_y = 120 * scale
    width = margin_x * 2 + cell * 8
    footer_height = 92 * scale
    height = margin_y * 2 + cell * 9 + footer_height

    image = Image.new("RGB", (width, height), colors["page"])
    draw = ImageDraw.Draw(image)
    font_piece = _load_font(32 * scale)
    font_small = _load_font(20 * scale)
    font_title = _load_font(32 * scale)

    left = margin_x
    top = margin_y
    right = left + cell * 8
    bottom = top + cell * 9

    _draw_board_surface(draw, width, height, left, top, right, bottom, cell, scale, colors)
    _draw_grid(draw, left, top, right, bottom, cell, scale, colors)
    _draw_palaces(draw, left, top, bottom, cell, scale, colors)
    _draw_point_marks(draw, left, top, cell, scale, colors)
    _draw_river(draw, left, right, top, cell, font_title, scale, colors)

    for idx in range(9):
        label = chr(ord("a") + idx)
        x = left + idx * cell
        _draw_centered(draw, (x, top - 48 * scale), label, font_small, colors["line"])
        _draw_centered(draw, (x, bottom + 48 * scale), label, font_small, colors["line"])
    for idx in range(10):
        y = top + idx * cell
        _draw_centered(draw, (left - 40 * scale, y), str(idx), font_small, colors["line"])
        _draw_centered(draw, (right + 40 * scale, y), str(idx), font_small, colors["line"])

    if board.last_move is not None:
        for pos in (board.last_move.from_pos, board.last_move.to_pos):
            _highlight_cell(draw, pos, left, top, cell, colors)

    for y, row in enumerate(board.grid):
        for x, piece in enumerate(row):
            if piece is None:
                continue
            _draw_piece(draw, piece, (x, y), left, top, cell, font_piece, scale, colors)

    status = f"当前行棋: {'红方' if board.side_to_move == RED else '黑方'}"
    if board.last_move is not None:
        status += f"   最近一步: {format_coord(board.last_move.from_pos)} -> {format_coord(board.last_move.to_pos)}"
    status_y = bottom + 78 * scale
    draw.rounded_rectangle(
        (34 * scale, status_y - 18 * scale, width - 34 * scale, status_y + 22 * scale),
        radius=10 * scale,
        fill=colors["status_fill"],
        outline=colors["board_edge"],
        width=max(1, scale),
    )
    draw.text((48 * scale, status_y - 11 * scale), status, fill=colors["line"], font=font_small)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if scale != output_scale:
        target_size = (width * output_scale // scale, height * output_scale // scale)
        image = image.resize(target_size, _resample_lanczos())
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
    colors: dict[str, str],
) -> None:
    draw.rounded_rectangle(
        (18 * scale, 18 * scale, width - 18 * scale, height - 18 * scale),
        radius=24 * scale,
        fill=colors["outer"],
        outline=colors["outer_edge"],
        width=4 * scale,
    )
    board_box = (
        left - int(cell * 0.55),
        top - int(cell * 0.72),
        right + int(cell * 0.55),
        bottom + int(cell * 0.72),
    )
    draw.rounded_rectangle(board_box, radius=18 * scale, fill=colors["board"], outline=colors["board_edge"], width=2 * scale)
    for offset in range(0, board_box[2] - board_box[0], max(16 * scale, 1)):
        color = colors["stripe_a"] if (offset // max(16 * scale, 1)) % 2 == 0 else colors["stripe_b"]
        x = board_box[0] + offset
        draw.rectangle((x, board_box[1], min(x + 10 * scale, board_box[2]), board_box[3]), fill=color)
    draw.rounded_rectangle(board_box, radius=18 * scale, outline=colors["board_edge"], width=2 * scale)


def _draw_grid(draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int, cell: int, scale: int, colors: dict[str, str]) -> None:
    line_w = max(2 * scale, 1)
    heavy_w = max(3 * scale, line_w)
    for y in range(10):
        py = top + y * cell
        draw.line((left, py, right, py), fill=colors["line"], width=heavy_w if y in {0, 9} else line_w)
    for x in range(9):
        px = left + x * cell
        width = heavy_w if x in {0, 8} else line_w
        draw.line((px, top, px, top + cell * 4), fill=colors["line"], width=width)
        draw.line((px, top + cell * 5, px, bottom), fill=colors["line"], width=width)


def _draw_palaces(draw: ImageDraw.ImageDraw, left: int, top: int, bottom: int, cell: int, scale: int, colors: dict[str, str]) -> None:
    line_w = max(2 * scale, 1)
    draw.line((left + 3 * cell, top, left + 5 * cell, top + 2 * cell), fill=colors["line"], width=line_w)
    draw.line((left + 5 * cell, top, left + 3 * cell, top + 2 * cell), fill=colors["line"], width=line_w)
    draw.line((left + 3 * cell, bottom, left + 5 * cell, bottom - 2 * cell), fill=colors["line"], width=line_w)
    draw.line((left + 5 * cell, bottom, left + 3 * cell, bottom - 2 * cell), fill=colors["line"], width=line_w)


def _draw_river(
    draw: ImageDraw.ImageDraw,
    left: int,
    right: int,
    top: int,
    cell: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    scale: int,
    colors: dict[str, str],
) -> None:
    river_y = top + cell * 4 + cell // 2
    draw.rectangle((left + 2 * scale, top + cell * 4 + 3 * scale, right - 2 * scale, top + cell * 5 - 3 * scale), fill=colors["river"])
    _draw_centered(draw, (left + cell * 2, river_y), "楚 河", font, colors["river_text"])
    _draw_centered(draw, (right - cell * 2, river_y), "汉 界", font, colors["river_text"])


def _draw_point_marks(draw: ImageDraw.ImageDraw, left: int, top: int, cell: int, scale: int, colors: dict[str, str]) -> None:
    for x, y in (
        (1, 2), (7, 2), (1, 7), (7, 7),
        (0, 3), (2, 3), (4, 3), (6, 3), (8, 3),
        (0, 6), (2, 6), (4, 6), (6, 6), (8, 6),
    ):
        _draw_mark(draw, left + x * cell, top + y * cell, x, scale, colors)


def _draw_mark(draw: ImageDraw.ImageDraw, cx: int, cy: int, board_x: int, scale: int, colors: dict[str, str]) -> None:
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
        draw.line((x0, y0, x0 + sx * arm, y0), fill=colors["line"], width=line_w)
        draw.line((x0, y0, x0, y0 + sy * arm), fill=colors["line"], width=line_w)


def _draw_piece(
    draw: ImageDraw.ImageDraw,
    piece: str,
    pos: tuple[int, int],
    left: int,
    top: int,
    cell: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    scale: int,
    colors: dict[str, str],
) -> None:
    x, y = pos
    cx = left + x * cell
    cy = top + y * cell
    radius = int(cell * 0.38)
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    color = piece_color(piece)
    variant = "red" if color == RED else "black"
    fill = colors.get(f"{variant}_piece_fill", colors["piece_fill"])
    base = colors.get(f"{variant}_piece_base", colors["piece_base"])
    shadow_color = colors.get(f"{variant}_piece_shadow", colors["piece_shadow"])
    shadow = (bbox[0] + 3 * scale, bbox[1] + 5 * scale, bbox[2] + 3 * scale, bbox[3] + 5 * scale)
    draw.ellipse(shadow, fill=_blend_hex(shadow_color, "#000000", 0.20))

    outline = colors["red"] if color == RED else colors["black"]
    text_color = outline

    edge = _blend_hex(colors["board_edge"], outline, 0.24)
    draw.ellipse(bbox, fill=_blend_hex(base, "#000000", 0.06), outline=edge, width=max(scale, 1))
    rim = _inset_box(bbox, 2 * scale)
    draw.ellipse(rim, fill=base, outline=_blend_hex(base, "#ffffff", 0.24), width=max(scale, 1))
    for step in range(9):
        inset = int((7 + step * 1.8) * scale)
        ratio = (step + 1) / 9
        tone = _blend_hex(base, fill, ratio)
        draw.ellipse(_inset_box(bbox, inset), fill=tone)

    inner = _inset_box(bbox, 7 * scale)
    ring = _inset_box(bbox, 14 * scale)
    shine = _inset_box(bbox, 10 * scale)
    draw.ellipse(inner, outline=outline, width=max(2 * scale, 1))
    draw.ellipse(ring, outline=_blend_hex(outline, fill, 0.34), width=max(scale, 1))
    draw.arc(shine, 205, 300, fill=_blend_hex(fill, "#ffffff", 0.50), width=max(scale, 1))
    _draw_centered(
        draw,
        (cx, cy - int(0.8 * scale)),
        PIECE_NAMES[piece],
        font,
        text_color,
        stroke_width=max(scale, 1),
        stroke_fill=_blend_hex(fill, "#ffffff", 0.52),
    )


def _highlight_cell(draw: ImageDraw.ImageDraw, pos: tuple[int, int], left: int, top: int, cell: int, colors: dict[str, str]) -> None:
    x, y = pos
    cx = left + x * cell
    cy = top + y * cell
    span = int(cell * 0.44)
    draw.rounded_rectangle((cx - span, cy - span, cx + span, cy + span), radius=int(cell * 0.14), outline=colors["highlight"], width=3)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
    stroke_width: int = 0,
    stroke_fill: str | None = None,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (center[0] - (bbox[0] + bbox[2]) / 2, center[1] - (bbox[1] + bbox[3]) / 2),
        text,
        fill=fill,
        font=font,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill or fill,
    )


def _blend_hex(left: str, right: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    lr, lg, lb = _hex_to_rgb(left)
    rr, rg, rb = _hex_to_rgb(right)
    return "#{:02x}{:02x}{:02x}".format(
        round(lr + (rr - lr) * ratio),
        round(lg + (rg - lg) * ratio),
        round(lb + (rb - lb) * ratio),
    )


def _inset_box(box: tuple[int, int, int, int], inset: int) -> tuple[int, int, int, int]:
    return box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return 0, 0, 0
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _resample_lanczos() -> int:
    resampling = getattr(Image, "Resampling", None)
    return getattr(resampling, "LANCZOS", Image.LANCZOS)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/adobe-source-han-sans/SourceHanSansSC-Bold.otf",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()
