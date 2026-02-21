import json
from typing import Any
from datetime import datetime

from anyio import Path as AsyncPath
from nonebot import logger, require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store


class CacheManager:
    def __init__(
        self,
        group_cache_hours: float = 72.0,
        member_cache_hours: float = 72.0,
    ):
        self._group_cache_hours = group_cache_hours
        self._member_cache_hours = member_cache_hours
        self._cache_file = store.get_plugin_cache_file("cache.json")
        self._group_info_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._member_info_cache: dict[
            tuple[int, int], tuple[float, dict[str, Any]]
        ] = {}

    def _get_group_cache_seconds(self) -> float:
        return self._group_cache_hours * 3600

    def _get_member_cache_seconds(self) -> float:
        return self._member_cache_hours * 3600

    async def load(self) -> None:
        cache_file = AsyncPath(self._cache_file)

        if not await cache_file.exists():
            return

        try:
            import aiofiles

            async with aiofiles.open(self._cache_file, encoding="utf-8") as f:
                data = json.loads(await f.read())

            now = datetime.now().timestamp()

            for k, v in data.get("group_info", {}).items():
                if now - v[0] < self._get_group_cache_seconds():
                    self._group_info_cache[int(k)] = (v[0], v[1])

            for k, v in data.get("member_info", {}).items():
                if now - v[0] < self._get_member_cache_seconds():
                    gid, uid = map(int, k.split(":"))
                    self._member_info_cache[(gid, uid)] = (v[0], v[1])

            logger.debug(
                f"加载缓存: 群信息 {len(self._group_info_cache)} 条, "
                f"成员信息 {len(self._member_info_cache)} 条"
            )
        except Exception as e:
            logger.warning(f"加载缓存失败: {e}")

    async def save(self) -> None:
        try:
            cache_dir = AsyncPath(self._cache_file.parent)
            await cache_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "group_info": {
                    str(k): [v[0], v[1]] for k, v in self._group_info_cache.items()
                },
                "member_info": {
                    f"{k[0]}:{k[1]}": [v[0], v[1]]
                    for k, v in self._member_info_cache.items()
                },
            }

            import aiofiles

            async with aiofiles.open(self._cache_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False))

        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")

    def get_group(self, group_id: int) -> dict[str, Any] | None:
        if group_id in self._group_info_cache:
            cached_time, cached_data = self._group_info_cache[group_id]
            if (
                datetime.now().timestamp() - cached_time
                < self._get_group_cache_seconds()
            ):
                return cached_data
            del self._group_info_cache[group_id]
        return None

    def set_group(self, group_id: int, data: dict[str, Any]) -> None:
        self._group_info_cache[group_id] = (datetime.now().timestamp(), data)

    def get_member(self, group_id: int, user_id: int) -> dict[str, Any] | None:
        cache_key = (group_id, user_id)
        if cache_key in self._member_info_cache:
            cached_time, cached_data = self._member_info_cache[cache_key]
            if (
                datetime.now().timestamp() - cached_time
                < self._get_member_cache_seconds()
            ):
                return cached_data
            del self._member_info_cache[cache_key]
        return None

    def set_member(self, group_id: int, user_id: int, data: dict[str, Any]) -> None:
        self._member_info_cache[(group_id, user_id)] = (
            datetime.now().timestamp(),
            data,
        )
