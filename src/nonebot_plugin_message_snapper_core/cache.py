import os
import json
import asyncio
from typing import Any
from pathlib import Path
from datetime import datetime
from urllib.request import urlopen

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
        self._qface_dir = store.get_plugin_cache_dir() / "qface"
        self._group_info_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._member_info_cache: dict[
            tuple[int, int], tuple[float, dict[str, Any]]
        ] = {}
        # In-progress download tasks to dedupe concurrent requests per face_id
        # Use lightweight future-based dedupe: dict maps face_id -> Future
        self._qface_tasks: dict[int, asyncio.Future] = {}
        # Limit concurrent downloads to avoid excessive threads/IO
        self._download_semaphore: asyncio.Semaphore = asyncio.Semaphore(8)

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

    async def get_qface_image(self, face_id: int) -> str | None:
        """Get local URI for a QFace; download if missing.

        This method dedupes concurrent downloads per face_id by reusing an
        in-progress asyncio.Task. The download writes to a temporary file and
        atomically replaces the final file to avoid partial writes.
        """
        if face_id < 0:
            return None

        local_path = self._qface_dir / f"{face_id}.png"
        file_path = AsyncPath(local_path)
        if await file_path.exists():
            return local_path.as_uri()

        await AsyncPath(self._qface_dir).mkdir(parents=True, exist_ok=True)

        # Lightweight dedupe using a Future placeholder per face_id
        loop = asyncio.get_running_loop()
        placeholder = loop.create_future()
        existing = self._qface_tasks.setdefault(face_id, placeholder)

        if existing is placeholder:
            try:
                await self._download_and_save_qface(face_id, local_path)
                placeholder.set_result(True)
            except Exception as e:
                placeholder.set_exception(e)
            finally:
                cur = self._qface_tasks.get(face_id)
                if cur is placeholder:
                    self._qface_tasks.pop(face_id, None)
        else:
            try:
                await existing
            except Exception:
                pass

        return local_path.as_uri() if await file_path.exists() else None

    async def _download_and_save_qface(
        self,
        face_id: int,
        local_path: Path,
    ) -> None:
        url = f"https://koishi.js.org/QFace/assets/qq_emoji/{face_id}/png/{face_id}.png"
        tmp_path = local_path.with_suffix(".tmp")
        try:
            # limit concurrent downloads
            async with self._download_semaphore:
                data = await asyncio.to_thread(self._download_qface, url)
                # write to temp file first
                async with await AsyncPath(tmp_path).open("wb") as f:
                    await f.write(data)
                # atomic replace
                await asyncio.to_thread(os.replace, str(tmp_path), str(local_path))
        except Exception as e:
            # cleanup temp file if exists
            try:
                await asyncio.to_thread(os.remove, str(tmp_path))
            except Exception:
                pass
            logger.warning(f"下载 QFace 表情失败({face_id}): {e}")
            raise

    @staticmethod
    def _download_qface(url: str) -> bytes:
        with urlopen(url, timeout=10) as response:
            return response.read()
