from nonebot.plugin import PluginMetadata

from .cache import CacheManager
from .service import MessageSnapper

__plugin_meta__ = PluginMetadata(
    name="消息快照核心库",
    description="将消息转换为图片快照的核心库，可供其他插件复用",
    usage="from nonebot_plugin_message_snapper_core import MessageSnapper",
    type="library",
    homepage="https://github.com/Xwei1645/nonebot-plugin-message-snapper-core",
)

__all__ = ["CacheManager", "MessageSnapper"]
