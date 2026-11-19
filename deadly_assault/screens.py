from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from one_dragon.utils.log_utils import log

from .const import APP_ID

if TYPE_CHECKING:
    from one_dragon.base.screen.screen_loader import ScreenContext

PLUGIN_DIR = Path(__file__).parent
SCREEN_DIR: Path = PLUGIN_DIR / 'screen_info'


def ensure_screens(screen_loader: ScreenContext) -> bool:
    """确保插件画面已注入到框架的 screen_loader 中。"""
    if screen_loader.get_area('危局强袭战-关卡详情', '锁定确认-确认') is not None:
        return False
    if SCREEN_DIR.is_dir():
        screen_loader.load_extra_screen_dir(str(SCREEN_DIR), default_app_id=APP_ID)
        log.info('危局强袭战画面定义已加载: %s', SCREEN_DIR)
        return True
    return False
