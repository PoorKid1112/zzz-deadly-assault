from __future__ import annotations

from one_dragon.base.operation.application.application_config import ApplicationConfig

from . import const


class DeadlyAssaultConfig(ApplicationConfig):
    """危局强袭战配置。"""

    def __init__(self, instance_idx: int, group_id: str) -> None:
        super().__init__(
            app_id=const.APP_ID,
            instance_idx=instance_idx,
            group_id=group_id,
        )

    @property
    def auto_team(self) -> bool:
        """是否自动进行配队推荐（步骤6-12）"""
        return self.get('auto_team', True)

    @auto_team.setter
    def auto_team(self, value: bool) -> None:
        self.update('auto_team', value)

    @property
    def wait_generate_seconds(self) -> int:
        """生成配队等待时间（秒，步骤9）"""
        return self.get('wait_generate_seconds', 300)

    @wait_generate_seconds.setter
    def wait_generate_seconds(self, value: int) -> None:
        self.update('wait_generate_seconds', value)

    @property
    def walk_duration(self) -> float:
        """进入战斗后向前走的时长（秒，步骤15）"""
        return float(self.get('walk_duration', 5.0))

    @walk_duration.setter
    def walk_duration(self, value: float) -> None:
        self.update('walk_duration', value)

    @property
    def claim_rewards(self) -> bool:
        """战斗结束后是否领取荣誉酬赏（步骤26-28）"""
        return self.get('claim_rewards', True)

    @claim_rewards.setter
    def claim_rewards(self, value: bool) -> None:
        self.update('claim_rewards', value)

    @property
    def auto_battle_config(self) -> str:
        """使用的自动战斗策略名称（留空则使用全局战斗助手设置）"""
        return self.get('auto_battle_config', '')

    @auto_battle_config.setter
    def auto_battle_config(self, value: str) -> None:
        self.update('auto_battle_config', value)

    @property
    def cycle_anchor_dt(self) -> str:
        """危局强袭战双周周五刷新基准日 (yyyyMMdd，默认 20261120 周五 04:00)"""
        return self.get('cycle_anchor_dt', '20261120')

    @cycle_anchor_dt.setter
    def cycle_anchor_dt(self, value: str) -> None:
        self.update('cycle_anchor_dt', value)

    @property
    def two_day_mode(self) -> bool:
        """分天运行模式：启用后第1天仅执行预处理（触发生成更多后直接返回），第2天再完整挑战（默认关闭）"""
        return bool(self.get('two_day_mode', False))

    @two_day_mode.setter
    def two_day_mode(self, value: bool) -> None:
        self.update('two_day_mode', bool(value))
