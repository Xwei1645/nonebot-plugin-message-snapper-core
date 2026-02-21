from typing import Any
from pathlib import Path
from datetime import datetime

from nonebot import logger, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

require("nonebot_plugin_htmlrender")
require("nonebot_plugin_localstore")

from nonebot_plugin_htmlrender import template_to_pic

from .cache import CacheManager

DEFAULT_FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, '
    '"Helvetica Neue", Arial, "PingFang SC", '
    '"Hiragino Sans GB", "Microsoft YaHei", sans-serif'
)

AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"


class MessageSnapper:
    def __init__(
        self,
        template: str = "default.html",
        font_family: str | None = None,
        group_cache_hours: float = 72.0,
        member_cache_hours: float = 72.0,
        template_path: Path | None = None,
    ):
        self._template = template
        self._font_family = font_family or DEFAULT_FONT_FAMILY
        self._cache_manager = CacheManager(group_cache_hours, member_cache_hours)
        self._template_path = template_path or Path(__file__).parent / "templates"

    async def load_cache(self) -> None:
        await self._cache_manager.load()

    async def save_cache(self) -> None:
        await self._cache_manager.save()

    async def get_group_info(self, bot: Bot, group_id: int) -> dict[str, Any]:
        cached = self._cache_manager.get_group(group_id)
        if cached is not None:
            return cached

        try:
            info = await bot.get_group_info(group_id=group_id)
            self._cache_manager.set_group(group_id, info)
            return info
        except Exception:
            return {"group_name": "未知群", "member_count": 0}

    async def get_member_info(
        self, bot: Bot, group_id: int, user_id: int
    ) -> dict[str, Any]:
        cached = self._cache_manager.get_member(group_id, user_id)
        if cached is not None:
            return cached

        try:
            info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
            self._cache_manager.set_member(group_id, user_id, info)
            return info
        except Exception:
            return {}

    async def generate_snapshot(
        self,
        bot: Bot,
        group_id: int,
        user_id: int,
        message: Message,
        time: float,
        sender_name: str | None = None,
        sender_level: str | None = None,
        sender_title: str | None = None,
        sender_role: str | None = None,
    ) -> bytes:
        reply_preview = await self._extract_reply_preview(bot, message, group_id)
        message_segments = await self._extract_message_segments(bot, group_id, message)
        message_content = await self._extract_text_content(bot, group_id, message)
        single_image_only = self._is_single_image_message(message_segments)

        if not message_segments and reply_preview is None:
            raise ValueError("无法获取消息内容，可能包含不支持的消息类型")

        time_str = datetime.fromtimestamp(time).strftime("%Y-%m-%d %H:%M")

        group_info = await self.get_group_info(bot, group_id)
        group_name = group_info.get("group_name", "未知群")
        member_count = group_info.get("member_count", 0)

        member_info = await self.get_member_info(bot, group_id, user_id)
        if member_info:
            level = member_info.get("level", "") or ""
            title = member_info.get("title", "") or ""
            role = member_info.get("role", "") or ""
            card = member_info.get("card", "") or ""
            nickname = member_info.get("nickname", "") or ""
            final_sender_name = card or nickname or "未知用户"
        else:
            level = sender_level or ""
            title = sender_title or ""
            role = sender_role or ""
            final_sender_name = sender_name or "未知用户"

        avatar_url = AVATAR_URL.format(user_id=user_id)

        img_bytes = await template_to_pic(
            template_path=str(self._template_path),
            template_name=self._template,
            templates={
                "font_family": self._font_family,
                "group_name": group_name,
                "member_count": member_count,
                "avatar_url": avatar_url,
                "sender_name": final_sender_name,
                "sender_id": user_id,
                "level": level,
                "title": title,
                "role": role,
                "reply_preview": reply_preview,
                "message_segments": message_segments,
                "single_image_only": single_image_only,
                "message_content": message_content,
                "time": time_str,
            },
        )

        logger.info(f"成功生成消息快照: 用户 {final_sender_name}({user_id})")
        return img_bytes

    def _format_time(self, timestamp: Any) -> str:
        try:
            return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return "未知时间"

    async def _extract_reply_preview(
        self, bot: Bot, message: Message, group_id: int
    ) -> dict[str, Any] | None:
        if not isinstance(message, Message):
            return None

        for seg in message:
            if seg.type != "reply":
                continue
            message_id = seg.data.get("id")
            if message_id is None:
                return None
            try:
                quoted = await bot.get_msg(message_id=int(message_id))
            except Exception as e:
                logger.warning(f"获取引用消息失败: {e}")
                return None

            sender = quoted.get("sender", {}) if isinstance(quoted, dict) else {}
            sender_name = (
                sender.get("card")
                or sender.get("nickname")
                or str(sender.get("user_id") or "未知用户")
            )
            quoted_message = self._normalize_message_payload(quoted.get("message", ""))
            segments = await self._extract_message_segments(
                bot, group_id, quoted_message
            )
            content = await self._extract_text_content(bot, group_id, quoted_message)
            return {
                "sender_name": sender_name,
                "time": self._format_time(quoted.get("time", 0)),
                "segments": segments,
                "content": content or "[消息]",
            }
        return None

    def _normalize_message_payload(self, payload: Any) -> Message:
        if isinstance(payload, Message):
            return payload
        if isinstance(payload, str):
            return Message(payload)
        if isinstance(payload, list):
            segments: list[MessageSegment] = []
            for item in payload:
                if isinstance(item, MessageSegment):
                    segments.append(item)
                    continue
                if isinstance(item, dict):
                    seg_type = item.get("type")
                    seg_data = item.get("data", {})
                    if isinstance(seg_type, str) and isinstance(seg_data, dict):
                        segments.append(MessageSegment(seg_type, seg_data))
                        continue
                logger.warning(f"忽略无法解析的消息段: {item!r}")
            return Message(segments)
        if isinstance(payload, dict):
            seg_type = payload.get("type")
            seg_data = payload.get("data", {})
            if isinstance(seg_type, str) and isinstance(seg_data, dict):
                return Message([MessageSegment(seg_type, seg_data)])
        return Message(str(payload))

    async def _extract_message_segments(
        self, bot: Bot, group_id: int, message: Message
    ) -> list[dict[str, str]]:
        message = self._normalize_message_payload(message)

        parts = []
        for seg in message:
            if seg.type == "text":
                text = seg.data.get("text", "")
                if text:
                    parts.append({"type": "text", "content": text})
            elif seg.type == "image":
                image_url = seg.data.get("url") or seg.data.get("file") or ""
                if image_url:
                    parts.append({"type": "image", "content": image_url})
                else:
                    parts.append({"type": "text", "content": "[图片]"})
            elif seg.type == "face":
                face_id = seg.data.get("id", 0)
                parts.append({"type": "text", "content": f"[表情:{face_id}]"})
            elif seg.type == "emoji":
                parts.append(
                    {"type": "text", "content": seg.data.get("text", "[emoji]")}
                )
            elif seg.type == "at":
                qq = seg.data.get("qq", "")
                name = ""
                user_id = None
                if isinstance(qq, int):
                    user_id = qq
                else:
                    try:
                        user_id = int(qq)
                    except Exception:
                        user_id = None
                if user_id is not None:
                    member_info = await self.get_member_info(bot, group_id, user_id)
                    card = member_info.get("card", "") or ""
                    nickname = member_info.get("nickname", "") or ""
                    name = card or nickname or str(user_id)
                else:
                    name = qq or ""
                parts.append({"type": "text", "content": f"@{name} "})
            elif seg.type == "reply":
                continue
            else:
                parts.append({"type": "text", "content": f"[{seg.type}]"})

        merged: list[dict[str, str]] = []
        for p in parts:
            if merged and p["type"] == "text" and merged[-1]["type"] == "text":
                prev = merged[-1]["content"]
                cur = p["content"]
                merged[-1]["content"] = prev.rstrip() + " " + cur.lstrip()
            else:
                merged.append(p.copy())

        return merged

    async def _extract_text_content(
        self, bot: Bot, group_id: int, message: Message
    ) -> str:
        parts = []
        for seg in await self._extract_message_segments(bot, group_id, message):
            if seg["type"] == "image":
                parts.append("[图片]")
            else:
                parts.append(seg["content"])
        return "".join(parts).strip()

    def _is_single_image_message(self, message_segments: list[dict[str, str]]) -> bool:
        return (
            len(message_segments) == 1
            and message_segments[0].get("type") == "image"
            and bool(message_segments[0].get("content"))
        )
