from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from .engine.ai import choose_move, describe_move
from .engine.board import BLACK, RED, Board, Move, PIECE_NAMES, opponent, piece_color
from .engine.chinese_notation import CHINESE_NOTATION_PATTERN, CHINESE_NOTATION_RE, parse_chinese_notation
from .engine.parser import ParseError, format_coord, parse_coord
from .engine.pikafish_adapter import PikafishEngine
from .engine.rules import IllegalMoveError, is_checkmate, is_in_check, is_stalemate, legal_moves
from .engine.xqwlight_adapter import choose_move_xqwlight
from .render.board_image import render_board
from .storage.session_store import SessionStore
from .webui.server import XiangqiWebServer

try:
    from astrbot.core.agent.message import TextPart
except Exception:  # pragma: no cover - AstrBot older runtimes may not expose TextPart.
    TextPart = None


MOVE_TEXT_RE = re.compile(r"^\s*([a-i][0-9])(?:(?:\s*(?:->|[-=>])\s*)|\s+)?([a-i][0-9])\s*$", re.IGNORECASE)
BARE_MOVE_RE = r"^\s*[a-i][0-9](?:(?:\s*(?:->|[-=>])\s*)|\s+)?[a-i][0-9]\s*$"
CHINESE_MOVE_RE = CHINESE_NOTATION_PATTERN
MOVE_COMMAND_PREFIXES = ("走棋", "走", "move")
PLUGIN_LOG_NAME = "xiangqi_arena"
BOARD_CONTEXT_MARKER = "[xiangqi_arena_board_context]"
ACTIVE_GAME_RESET_MESSAGE = "当前会话已有未结束的象棋对局。发送“棋盘”查看，发送“重开”强制重置。"


@dataclass(slots=True)
class TurnOutcome:
    ok: bool
    board: Board | None = None
    error: str = ""
    summary_text: str = ""
    talk_lines: list[str] | None = None
    player_move: Move | None = None
    bot_move: Move | None = None
    bot_reason: str = ""
    player_in_check: bool = False
    ended: bool = False


class WebEventProxy:
    def __init__(self, session_id: str):
        self.unified_msg_origin = session_id
        self.message_str = ""

    def get_sender_id(self) -> str:
        return self.unified_msg_origin


class XiangqiArenaPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.data_dir: Path = StarTools.get_data_dir()
        self.store = SessionStore(self.data_dir)
        self.board_dir = self.data_dir / "boards"
        self.board_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or {}
        self._pikafish_engine: PikafishEngine | None = None
        self._pikafish_signature: tuple[Any, ...] | None = None
        self._engine_cooldowns: dict[str, float] = {"pikafish": 0.0, "xqwlight": 0.0}
        self._llm_talk_cooldown_until = 0.0
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._webui_server: XiangqiWebServer | None = None

    async def initialize(self) -> None:
        if self._webui_enabled():
            await self._ensure_webui_started()

    @filter.command("开局", alias=["象棋", "象棋新局", "新局", "下棋"])
    async def new_game(self, event: AstrMessageEvent):
        """开始新对局，默认玩家执红。"""
        async for result in self._yield_new_game(event, RED, force=False):
            yield result

    @filter.command("重开", alias=["强制开局", "重新开局", "重置开局"])
    async def restart_game(self, event: AstrMessageEvent):
        """强制重置当前会话对局，玩家执红。"""
        async for result in self._yield_new_game(event, RED, force=True):
            yield result

    @filter.command("执黑", alias=["象棋执黑", "开局执黑"])
    async def new_game_black(self, event: AstrMessageEvent):
        """开始新对局，玩家执黑，Bot 先手。"""
        async for result in self._yield_new_game(event, BLACK, force=False):
            yield result

    @filter.command("重开执黑", alias=["强制执黑", "重新执黑", "重置执黑"])
    async def restart_game_black(self, event: AstrMessageEvent):
        """强制重置当前会话对局，玩家执黑。"""
        async for result in self._yield_new_game(event, BLACK, force=True):
            yield result

    @filter.on_llm_request(priority=-5)
    async def inject_board_context(self, event: AstrMessageEvent, request: ProviderRequest) -> None:
        """让普通聊天也能临时看到当前象棋局势。"""
        if not self._chat_board_context_enabled():
            return
        board = self.store.load(self._session_id(event))
        if board is None:
            return
        context_text = self._build_chat_board_context(board)
        self._inject_request_hint(request, context_text)

    @filter.command("走", alias=["走棋", "move"])
    async def move(self, event: AstrMessageEvent, from_coord: str | None = None, to_coord: str | None = None):
        """按坐标走棋，例如：走 a6 b6。"""
        try:
            from_pos, to_pos = self._parse_move(from_coord, to_coord, getattr(event, "message_str", ""))
        except ParseError as exc:
            raw_move = self._strip_move_prefix(getattr(event, "message_str", ""))
            if self._chinese_notation_enabled() and CHINESE_NOTATION_RE.fullmatch(re.sub(r"\s+", "", raw_move)):
                async for result in self._handle_chinese_move(event, raw_move, quiet_no_game=False):
                    yield result
                return
            yield event.plain_result(str(exc))
            return
        async for result in self._handle_player_move(event, from_pos, to_pos, quiet_no_game=False):
            yield result

    @filter.regex(BARE_MOVE_RE)
    async def move_short(self, event: AstrMessageEvent):
        """对局中直接发送 a6 b6 / a6-b6 走棋。"""
        try:
            from_pos, to_pos = self._parse_move(None, None, getattr(event, "message_str", ""))
        except ParseError:
            return
        async for result in self._handle_player_move(event, from_pos, to_pos, quiet_no_game=True):
            yield result

    @filter.regex(CHINESE_MOVE_RE)
    async def move_chinese_short(self, event: AstrMessageEvent):
        """对局中直接发送马八进七 / 炮二平五走棋。"""
        if not self._chinese_notation_enabled():
            return
        async for result in self._handle_chinese_move(event, getattr(event, "message_str", ""), quiet_no_game=True):
            yield result

    @filter.command("棋盘", alias=["盘面"])
    async def board(self, event: AstrMessageEvent):
        """查看当前棋盘。"""
        session_id = self._session_id(event)
        board = self.store.load(session_id)
        if board is None:
            yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        yield event.image_result(str(self._render_session_board(session_id, board)))

    @filter.command("悔棋", alias=["撤销"])
    async def undo(self, event: AstrMessageEvent):
        """撤销上一整个回合。"""
        session_id = self._session_id(event)
        board = None
        message = "已撤销上一整个回合。"
        send_board = False
        async with self._session_lock(session_id):
            board = self.store.load(session_id)
            if board is None:
                message = "当前没有对局，请先发送“开局”。"
            elif len(board.history) < 2:
                message = "当前没有可撤销的完整回合。"
            else:
                board.pop_state()
                board.pop_state()
                self.store.save(session_id, board)
                send_board = True
        yield event.plain_result(message)
        if send_board and board is not None:
            yield event.image_result(str(self._render_session_board(session_id, board)))

    @filter.command("认输", alias=["投降", "结束"])
    async def resign(self, event: AstrMessageEvent):
        """认输并结束当前对局。"""
        session_id = self._session_id(event)
        message = "你已认输，本局结束。"
        async with self._session_lock(session_id):
            board = self.store.load(session_id)
            if board is None:
                message = "当前没有对局，请先发送“开局”。"
            else:
                self.store.delete(session_id)
        yield event.plain_result(message)

    @filter.command("提示", alias=["建议"])
    async def hint(self, event: AstrMessageEvent):
        """给出当前局面的建议走法。"""
        session_id = self._session_id(event)
        board = self.store.load(session_id)
        if board is None:
            yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        if board.side_to_move != board.player_color:
            yield event.plain_result("当前不是你的回合。")
            return
        move, reason = await self._choose_bot_move(board, board.player_color)
        if move is None:
            yield event.plain_result("当前没有合法走法。")
            return
        message = f"可以考虑：{describe_move(move)}"
        if reason and self._engine_details_in_chat():
            message += f"（{reason}）"
        yield event.plain_result(message)

    @filter.command("状态", alias=["象棋状态"])
    async def status(self, event: AstrMessageEvent):
        """查看对局与引擎状态。"""
        session_id = self._session_id(event)
        board = self.store.load(session_id)
        if board is None:
            yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        side = "红方" if board.side_to_move == RED else "黑方"
        player = "红方" if board.player_color == RED else "黑方"
        cooldowns = self._cooldown_summary()
        suffix = f"；{cooldowns}" if cooldowns else ""
        yield event.plain_result(f"你执{player}，当前轮到{side}。引擎顺序：{', '.join(self._engine_order())}{suffix}")

    @filter.command("网页下棋", alias=["棋局链接", "象棋网页", "webui", "web"])
    async def webui_link(self, event: AstrMessageEvent):
        """生成当前会话的独立网页棋盘链接。"""
        if not self._webui_enabled():
            yield event.plain_result("WebUI 当前未开启，请在插件配置里打开 webui_enabled。")
            return
        await self._ensure_webui_started()
        if self._webui_server is None or not self._webui_server.is_running:
            yield event.plain_result("WebUI 启动失败，请查看 AstrBot 日志里的 xiangqi_arena webui 记录。")
            return
        url = self._webui_server.issue_url(self._session_id(event))
        yield event.plain_result(f"网页棋盘：{url}\n这个链接绑定当前会话，请勿随意转发。")

    async def terminate(self):
        if self._webui_server is not None:
            await self._webui_server.stop()
            self._webui_server = None
        if self._pikafish_engine is not None:
            await self._pikafish_engine.close()
            self._pikafish_engine = None
        return None

    async def _ensure_webui_started(self) -> None:
        if self._webui_server is not None and self._webui_server.is_running:
            return
        self._webui_server = XiangqiWebServer(
            plugin=self,
            host=self._webui_host(),
            port=self._webui_port(),
            public_base_url=self._webui_public_base_url(),
            token_ttl_seconds=self._webui_token_ttl_seconds(),
        )
        try:
            await self._webui_server.start()
        except Exception as exc:
            logger.warning("%s webui failed to start: %r", PLUGIN_LOG_NAME, exc)
            self._webui_server = None

    async def _yield_new_game(self, event: AstrMessageEvent, player_color: str, force: bool):
        session_id = self._session_id(event)
        result = await self._create_new_game(session_id, player_color, event=event, force=force)
        if not result["ok"]:
            yield event.plain_result(result["error"])
            return
        board = result["board"]
        yield event.plain_result(result["message"])
        async for talk_result in self._yield_talk(event, result.get("talk_lines")):
            yield talk_result
        yield event.image_result(str(self._render_session_board(session_id, board)))

    async def _create_new_game(
        self,
        session_id: str,
        player_color: str,
        event: AstrMessageEvent | WebEventProxy | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if player_color not in {RED, BLACK}:
            return {"ok": False, "error": "执色参数无效。", "board": self.store.load(session_id)}
        event = event or WebEventProxy(session_id)
        async with self._session_lock(session_id):
            existing = self.store.load(session_id)
            if existing is not None and not force and self._protect_active_game_reset():
                return {"ok": False, "error": ACTIVE_GAME_RESET_MESSAGE, "board": existing}

            board = Board.new_game(player_color=player_color)
            message = "新局开始，你执红先行。" if player_color == RED else "新局开始，你执黑。"
            talk_lines = None
            if player_color == BLACK:
                bot_move, reason = await self._choose_bot_move(board, RED)
                if bot_move is not None:
                    board.push_state()
                    board.apply_move(bot_move.from_pos, bot_move.to_pos)
                    message += f" 我先手：{describe_move(bot_move)}"
                    if reason and self._engine_details_in_chat():
                        message += f"（{reason}）"
                    talk_lines = await self._generate_bot_talk(event, board, bot_move, reason, RED)
                    self._remember_turn(board, None, bot_move, talk_lines, False)
                    logger.info(
                        "%s bot opening move: session=%s bot=%s reason=%s force=%s",
                        PLUGIN_LOG_NAME,
                        session_id,
                        describe_move(bot_move),
                        reason,
                        force,
                    )
            self.store.save(session_id, board)

        logger.info("%s new game: session=%s player_color=%s force=%s", PLUGIN_LOG_NAME, session_id, player_color, force)
        return {"ok": True, "message": message, "board": board, "talk_lines": talk_lines}

    async def _handle_player_move(
        self,
        event: AstrMessageEvent,
        from_pos: tuple[int, int],
        to_pos: tuple[int, int],
        quiet_no_game: bool,
    ):
        session_id = self._session_id(event)
        outcome = await self._play_player_turn(event, session_id, from_pos, to_pos, quiet_no_game=quiet_no_game)
        if not outcome.ok:
            if outcome.error:
                yield event.plain_result(outcome.error)
            return
        if outcome.summary_text:
            yield event.plain_result(outcome.summary_text)
        async for result in self._yield_talk(event, outcome.talk_lines):
            yield result
        if outcome.board is not None:
            yield event.image_result(str(self._render_session_board(session_id, outcome.board)))

    async def _play_player_turn(
        self,
        event: AstrMessageEvent | WebEventProxy,
        session_id: str,
        from_pos: tuple[int, int],
        to_pos: tuple[int, int],
        quiet_no_game: bool = False,
    ) -> TurnOutcome:
        async with self._session_lock(session_id):
            return await self._play_player_turn_locked(event, session_id, from_pos, to_pos, quiet_no_game)

    async def _play_player_turn_locked(
        self,
        event: AstrMessageEvent | WebEventProxy,
        session_id: str,
        from_pos: tuple[int, int],
        to_pos: tuple[int, int],
        quiet_no_game: bool,
    ) -> TurnOutcome:
        board = self.store.load(session_id)
        if board is None:
            return TurnOutcome(ok=False, error="" if quiet_no_game else "当前没有对局，请先发送“开局”。")
        player_color = board.player_color
        if board.side_to_move != player_color:
            return TurnOutcome(ok=False, error="当前不是你的回合，请稍后再试。")

        try:
            player_move = self._apply_player_move(board, from_pos, to_pos, player_color)
        except (IllegalMoveError, ValueError) as exc:
            return TurnOutcome(ok=False, error=str(exc), board=board)

        message_parts: list[str] = []
        player_only_summary = self._format_player_move_summary(player_move)
        if player_only_summary:
            message_parts.append(player_only_summary)
        if self._append_endgame_message(board, opponent(player_color), message_parts, winner_is_player=True):
            self.store.delete(session_id)
            return TurnOutcome(
                ok=True,
                board=board,
                summary_text=" ".join(message_parts).strip(),
                player_move=player_move,
                ended=True,
            )

        bot_color = opponent(player_color)
        bot_move, bot_reason = await self._choose_bot_move(board, bot_color)
        if bot_move is None:
            message_parts.append("我无合法走法，本局结束。")
            self.store.delete(session_id)
            return TurnOutcome(
                ok=True,
                board=board,
                summary_text=" ".join(message_parts).strip(),
                player_move=player_move,
                bot_reason=bot_reason,
                ended=True,
            )

        board.push_state()
        board.apply_move(bot_move.from_pos, bot_move.to_pos)
        summary = self._format_turn_summary(player_move, bot_move)
        if summary:
            message_parts = [summary]
        if summary and bot_reason and self._engine_details_in_chat():
            message_parts.append(f"（{bot_reason}）")
        talk_lines = await self._generate_bot_talk(event, board, bot_move, bot_reason, bot_color, player_move)

        player_in_check = is_in_check(board, player_color)
        if player_in_check:
            message_parts.append("你现在被将军。")
        logger.info(
            "%s turn: session=%s player=%s bot=%s reason=%s",
            PLUGIN_LOG_NAME,
            session_id,
            describe_move(player_move),
            describe_move(bot_move),
            bot_reason,
        )
        self._remember_turn(board, player_move, bot_move, talk_lines, player_in_check)
        if self._append_endgame_message(board, player_color, message_parts, winner_is_player=False):
            self.store.delete(session_id)
            ended = True
        else:
            self.store.save(session_id, board)
            ended = False

        summary_text = " ".join(message_parts).strip()
        return TurnOutcome(
            ok=True,
            board=board,
            summary_text=summary_text,
            talk_lines=talk_lines,
            player_move=player_move,
            bot_move=bot_move,
            bot_reason=bot_reason,
            player_in_check=player_in_check,
            ended=ended,
        )

    async def _handle_chinese_move(self, event: AstrMessageEvent, notation: str, quiet_no_game: bool):
        session_id = self._session_id(event)
        board = self.store.load(session_id)
        if board is None:
            if not quiet_no_game:
                yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        player_color = board.player_color
        if board.side_to_move != player_color:
            yield event.plain_result("当前不是你的回合，请稍后再试。")
            return
        try:
            move = parse_chinese_notation(notation, board, player_color)
        except ParseError as exc:
            yield event.plain_result(str(exc))
            return
        async for result in self._handle_player_move(event, move.from_pos, move.to_pos, quiet_no_game=quiet_no_game):
            yield result

    async def _yield_talk(self, event: AstrMessageEvent, talk_lines: list[str] | None):
        if not talk_lines:
            return
        for talk_line in talk_lines:
            yield event.plain_result(self._format_talk_line(talk_line))

    def _parse_move(self, from_coord: str | None, to_coord: str | None, raw_text: str) -> tuple[tuple[int, int], tuple[int, int]]:
        if from_coord and to_coord:
            return parse_coord(from_coord), parse_coord(to_coord)
        text = self._strip_move_prefix(raw_text or "")
        match = MOVE_TEXT_RE.fullmatch(text)
        if not match:
            raise ParseError("走法格式：a6 b6、a6-b6，或“走 a6 b6”。")
        return parse_coord(match.group(1)), parse_coord(match.group(2))

    def _strip_move_prefix(self, raw_text: str) -> str:
        text = raw_text.strip()
        lowered = text.lower()
        for prefix in MOVE_COMMAND_PREFIXES:
            if lowered.startswith(prefix):
                return text[len(prefix) :].strip()
        return text

    def _session_id(self, event: AstrMessageEvent) -> str:
        origin = getattr(event, "unified_msg_origin", None)
        if origin:
            return str(origin)
        group_id = getattr(event, "get_group_id", lambda: None)()
        if group_id:
            return f"group:{group_id}"
        return f"user:{event.get_sender_id()}"

    def _render_session_board(self, session_id: str, board: Board) -> Path:
        filename = session_id.replace("/", "_").replace(":", "_") + ".png"
        return render_board(board, self.board_dir / filename, self._image_scale())

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def webui_get_state(self, session_id: str) -> dict[str, Any]:
        return self._serialize_webui_state(session_id, self.store.load(session_id))

    async def webui_start_game(self, session_id: str, player_color: str, force: bool = False) -> dict[str, Any]:
        result = await self._create_new_game(session_id, player_color, event=WebEventProxy(session_id), force=force)
        board = result.get("board")
        if not result["ok"]:
            return {"ok": False, "error": result["error"], "state": self._serialize_webui_state(session_id, board)}

        message = result["message"]
        talk_lines = result.get("talk_lines")
        await self._send_webui_chat_sync(session_id, message, board, talk_lines)
        return {
            "ok": True,
            "message": message,
            "talk": self._format_talk_lines(talk_lines),
            "state": self._serialize_webui_state(session_id, board),
        }

    async def webui_move(self, session_id: str, from_coord: str, to_coord: str) -> dict[str, Any]:
        try:
            from_pos = parse_coord(from_coord)
            to_pos = parse_coord(to_coord)
        except ParseError as exc:
            return {"ok": False, "error": str(exc), "state": await self.webui_get_state(session_id)}

        outcome = await self._play_player_turn(WebEventProxy(session_id), session_id, from_pos, to_pos)
        if not outcome.ok:
            return {
                "ok": False,
                "error": outcome.error,
                "state": self._serialize_webui_state(session_id, outcome.board or self.store.load(session_id)),
            }

        await self._send_webui_chat_sync(session_id, outcome.summary_text, outcome.board, outcome.talk_lines)
        return {
            "ok": True,
            "message": outcome.summary_text,
            "talk": self._format_talk_lines(outcome.talk_lines),
            "ended": outcome.ended,
            "state": self._serialize_webui_state(session_id, outcome.board, ended=outcome.ended),
        }

    async def webui_undo(self, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            board = self.store.load(session_id)
            if board is None:
                return {"ok": False, "error": "当前没有对局，请先开局。", "state": self._serialize_webui_state(session_id, None)}
            if len(board.history) < 2:
                return {"ok": False, "error": "当前没有可撤销的完整回合。", "state": self._serialize_webui_state(session_id, board)}
            board.pop_state()
            board.pop_state()
            self.store.save(session_id, board)
            state = self._serialize_webui_state(session_id, board)

        message = "网页端已撤销上一整个回合。"
        await self._send_webui_chat_sync(session_id, message, board, None)
        return {"ok": True, "message": message, "state": state}

    async def webui_resign(self, session_id: str) -> dict[str, Any]:
        async with self._session_lock(session_id):
            board = self.store.load(session_id)
            if board is None:
                return {"ok": False, "error": "当前没有对局。", "state": self._serialize_webui_state(session_id, None)}
            self.store.delete(session_id)

        message = "网页端已认输，本局结束。"
        await self._send_webui_chat_sync(session_id, message, board, None)
        return {"ok": True, "message": message, "state": self._serialize_webui_state(session_id, board, ended=True)}

    async def webui_hint(self, session_id: str) -> dict[str, Any]:
        board = self.store.load(session_id)
        if board is None:
            return {"ok": False, "error": "当前没有对局，请先开局。", "state": self._serialize_webui_state(session_id, None)}
        if board.side_to_move != board.player_color:
            return {"ok": False, "error": "当前不是你的回合。", "state": self._serialize_webui_state(session_id, board)}
        move, reason = await self._choose_bot_move(board, board.player_color)
        if move is None:
            return {"ok": False, "error": "当前没有合法走法。", "state": self._serialize_webui_state(session_id, board)}
        message = f"可以考虑：{describe_move(move)}"
        if reason and self._engine_details_in_chat():
            message += f"（{reason}）"
        logger.info("%s webui hint: session=%s move=%s reason=%s", PLUGIN_LOG_NAME, session_id, describe_move(move), reason)
        return {"ok": True, "message": message, "state": self._serialize_webui_state(session_id, board)}

    def _serialize_webui_state(self, session_id: str, board: Board | None, ended: bool = False) -> dict[str, Any]:
        display_board = board or Board.new_game()
        active = board is not None and not ended
        player_color = display_board.player_color
        bot_color = opponent(player_color)
        legal: list[dict[str, Any]] = []
        if active and display_board.side_to_move == player_color:
            legal = [self._serialize_move(move) for move in legal_moves(display_board, player_color)]
        status_text = "当前没有对局。" if board is None else "本局结束。" if ended else self._webui_status_text(display_board)
        in_check = bool(board is not None and not ended and is_in_check(display_board, display_board.side_to_move))
        return {
            "session": self._short_session_label(session_id),
            "game_active": active,
            "ended": ended,
            "status": status_text,
            "side_to_move": display_board.side_to_move,
            "side_to_move_label": self._side_label(display_board.side_to_move),
            "turn_owner": "player" if active and display_board.side_to_move == player_color else "bot",
            "player_color": player_color,
            "player_label": self._side_label(player_color),
            "bot_color": bot_color,
            "bot_label": self._side_label(bot_color),
            "in_check": in_check,
            "can_undo": active and len(display_board.history) >= 2,
            "last_move": None if display_board.last_move is None else self._serialize_move(display_board.last_move),
            "legal_moves": legal,
            "grid": [[self._serialize_piece(piece) for piece in row] for row in display_board.grid],
            "move_log": list(display_board.move_log[-8:]),
            "talk_log": list(display_board.talk_log[-8:]),
            "coordinates": {
                "files": [chr(ord("a") + index) for index in range(9)],
                "ranks": [str(index) for index in range(10)],
            },
        }

    def _serialize_piece(self, piece: str | None) -> dict[str, str] | None:
        if piece is None:
            return None
        color = piece_color(piece) or ""
        return {"code": piece, "name": PIECE_NAMES.get(piece, piece), "color": color, "label": self._side_label(color)}

    def _serialize_move(self, move: Move) -> dict[str, Any]:
        return {
            "from": format_coord(move.from_pos),
            "to": format_coord(move.to_pos),
            "piece": self._serialize_piece(move.piece),
            "captured": self._serialize_piece(move.captured),
            "text": describe_move(move),
        }

    def _webui_status_text(self, board: Board) -> str:
        side = self._side_label(board.side_to_move)
        owner = "你" if board.side_to_move == board.player_color else "我"
        check = "，正在被将军" if is_in_check(board, board.side_to_move) else ""
        return f"你执{self._side_label(board.player_color)}，当前轮到{side}（{owner}走）{check}。"

    def _side_label(self, color: str) -> str:
        if color == RED:
            return "红方"
        if color == BLACK:
            return "黑方"
        return color

    def _short_session_label(self, session_id: str) -> str:
        clean = session_id.replace(":", "/")
        return clean if len(clean) <= 28 else f"...{clean[-25:]}"

    def _format_talk_lines(self, talk_lines: list[str] | None) -> list[str]:
        return [self._format_talk_line(line) for line in talk_lines or [] if line]

    async def _send_webui_chat_sync(
        self,
        session_id: str,
        message: str,
        board: Board | None,
        talk_lines: list[str] | None,
    ) -> None:
        if not self._webui_notify_chat():
            return
        text_parts = []
        if message:
            text_parts.append(message)
        text_parts.extend(self._format_talk_lines(talk_lines))
        if not text_parts and (board is None or not self._webui_notify_board()):
            return
        try:
            chain = MessageChain()
            if text_parts:
                chain.message("\n".join(text_parts))
            if board is not None and self._webui_notify_board():
                chain.file_image(str(self._render_session_board(session_id, board)))
            await self.context.send_message(session_id, chain)
        except Exception as exc:
            logger.warning("%s webui chat sync failed: session=%s error=%r", PLUGIN_LOG_NAME, session_id, exc)

    def _bool_config(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", "关闭", "否"}
        return bool(value)

    def _int_config(self, key: str, default: int, minimum: int, maximum: int) -> int:
        raw_value = self.config.get(key, default)
        if raw_value is None or raw_value == "":
            raw_value = default
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _float_config(self, key: str, default: float, minimum: float, maximum: float) -> float:
        raw_value = self.config.get(key, default)
        if raw_value is None or raw_value == "":
            raw_value = default
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _str_config(self, key: str, default: str = "") -> str:
        return str(self.config.get(key, default) or "").strip()

    def _engine_order(self) -> list[str]:
        backend = self._str_config("engine_backend", "auto").lower()
        if backend in {"pikafish", "xqwlight", "builtin"}:
            order = [backend]
        else:
            order = ["pikafish", "xqwlight", "builtin"]

        if not self._bool_config("enable_pikafish_engine", True):
            order = [item for item in order if item != "pikafish"]
        if not self._bool_config("enable_xqwlight_engine", True):
            order = [item for item in order if item != "xqwlight"]
        if "builtin" not in order:
            order.append("builtin")
        return order

    def _ai_depth(self) -> int:
        return self._int_config("ai_depth", 1, 1, 3)

    def _xqwlight_depth(self) -> int:
        return self._int_config("xqwlight_depth", 4, 1, 12)

    def _xqwlight_timeout_ms(self) -> int:
        return self._int_config("xqwlight_timeout_ms", 800, 200, 10000)

    def _xqwlight_failure_cooldown_seconds(self) -> int:
        return self._int_config("xqwlight_failure_cooldown_seconds", 0, 0, 3600)

    def _xqwlight_jar_path(self) -> str | None:
        return self._str_config("xqwlight_jar_path") or None

    def _pikafish_failure_cooldown_seconds(self) -> int:
        return self._int_config("pikafish_failure_cooldown_seconds", 0, 0, 3600)

    def _pikafish_signature_values(self) -> tuple[Any, ...]:
        return (
            self._str_config("pikafish_path", "pikafish") or "pikafish",
            self._str_config("pikafish_working_dir"),
            self._str_config("pikafish_eval_file"),
            self._int_config("pikafish_threads", 1, 1, 8),
            self._int_config("pikafish_hash_mb", 16, 8, 1024),
            self._int_config("pikafish_movetime_ms", 500, 50, 10000),
            self._float_config("pikafish_startup_timeout", 5.0, 1.0, 30.0),
            self._int_config("pikafish_move_overhead_ms", 30, 0, 1000),
        )

    def _image_scale(self) -> int:
        scale = self._int_config("image_scale", 1, 1, 2)
        return 1 if scale <= 1 else 2

    def _webui_enabled(self) -> bool:
        return self._bool_config("webui_enabled", True)

    def _webui_host(self) -> str:
        return self._str_config("webui_host", "127.0.0.1") or "127.0.0.1"

    def _webui_port(self) -> int:
        return self._int_config("webui_port", 8787, 1, 65535)

    def _webui_public_base_url(self) -> str:
        return self._str_config("webui_public_base_url")

    def _webui_token_ttl_seconds(self) -> int:
        return self._int_config("webui_token_ttl_seconds", 86400, 0, 604800)

    def _webui_notify_chat(self) -> bool:
        return self._bool_config("webui_notify_chat", True)

    def _webui_notify_board(self) -> bool:
        return self._bool_config("webui_notify_board", True)

    def _move_summary_enabled(self) -> bool:
        return self._bool_config("move_summary_enabled", True)

    def _protect_active_game_reset(self) -> bool:
        return self._bool_config("protect_active_game_reset", True)

    def _chinese_notation_enabled(self) -> bool:
        return self._bool_config("enable_chinese_notation", True)

    def _turn_summary_template(self) -> str:
        return self._str_config("move_summary_template", "你走了 {player_move} 我走了 {bot_move}")

    def _player_move_summary_template(self) -> str:
        return self._str_config("player_move_summary_template", "你走了 {player_move}")

    def _engine_details_in_chat(self) -> bool:
        return self._bool_config("engine_details_in_chat", False)

    def _chat_board_context_enabled(self) -> bool:
        return self._bool_config("chat_board_context_enabled", True)

    def _chat_board_context_max_items(self) -> int:
        return self._int_config("chat_board_context_max_items", 5, 0, 10)

    def _talk_line_template(self) -> str:
        return self._str_config("llm_talk_template", "{talk}") or "{talk}"

    def _llm_talk_enabled(self) -> bool:
        return self._bool_config("llm_talk_enabled", True)

    def _llm_extra_prompt(self) -> str:
        return self._str_config("llm_extra_prompt")

    def _llm_talk_timeout(self) -> float:
        return self._float_config("llm_talk_timeout", 3.0, 1.0, 300.0)

    def _llm_talk_failure_cooldown_seconds(self) -> int:
        return self._int_config("llm_talk_failure_cooldown_seconds", 0, 0, 3600)

    def _llm_talk_sentence_count(self) -> int:
        max_count = self._int_config("llm_talk_max_sentences", 1, 1, 3)
        return random.randint(1, max_count)

    def _llm_talk_max_chars(self) -> int:
        return self._int_config("llm_talk_max_chars", 45, 10, 100)

    def _llm_provider_id(self) -> str | None:
        return self._str_config("llm_provider_id") or None

    def _llm_model(self) -> str | None:
        return self._str_config("llm_model") or None

    async def _choose_bot_move(self, board: Board, color: str) -> tuple[Move | None, str]:
        failures: list[str] = []
        for backend in self._engine_order():
            remaining = self._engine_cooldowns.get(backend, 0.0) - time.monotonic()
            if remaining > 0:
                failures.append(f"{backend}冷却中")
                continue
            try:
                if backend == "pikafish":
                    return await self._choose_pikafish_move(board, color, failures)
                if backend == "xqwlight":
                    return await self._choose_xqwlight_move(board, color, failures)
                move, reason = choose_move(board, color, self._ai_depth())
                return move, self._merge_engine_reason(reason, failures)
            except Exception as exc:
                failures.append(f"{backend}失败：{exc}")
                self._cooldown_engine(backend, exc)

        move, reason = choose_move(board, color, self._ai_depth())
        return move, self._merge_engine_reason(reason, failures)

    async def _choose_pikafish_move(self, board: Board, color: str, failures: list[str]) -> tuple[Move | None, str]:
        engine = await self._get_pikafish_engine()
        move, reason = await engine.choose_move(board, color)
        self._engine_cooldowns["pikafish"] = 0.0
        return move, self._merge_engine_reason(reason, failures)

    async def _choose_xqwlight_move(self, board: Board, color: str, failures: list[str]) -> tuple[Move | None, str]:
        move, reason = await choose_move_xqwlight(
            board=board,
            color=color,
            jar_path=self._xqwlight_jar_path(),
            depth=self._xqwlight_depth(),
            timeout_ms=self._xqwlight_timeout_ms(),
        )
        self._engine_cooldowns["xqwlight"] = 0.0
        return move, self._merge_engine_reason(reason, failures)

    async def _get_pikafish_engine(self) -> PikafishEngine:
        signature = self._pikafish_signature_values()
        if self._pikafish_engine is not None and self._pikafish_signature == signature:
            return self._pikafish_engine
        if self._pikafish_engine is not None:
            await self._pikafish_engine.close()
        self._pikafish_signature = signature
        self._pikafish_engine = PikafishEngine(
            executable=signature[0],
            working_dir=signature[1] or None,
            eval_file=signature[2] or None,
            threads=signature[3],
            hash_mb=signature[4],
            movetime_ms=signature[5],
            startup_timeout=signature[6],
            move_overhead_ms=signature[7],
        )
        return self._pikafish_engine

    def _cooldown_engine(self, backend: str, exc: Exception) -> None:
        if backend == "pikafish":
            cooldown = self._pikafish_failure_cooldown_seconds()
        elif backend == "xqwlight":
            cooldown = self._xqwlight_failure_cooldown_seconds()
        else:
            return
        if cooldown > 0:
            self._engine_cooldowns[backend] = time.monotonic() + cooldown
        logger.warning(
            "%s %s engine failed; cooldown=%ss; config=%s; error=%r",
            PLUGIN_LOG_NAME,
            backend,
            cooldown,
            self._engine_log_config(backend),
            exc,
        )

    def _engine_log_config(self, backend: str) -> str:
        if backend == "pikafish":
            signature = self._pikafish_signature_values()
            return (
                f"path={signature[0]!r}, working_dir={signature[1]!r}, eval_file={signature[2]!r}, "
                f"threads={signature[3]}, hash_mb={signature[4]}, movetime_ms={signature[5]}, "
                f"startup_timeout={signature[6]}, move_overhead_ms={signature[7]}"
            )
        if backend == "xqwlight":
            return (
                f"jar_path={self._xqwlight_jar_path()!r}, depth={self._xqwlight_depth()}, "
                f"timeout_ms={self._xqwlight_timeout_ms()}"
            )
        return "builtin"

    def _merge_engine_reason(self, reason: str, failures: list[str]) -> str:
        if not failures:
            return reason
        return f"{reason}；已跳过：{'；'.join(failures[:2])}"

    def _cooldown_summary(self) -> str:
        now = time.monotonic()
        parts = []
        for backend, until in self._engine_cooldowns.items():
            remaining = int(until - now)
            if remaining > 0:
                parts.append(f"{backend}冷却{remaining}s")
        llm_remaining = int(self._llm_talk_cooldown_until - now)
        if llm_remaining > 0:
            parts.append(f"台词冷却{llm_remaining}s")
        return "，".join(parts)

    def _apply_player_move(self, board: Board, from_pos, to_pos, color):
        from .engine.rules import apply_legal_move

        return apply_legal_move(board, from_pos, to_pos, color)

    def _get_llm_provider(self, event: AstrMessageEvent):
        provider_id = self._llm_provider_id()
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if provider:
                    return provider
            except Exception as exc:
                logger.warning("%s llm talk skipped: provider id %s unavailable: %s", PLUGIN_LOG_NAME, provider_id, exc)
        try:
            return self.context.get_using_provider(umo=getattr(event, "unified_msg_origin", None))
        except Exception as exc:
            logger.warning("%s llm talk skipped: no provider: %s", PLUGIN_LOG_NAME, exc)
            return None

    async def _generate_bot_talk(
        self,
        event: AstrMessageEvent,
        board: Board,
        bot_move: Move,
        bot_reason: str | None,
        bot_color: str,
        player_move: Move | None = None,
    ) -> list[str] | None:
        if not self._llm_talk_enabled():
            return None
        sentence_count = self._llm_talk_sentence_count()
        if self._llm_talk_cooldown_until > time.monotonic():
            return self._fallback_bot_talk(bot_move, bot_reason, bot_color, player_move, sentence_count)
        provider = self._get_llm_provider(event)
        if provider is None:
            return self._fallback_bot_talk(bot_move, bot_reason, bot_color, player_move, sentence_count)

        prompt = self._build_talk_prompt(board, bot_move, bot_reason, bot_color, sentence_count, player_move)
        system_prompt = await self._build_persona_system_prompt(event)
        session_id = f"{PLUGIN_LOG_NAME}_talk_{self._session_id(event)}"
        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    system_prompt=system_prompt,
                    session_id=session_id,
                    model=self._llm_model(),
                    func_tool=None,
                    tool_choice="auto",
                ),
                timeout=self._llm_talk_timeout(),
            )
        except TypeError:
            try:
                kwargs = {
                    "prompt": prompt if not system_prompt else f"{system_prompt}\n\n{prompt}",
                    "contexts": [],
                    "session_id": session_id,
                    "func_tool": None,
                    "tool_choice": "auto",
                }
                response = await asyncio.wait_for(provider.text_chat(**kwargs), timeout=self._llm_talk_timeout())
            except Exception as exc:
                logger.warning("%s llm talk failed: %r", PLUGIN_LOG_NAME, exc)
                self._cooldown_llm_talk(exc)
                return self._fallback_bot_talk(bot_move, bot_reason, bot_color, player_move, sentence_count)
        except Exception as exc:
            logger.warning("%s llm talk failed: %r", PLUGIN_LOG_NAME, exc)
            self._cooldown_llm_talk(exc)
            return self._fallback_bot_talk(bot_move, bot_reason, bot_color, player_move, sentence_count)

        text = str(getattr(response, "completion_text", "") or "").strip()
        cleaned = self._clean_llm_talk(text, sentence_count)
        self._llm_talk_cooldown_until = 0.0
        return cleaned or self._fallback_bot_talk(bot_move, bot_reason, bot_color, player_move, sentence_count)

    async def _build_persona_system_prompt(self, event: AstrMessageEvent) -> str | None:
        prompt = await self._get_default_persona_prompt(event)
        extra = self._llm_extra_prompt()
        if extra:
            prompt = f"{prompt}\n\n{extra}" if prompt else extra
        return prompt or None

    async def _get_default_persona_prompt(self, event: AstrMessageEvent) -> str:
        persona_manager = getattr(self.context, "persona_manager", None)
        getter = getattr(persona_manager, "get_default_persona_v3", None)
        if getter is None:
            return ""
        try:
            result = getter(getattr(event, "unified_msg_origin", None))
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.warning("%s default persona unavailable: %s", PLUGIN_LOG_NAME, exc)
            return ""
        return str(getattr(result, "prompt", None) or getattr(result, "system_prompt", None) or "").strip()

    def _cooldown_llm_talk(self, exc: Exception) -> None:
        cooldown = self._llm_talk_failure_cooldown_seconds()
        if cooldown <= 0:
            return
        self._llm_talk_cooldown_until = time.monotonic() + cooldown
        logger.warning("%s llm talk cooled down for %ss after failure: %r", PLUGIN_LOG_NAME, cooldown, exc)

    def _build_talk_prompt(
        self,
        board: Board,
        bot_move: Move,
        bot_reason: str | None,
        bot_color: str,
        sentence_count: int,
        player_move: Move | None = None,
    ) -> str:
        bot_side = "红方" if bot_color == RED else "黑方"
        player_color = opponent(bot_color)
        player_in_check = is_in_check(board, player_color)
        captured = f"，吃掉{bot_move.captured}" if getattr(bot_move, "captured", None) else ""
        player_move_text = describe_move(player_move) if player_move is not None else "无，本局由你先手走棋"
        board_text = self._ascii_board(board)
        memory_text = self._memory_text(board)
        max_chars = self._llm_talk_max_chars()
        if self._engine_details_in_chat():
            engine_text = f"引擎信息：{bot_reason or '无'}。\n"
        else:
            engine_text = "内部引擎信息不向聊天展示；不要提搜索评分、引擎名称、失败、降级或冷却。\n"
        return (
            "你正在当前聊天会话里下中国象棋。请沿用当前 AstrBot 人格说话，但只为 Bot 自己说话。\n"
            "刚才完整回合：人类玩家先走，Bot 随后应对。\n"
            f"人类玩家走：{player_move_text}。\n"
            f"Bot 也就是你走：{describe_move(bot_move)}{captured}。\n"
            f"Bot 执{bot_side}。{engine_text}"
            f"对手当前{'被将军' if player_in_check else '没有被将军'}。\n"
            f"最近对局记忆：\n{memory_text}\n"
            f"当前棋盘，0行是黑方底线，9行是红方底线：\n{board_text}\n\n"
            "输出要求：\n"
            f"- 只输出{sentence_count}句中文台词，每句单独一行，每句最多{max_chars}字。\n"
            "- 可以自然承接你之前说过的话，但不要机械复读旧台词。\n"
            "- 不要说'玩家走了Bot这步'，不要把 Bot 的走法归到人类玩家身上。\n"
            "- 不要输出编号、JSON、引号、括号说明；不要替玩家走棋。\n"
            "- 可以有情绪和胜负欲，但不要低俗，不要像客服，不要说'作为AI'。"
        )

    def _ascii_board(self, board: Board) -> str:
        rows = []
        for y, row in enumerate(board.grid):
            rows.append(f"{y} " + " ".join(piece if piece is not None else "." for piece in row))
        rows.append("  a b c d e f g h i")
        return "\n".join(rows)

    def _memory_text(self, board: Board) -> str:
        lines: list[str] = []
        if board.move_log:
            lines.append("最近几手：")
            lines.extend(f"- {item}" for item in board.move_log[-5:])
        if board.talk_log:
            lines.append("我之前说过：")
            lines.extend(f"- {item}" for item in board.talk_log[-5:])
        return "\n".join(lines) if lines else "暂无。"

    def _build_chat_board_context(self, board: Board) -> str:
        player_side = "红方" if board.player_color == RED else "黑方"
        bot_color = opponent(board.player_color)
        bot_side = "红方" if bot_color == RED else "黑方"
        turn_side = "红方" if board.side_to_move == RED else "黑方"
        turn_owner = "用户" if board.side_to_move == board.player_color else "你"
        last_move = "暂无" if board.last_move is None else describe_move(board.last_move)
        limit = self._chat_board_context_max_items()

        lines = [
            BOARD_CONTEXT_MARKER,
            "以下是你和用户正在进行的中国象棋对局临时上下文。",
            "用户普通聊天时，如果提到棋局、局势、刚才那步、轮到谁或棋子，请自然参考；如果话题无关，不要生硬转回象棋。",
            f"用户执{player_side}，你执{bot_side}，当前轮到{turn_side}（{turn_owner}走）。",
            f"最近一步：{last_move}。",
        ]
        if limit > 0 and board.move_log:
            lines.append("最近几手：")
            lines.extend(f"- {item}" for item in board.move_log[-limit:])
        if limit > 0 and board.talk_log:
            lines.append("你之前说过：")
            lines.extend(f"- {item}" for item in board.talk_log[-limit:])
        lines.extend(
            [
                "棋盘坐标：0 行是黑方底线，9 行是红方底线；a-i 为列。大写为红方，小写为黑方。",
                "棋子字母：K帅 A仕 B相 N马 R车 C炮 P兵；k将 a士 b象 n马 r车 c炮 p卒。",
                self._ascii_board(board),
            ]
        )
        return "\n".join(lines)

    def _inject_request_hint(self, request: ProviderRequest, hint_text: str) -> None:
        if not hint_text or self._request_has_marker(request):
            return

        parts = getattr(request, "extra_user_content_parts", None)
        if TextPart is not None and isinstance(parts, list):
            try:
                part = TextPart(text=hint_text)
                mark_as_temp = getattr(part, "mark_as_temp", None)
                if callable(mark_as_temp):
                    part = mark_as_temp() or part
                parts.append(part)
                return
            except Exception as exc:
                logger.warning("%s board context extra part injection failed: %r", PLUGIN_LOG_NAME, exc)

        try:
            system_prompt = str(getattr(request, "system_prompt", "") or "")
            request.system_prompt = f"{system_prompt}\n\n{hint_text}".strip() if system_prompt else hint_text
        except Exception as exc:
            logger.warning("%s board context system prompt injection failed: %r", PLUGIN_LOG_NAME, exc)

    def _request_has_marker(self, request: ProviderRequest) -> bool:
        for attr in ("system_prompt", "prompt"):
            if BOARD_CONTEXT_MARKER in str(getattr(request, attr, "") or ""):
                return True
        parts = getattr(request, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            return False
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text", "")
            else:
                text = getattr(part, "text", "")
            if BOARD_CONTEXT_MARKER in str(text or ""):
                return True
        return False

    def _clean_llm_talk(self, text: str, sentence_count: int) -> list[str] | None:
        text = text.replace("\r", "\n").strip()
        if not text:
            return None
        candidates: list[str] = []
        for raw_line in re.split(r"[\n]+", text):
            line = raw_line.strip()
            line = re.sub(r"^[-*•\s]*\d+[.、)）]\s*", "", line)
            line = re.sub(r"^[-*•]+\s*", "", line)
            for prefix in ("台词：", "台词:", "回复：", "回复:", "Bot：", "Bot:", "机器人：", "机器人:"):
                if line.startswith(prefix):
                    line = line[len(prefix) :].strip()
            line = line.strip("`\"'“”‘’「」")
            if line:
                candidates.append(line)

        if not candidates:
            fallback = text.replace("\n", " ").strip("`\"'“”‘’「」")
            if fallback:
                candidates = [fallback]

        max_chars = self._llm_talk_max_chars()
        cleaned: list[str] = []
        for line in candidates:
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if len(line) > max_chars:
                line = line[:max_chars].rstrip("，。！？、 ") + "…"
            cleaned.append(line)
            if len(cleaned) >= sentence_count:
                break
        return cleaned or None

    def _fallback_bot_talk(
        self,
        bot_move: Move,
        bot_reason: str | None,
        bot_color: str,
        player_move: Move | None = None,
        sentence_count: int = 1,
    ) -> list[str]:
        bot_side = "红方" if bot_color == RED else "黑方"
        bot_text = describe_move(bot_move)
        player_text = describe_move(player_move) if player_move is not None else "开局"
        reason = (bot_reason or "").strip()
        templates = [
            f"你刚走 {player_text}，我应 {bot_text}。",
            f"这手 {bot_text} 先把局面稳住。",
            f"我执{bot_side}，这步 {bot_text} 不急。",
        ]
        if reason and self._engine_details_in_chat():
            templates.append(f"引擎看好 {bot_text}，{reason}。")
        max_chars = self._llm_talk_max_chars()
        result: list[str] = []
        for line in templates:
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) > max_chars:
                line = line[:max_chars].rstrip("，。！？、 ") + "…"
            result.append(line)
            if len(result) >= max(1, sentence_count):
                break
        return result or [f"我走 {bot_text}。"]

    def _format_turn_summary(self, player_move: Move, bot_move: Move) -> str | None:
        if not self._move_summary_enabled():
            return None
        return self._render_template(
            self._turn_summary_template(),
            {
                "player_move": describe_move(player_move),
                "bot_move": describe_move(bot_move),
                "player_from": format_coord(player_move.from_pos),
                "player_to": format_coord(player_move.to_pos),
                "bot_from": format_coord(bot_move.from_pos),
                "bot_to": format_coord(bot_move.to_pos),
            },
        )

    def _format_player_move_summary(self, player_move: Move) -> str | None:
        if not self._move_summary_enabled():
            return None
        return self._render_template(
            self._player_move_summary_template(),
            {
                "player_move": describe_move(player_move),
                "player_from": format_coord(player_move.from_pos),
                "player_to": format_coord(player_move.to_pos),
            },
        )

    def _format_talk_line(self, talk_line: str) -> str:
        return self._render_template(self._talk_line_template(), {"talk": talk_line}).strip() or talk_line

    def _render_template(self, template: str, values: dict[str, str]) -> str:
        result = template
        for key, value in values.items():
            result = result.replace("{" + key + "}", value)
        return result.strip()

    def _remember_turn(
        self,
        board: Board,
        player_move: Move | None,
        bot_move: Move,
        talk_lines: list[str] | None,
        player_in_check: bool,
    ) -> None:
        player_text = describe_move(player_move) if player_move is not None else "开局"
        note = f"玩家：{player_text}；我：{describe_move(bot_move)}"
        if player_in_check:
            note += "；玩家被将军"
        board.move_log.append(note)
        board.move_log = board.move_log[-8:]
        for line in talk_lines or []:
            clean = re.sub(r"\s+", " ", line).strip()
            if clean:
                board.talk_log.append(clean)
        board.talk_log = board.talk_log[-8:]

    def _append_endgame_message(
        self,
        board: Board,
        color: str,
        message_parts: list[str],
        winner_is_player: bool,
    ) -> bool:
        if is_checkmate(board, color):
            message_parts.append("将死，恭喜获胜。" if winner_is_player else "你被将死，本局结束。")
            return True
        if is_stalemate(board, color):
            message_parts.append("对方无子可走，本局结束。" if winner_is_player else "你已无合法走法，本局结束。")
            return True
        return False
