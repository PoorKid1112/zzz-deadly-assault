from __future__ import annotations

import datetime
import time
from typing import TYPE_CHECKING, ClassVar

from one_dragon.base.operation.application_run_record import AppRunRecord
from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log

from . import const

if TYPE_CHECKING:
    from .config import DeadlyAssaultConfig


class DeadlyAssaultRunRecord(AppRunRecord):
    """危局强袭战运行记录与双周周期运行判断。

    游戏刷新机制：
    - 危局强袭战与式舆防卫战每周五凌晨 04:00（按账号区服偏移）交替刷新，各自周期为 14 天：
      这一周周五刷新危局 -> 下一周周五刷新式舆 -> 下下周周五刷新危局 -> 下下下周周五刷新式舆，以此类推。
    - 实机验证基准刷新日：2026-11-20（周五 04:00 危局强袭战刷新）。

    分天运行模式（config.two_day_mode，默认关闭）：
    - 启用后：同一 14 天周期内第 1 天仅按 prepare 规则触发配队生成后结束，并在「当天」标记为已完成以免重复运行；
      进入第 2 天后（含跨第 1 个式舆周五）自动变为待运行，执行这一次全量挑战并标记全周期通关（若第 2 天未运行或中途失败则延后补完这一次）；
      全周期通关后第 3 天起持续跳过（含跨第 1 个式舆周五），直到第 2 个周五（下次危局 04:00 刷新）自动彻底重置（若第 1 天预处理跨了第 2 个周五也一并重置，在新周期重新先执行预处理）。
    """

    DEFAULT_ANCHOR_DT: ClassVar[str] = '20261120'
    CYCLE_DAYS: ClassVar[int] = 14
    TOTAL_SLOTS: ClassVar[int] = 3

    def __init__(
        self,
        config: DeadlyAssaultConfig | None = None,
        instance_idx: int | None = None,
        game_refresh_hour_offset: int = 0,
    ) -> None:
        self.config: DeadlyAssaultConfig | None = config
        super().__init__(
            app_id=const.APP_ID,
            instance_idx=instance_idx,
            game_refresh_hour_offset=game_refresh_hour_offset,
        )

    @staticmethod
    def _is_valid_dt(dt_str: object) -> bool:
        if not isinstance(dt_str, str) or len(dt_str) != 8 or not dt_str.isdigit():
            return False
        try:
            datetime.datetime.strptime(dt_str, '%Y%m%d')
            return True
        except ValueError:
            return False

    def get_anchor_dt(self) -> str:
        """获取危局双周刷新基准日（默认 20261120 周五 04:00）。"""
        if self.config is not None:
            anchor = self.config.cycle_anchor_dt
            if self._is_valid_dt(anchor):
                return anchor
        return self.DEFAULT_ANCHOR_DT

    def get_period_start_dt(self, dt: str | None = None) -> str:
        """计算给定游戏日 dt 所属危局 14 天周期的起始周五日期 (yyyyMMdd)。"""
        target_dt = dt if dt is not None else self.get_current_dt()
        anchor_dt = self.get_anchor_dt()
        delta_days = os_utils.dt_day_diff(target_dt, anchor_dt)
        cycle_index = delta_days // self.CYCLE_DAYS
        return os_utils.add_dt_offset(anchor_dt, cycle_index * self.CYCLE_DAYS)

    def get_next_refresh_dt(self, dt: str | None = None) -> str:
        """计算下一次危局强袭战刷新日的周五日期 (yyyyMMdd)。"""
        return os_utils.add_dt_offset(self.get_period_start_dt(dt), self.CYCLE_DAYS)

    def is_deadly_assault_refresh_week(self, dt: str | None = None) -> bool:
        """判断指定游戏日是否处于危局刚刷新的前 7 天（True=本周五刷新危局，False=本周五刷新式舆）。"""
        target_dt = dt if dt is not None else self.get_current_dt()
        start_dt = self.get_period_start_dt(target_dt)
        days_in_cycle = os_utils.dt_day_diff(target_dt, start_dt)
        return 0 <= days_in_cycle < 7

    @property
    def recorded_period_start_dt(self) -> str:
        """记录对应的危局 14 天周期起始周五日期。"""
        stored = self.get('period_start_dt', '')
        if self._is_valid_dt(stored):
            return stored
        if self._is_valid_dt(self.dt):
            return self.get_period_start_dt(self.dt)
        return ''

    @property
    def finished_slots(self) -> list[int]:
        """当前周期已完成的首领槽位下标列表（0=左，1=中，2=右）。"""
        raw = self.get('finished_slots', [])
        if not isinstance(raw, list):
            return []
        result: list[int] = []
        for item in raw:
            if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < self.TOTAL_SLOTS:
                if item not in result:
                    result.append(item)
        result.sort()
        return result

    def add_slot_finished(self, slot_idx: int) -> None:
        """记录某个首领槽位在本周期内已完成。"""
        if not (0 <= slot_idx < self.TOTAL_SLOTS):
            return
        history = self.finished_slots
        if slot_idx not in history:
            history.append(slot_idx)
            history.sort()
            self.update('finished_slots', history)

    def mark_all_slots_finished(self) -> None:
        """标记当前周期全部 3 个首领槽位已完成。"""
        self.update('finished_slots', list(range(self.TOTAL_SLOTS)))

    def next_pending_slot_idx(self) -> int | None:
        """获取当前周期下一个尚未完成的首领槽位下标（全部完成返回 None）。"""
        history = set(self.finished_slots)
        for i in range(self.TOTAL_SLOTS):
            if i not in history:
                return i
        return None

    @property
    def rewards_claimed(self) -> bool:
        """当前周期荣誉酬赏是否已领取。"""
        return bool(self.get('rewards_claimed', False))

    @rewards_claimed.setter
    def rewards_claimed(self, value: bool) -> None:
        self.update('rewards_claimed', bool(value))

    @property
    def prepare_only_pending(self) -> bool:
        """分天模式下第 1 天预处理已完成但第 2 天尚未完整通关。"""
        return bool(self.get('prepare_only_pending', False))

    @property
    def prepare_period_start_dt(self) -> str:
        """记录最近一次分天预处理所属的危局 14 天周期起始日。"""
        val = self.get('prepare_period_start_dt', '')
        return val if self._is_valid_dt(val) else ''

    @property
    def prepare_dt(self) -> str:
        """记录最近一次分天预处理执行的游戏日。"""
        val = self.get('prepare_dt', '')
        return val if self._is_valid_dt(val) else ''

    @property
    def full_completed(self) -> bool:
        """当前周期完整流程（战斗+领奖或主界面已全通关）是否已完成。"""
        return bool(self.get('full_completed', False))

    def has_prepare_done_in_period(self, dt: str | None = None) -> bool:
        """判断当前 14 天周期内是否已完成过第 1 天预处理。"""
        if self._should_reset_by_dt():
            return False
        target_period = self.get_period_start_dt(dt)
        return self.prepare_period_start_dt == target_period

    def mark_prepare_done(self) -> None:
        """分天模式第 1 天预处理完成：标记当天已做预处理，保留第 2 天待战斗。"""
        current_dt = self.get_current_dt()
        current_period = self.get_period_start_dt(current_dt)
        self.update('prepare_only_pending', True, False)
        self.update('full_completed', False, False)
        self.update('prepare_dt', current_dt, False)
        self.update('prepare_period_start_dt', current_period, False)
        self.update('period_start_dt', current_period, False)
        self.save()

    def mark_full_completed(self) -> None:
        """标记当前 14 天周期已完整通关并清除分天待战状态。"""
        current_period = self.get_period_start_dt(self.get_current_dt())
        self.update('full_completed', True, False)
        self.update('prepare_only_pending', False, False)
        self.update('period_start_dt', current_period, False)
        self.save()

    def _should_reset_by_dt(self) -> bool:
        """判断是否已跨入新的危局 14 天刷新周期。"""
        current_dt = self.get_current_dt()
        recorded_period = self.recorded_period_start_dt
        if not recorded_period:
            return True
        return recorded_period != self.get_period_start_dt(current_dt)

    @property
    def is_finished_by_period(self) -> bool:
        """判断当前 14 天危局周期是否已完成（或分天模式第 1 天当天已预处理完毕）。"""
        current_dt = self.get_current_dt()
        if self._should_reset_by_dt():
            return False

        if self.full_completed:
            return True
        if self.next_pending_slot_idx() is None:
            if self.config is None or not self.config.claim_rewards or self.rewards_claimed:
                return True

        # 分天模式第 1 天完成预处理：仅当天视为已完成，进入次日（包含跨第一个式舆周五）即开放第 2 天挑战
        if self.prepare_only_pending:
            if self.config is None or self.config.two_day_mode:
                return self.dt == current_dt and self.run_status == AppRunRecord.STATUS_SUCCESS
            return False

        # 兼容未携带 full_completed / prepare_only_pending 的旧版成功记录
        if self.run_status == AppRunRecord.STATUS_SUCCESS:
            return True

        return False

    @property
    def is_done(self) -> bool:
        """一条龙运行判断：当前无需继续执行时返回 True 并跳过本次运行。"""
        return self.is_finished_by_period

    @property
    def run_status_under_now(self) -> int:
        """基于当前时间、双周交替刷新与分天状态显示的运行状态。"""
        if self.is_finished_by_period:
            return AppRunRecord.STATUS_SUCCESS
        if self._should_reset_by_dt():
            return AppRunRecord.STATUS_WAIT
        if self.dt != self.get_current_dt():
            return AppRunRecord.STATUS_WAIT
        return self.run_status

    def update_status(self, new_status: int, only_status: bool = False) -> None:
        """更新运行状态并同步记录所属的危局 14 天周期起始日。"""
        self.run_status = new_status
        self.update('run_status', self.run_status, False)
        if not only_status:
            self.dt = self.get_current_dt()
            self.run_time = self.app_record_now_time_str()
            self.run_time_float = time.time()
            self.update('dt', self.dt, False)
            self.update('run_time', self.run_time, False)
            self.update('run_time_float', self.run_time_float, False)
            self.update('period_start_dt', self.get_period_start_dt(self.dt), False)
        self.save()

    def reset_record(self) -> None:
        """重置危局运行记录为待运行，并清空本周期槽位、领奖与分天预处理进度。"""
        AppRunRecord.reset_record(self)
        self.update('finished_slots', [], False)
        self.update('rewards_claimed', False, False)
        self.update('prepare_only_pending', False, False)
        self.update('full_completed', False, False)
        self.update('prepare_dt', '', False)
        self.update('prepare_period_start_dt', '', False)
        self.update('period_start_dt', self.get_period_start_dt(self.get_current_dt()), False)
        self.save()

    def check_and_update_status(self) -> None:
        """检查并刷新运行状态：仅在进入新的危局周五刷新周期后彻底重置。"""
        current_dt = self.get_current_dt()
        if self._should_reset_by_dt():
            log.info(
                '检测到危局强袭战进入新周期（上一周期=%s，当前周期=%s，下期刷新=%s），重置运行记录',
                self.recorded_period_start_dt or '无',
                self.get_period_start_dt(current_dt),
                self.get_next_refresh_dt(current_dt),
            )
            self.reset_record()
        elif not self.is_finished_by_period and self.dt != current_dt:
            # 同一 14 天周期内：第 1 天预处理完成等待第 2 天战斗，或上次战斗中断待续，仅重置日状态并保留周期进度
            AppRunRecord.reset_record(self)
