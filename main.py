from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .engine.ai import choose_move, describe_move
from .engine.board import BLACK, RED, Board, Move, opponent
from .engine.parser import ParseError, format_coord, parse_coord
from .engine.pikafish_adapter import PikafishEngine
from .engine.rules import IllegalMoveError, is_checkmate, is_in_check, is_stalemate
from .engine.xqwlight_adapter import choose_move_xqwlight
from .render.board_image import render_board
from .storage.session_store import SessionStore


MOVE_TEXT_RE = re.compile(r"^\s*([a-i][0-9])(?:(?:\s*(?:->|[-=>])\s*)|\s+)?([a-i][0-9])\s*$", re.IGNORECASE)
BARE_MOVE_RE = r"^\s*[a-i][0-9](?:(?:\s*(?:->|[-=>])\s*)|\s+)?[a-i][0-9]\s*$"
MOVE_COMMAND_PREFIXES = ("走棋", "走", "move")
PLUGIN_LOG_NAME = "xiangqi_arena"


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

    @filter.command("开局", alias=["象棋", "象棋新局", "新局", "下棋"])
    async def new_game(self, event: AstrMessageEvent):
        """开始新对局，默认玩家执红。"""
        session_id = self._session_id(event)
        board = Board.new_game(player_color=RED)
        self.store.save(session_id, board)
        logger.info("%s new game: %s", PLUGIN_LOG_NAME, session_id)
        yield event.plain_result("新局开始，你执红先行。直接发 a6 b6 或 a6-b6 就能走棋。")
        yield event.image_result(str(self._render_session_board(session_id, board)))

    @filter.command("执黑", alias=["象棋执黑", "开局执黑"])
    async def new_game_black(self, event: AstrMessageEvent):
        """开始新对局，玩家执黑，Bot 先手。"""
        session_id = self._session_id(event)
        board = Board.new_game(player_color=BLACK)
        bot_move, reason = await self._choose_bot_move(board, RED)
        message = "新局开始，你执黑。"
        talk_lines = None
        if bot_move is not None:
            board.push_state()
            board.apply_move(bot_move.from_pos, bot_move.to_pos)
            message += f" Bot 先手：{describe_move(bot_move)}"
            if reason and self._show_engine_details():
                message += f"（{reason}）"
            talk_lines = await self._generate_bot_talk(event, board, bot_move, reason, RED)
        self.store.save(session_id, board)
        yield event.plain_result(message)
        async for result in self._yield_talk(event, talk_lines):
            yield result
        yield event.image_result(str(self._render_session_board(session_id, board)))

    @filter.command("走", alias=["走棋", "move"])
    async def move(self, event: AstrMessageEvent, from_coord: str | None = None, to_coord: str | None = None):
        """按坐标走棋，例如：走 a6 b6。"""
        try:
            from_pos, to_pos = self._parse_move(from_coord, to_coord, getattr(event, "message_str", ""))
        except ParseError as exc:
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
        board = self.store.load(session_id)
        if board is None:
            yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        if len(board.history) < 2:
            yield event.plain_result("当前没有可撤销的完整回合。")
            return
        board.pop_state()
        board.pop_state()
        self.store.save(session_id, board)
        yield event.plain_result("已撤销上一整个回合。")
        yield event.image_result(str(self._render_session_board(session_id, board)))

    @filter.command("认输", alias=["投降", "结束"])
    async def resign(self, event: AstrMessageEvent):
        """认输并结束当前对局。"""
        session_id = self._session_id(event)
        board = self.store.load(session_id)
        if board is None:
            yield event.plain_result("当前没有对局，请先发送“开局”。")
            return
        self.store.delete(session_id)
        yield event.plain_result("你已认输，本局结束。")

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
        if reason and self._show_engine_details():
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

    async def terminate(self):
        if self._pikafish_engine is not None:
            await self._pikafish_engine.close()
            self._pikafish_engine = None
        return None

    async def _handle_player_move(
        self,
        event: AstrMessageEvent,
        from_pos: tuple[int, int],
        to_pos: tuple[int, int],
        quiet_no_game: bool,
    ):
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
            player_move = self._apply_player_move(board, from_pos, to_pos, player_color)
        except (IllegalMoveError, ValueError) as exc:
            yield event.plain_result(str(exc))
            return

        message_parts = [f"你走了 {format_coord(player_move.from_pos)} -> {format_coord(player_move.to_pos)}"]
        if self._append_endgame_message(board, opponent(player_color), message_parts, winner_is_player=True):
            self.store.delete(session_id)
            yield event.plain_result(" ".join(message_parts))
            yield event.image_result(str(self._render_session_board(session_id, board)))
            return

        bot_color = opponent(player_color)
        bot_move, bot_reason = await self._choose_bot_move(board, bot_color)
        if bot_move is None:
            message_parts.append("Bot 无合法走法，本局结束。")
            self.store.delete(session_id)
            yield event.plain_result(" ".join(message_parts))
            yield event.image_result(str(self._render_session_board(session_id, board)))
            return

        board.push_state()
        board.apply_move(bot_move.from_pos, bot_move.to_pos)
        bot_message = f"Bot 走了 {describe_move(bot_move)}"
        if bot_reason and self._show_engine_details():
            bot_message += f"（{bot_reason}）"
        message_parts.append(bot_message)
        talk_lines = await self._generate_bot_talk(event, board, bot_move, bot_reason, bot_color, player_move)

        if is_in_check(board, player_color):
            message_parts.append("你现在被将军。")
        if self._append_endgame_message(board, player_color, message_parts, winner_is_player=False):
            self.store.delete(session_id)
        else:
            self.store.save(session_id, board)

        yield event.plain_result(" ".join(message_parts))
        async for result in self._yield_talk(event, talk_lines):
            yield result
        yield event.image_result(str(self._render_session_board(session_id, board)))

    async def _yield_talk(self, event: AstrMessageEvent, talk_lines: list[str] | None):
        if not talk_lines:
            return
        for talk_line in talk_lines:
            yield event.plain_result(f"「{talk_line}」")

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

    def _bool_config(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", "关闭", "否"}
        return bool(value)

    def _int_config(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.config.get(key, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _float_config(self, key: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(self.config.get(key, default) or default)
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
        return self._int_config("xqwlight_failure_cooldown_seconds", 600, 0, 3600)

    def _xqwlight_jar_path(self) -> str | None:
        return self._str_config("xqwlight_jar_path") or None

    def _pikafish_failure_cooldown_seconds(self) -> int:
        return self._int_config("pikafish_failure_cooldown_seconds", 600, 0, 3600)

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

    def _show_engine_details(self) -> bool:
        return self._bool_config("show_engine_details", False)

    def _llm_talk_enabled(self) -> bool:
        return self._bool_config("llm_talk_enabled", True)

    def _llm_extra_prompt(self) -> str:
        return self._str_config("llm_extra_prompt")

    def _llm_talk_timeout(self) -> float:
        return self._float_config("llm_talk_timeout", 3.0, 1.0, 15.0)

    def _llm_talk_failure_cooldown_seconds(self) -> int:
        return self._int_config("llm_talk_failure_cooldown_seconds", 600, 0, 3600)

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
        max_chars = self._llm_talk_max_chars()
        return (
            "你正在当前聊天会话里下中国象棋。请沿用当前 AstrBot 人格说话，但只为 Bot 自己说话。\n"
            "刚才完整回合：人类玩家先走，Bot 随后应对。\n"
            f"人类玩家走：{player_move_text}。\n"
            f"Bot 也就是你走：{describe_move(bot_move)}{captured}。\n"
            f"Bot 执{bot_side}。引擎信息：{bot_reason or '无'}。\n"
            f"对手当前{'被将军' if player_in_check else '没有被将军'}。\n"
            f"当前棋盘，0行是黑方底线，9行是红方底线：\n{board_text}\n\n"
            "输出要求：\n"
            f"- 只输出{sentence_count}句中文台词，每句单独一行，每句最多{max_chars}字。\n"
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
        if reason and self._show_engine_details():
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
