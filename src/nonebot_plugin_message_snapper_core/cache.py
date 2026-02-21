import os
import asyncio
from pathlib import Path
from urllib.request import urlopen

from anyio import Path as AsyncPath
from nonebot import logger, require

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store


class CacheManager:
    def __init__(self):
        self._qface_dir = store.get_plugin_cache_dir() / "qface"
        # In-progress download tasks to dedupe concurrent requests per face_id
        # Use lightweight future-based dedupe: dict maps face_id -> Future
        self._qface_tasks: dict[int, asyncio.Future] = {}
        # Limit concurrent downloads to avoid excessive threads/IO
        self._download_semaphore: asyncio.Semaphore = asyncio.Semaphore(8)

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
