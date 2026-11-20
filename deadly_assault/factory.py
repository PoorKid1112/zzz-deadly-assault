from __future__ import annotations

from typing import TYPE_CHECKING, cast

from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.application.application_factory import ApplicationFactory

from . import const
from .app import DeadlyAssaultApp
from .config import DeadlyAssaultConfig
from .run_record import DeadlyAssaultRunRecord

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
            self.get_run_record(instance_idx),
        )

    def create_config(self, instance_idx: int, group_id: str) -> DeadlyAssaultConfig:
        return DeadlyAssaultConfig(instance_idx, group_id)

    def create_run_record(self, instance_idx: int) -> DeadlyAssaultRunRecord:
        config = cast(
            DeadlyAssaultConfig,
            self.get_config(
                instance_idx=instance_idx,
                group_id=application_const.DEFAULT_GROUP_ID,
            ),
        )
        return DeadlyAssaultRunRecord(
            config=config,
            instance_idx=instance_idx,
            game_refresh_hour_offset=self.ctx.game_account_config.game_refresh_hour_offset,
        )
