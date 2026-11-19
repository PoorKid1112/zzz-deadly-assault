from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.application.application_factory import ApplicationFactory

from . import const
from .app import DeadlyAssaultApp
from .config import DeadlyAssaultConfig

if TYPE_CHECKING:
    from one_dragon.base.operation.application.application_base import Application
    from zzz_od.context.zzz_context import ZContext


class DeadlyAssaultFactory(ApplicationFactory):
    """危局强袭战插件应用工厂。"""

    def __init__(self, ctx: ZContext) -> None:
        super().__init__(const)
        self.ctx: ZContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return DeadlyAssaultApp(
            self.ctx,
            self.get_config(instance_idx, group_id),
        )

    def create_config(self, instance_idx: int, group_id: str) -> DeadlyAssaultConfig:
        return DeadlyAssaultConfig(instance_idx, group_id)
