from __future__ import annotations

import datetime
import os
import time
from typing import TYPE_CHECKING, ClassVar

from one_dragon.base.config import yaml_operator
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
    - 一条龙运行时根据 `check_and_update_status()` 与 `is_done` 判断：
      同一 14 天周期内已完成（`STATUS_SUCCESS` 或 3 个首领均已完成且已领奖）则直接跳过，
      进入下一个危局刷新日（两周后的周五 04:00）后自动重置状态并执行。
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

    def _should_reset_by_dt(self) -> bool:
        """判断是否已跨入新的危局 14 天刷新周期。"""
        current_dt = self.get_current_dt()
        recorded_period = self.recorded_period_start_dt
        if not recorded_period:
            return True
        return recorded_period != self.get_period_start_dt(current_dt)

    def _is_sibling_finished_for_period(self, sibling_app_id: str, target_period: str) -> bool:
        """检查同账号下危局系列其他模式在当前 14 天周期内是否已通关。"""
        if self.file_path is None:
            return False
        sibling_path = os.path.join(os.path.dirname(self.file_path), f'{sibling_app_id}.yml')
        if not os.path.exists(sibling_path):
            return False
        try:
            data = yaml_operator.read_cache_or_load(sibling_path)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False

        sib_period = data.get('period_start_dt', '')
        if not self._is_valid_dt(sib_period):
            sib_dt = data.get('dt', '')
            if not self._is_valid_dt(sib_dt):
                return False
            sib_period = self.get_period_start_dt(sib_dt)

        if sib_period != target_period:
            return False

        if data.get('run_status') == AppRunRecord.STATUS_SUCCESS:
            return True

        raw_slots = data.get('finished_slots', [])
        if isinstance(raw_slots, list):
            valid_slots = {
                int(x) for x in raw_slots
                if isinstance(x, int) and not isinstance(x, bool) and 0 <= x < self.TOTAL_SLOTS
            }
            if len(valid_slots) >= self.TOTAL_SLOTS:
                need_claim = self.config.claim_rewards if self.config is not None else True
                if not need_claim or bool(data.get('rewards_claimed', False)):
                    return True
        return False

    @property
    def is_finished_by_period(self) -> bool:
        """判断当前 14 天危局周期是否已全部完成（含本应用与战斗模式共享结果）。"""
        current_period = self.get_period_start_dt(self.get_current_dt())
        if not self._should_reset_by_dt():
            if self.run_status == AppRunRecord.STATUS_SUCCESS:
                return True
            if self.next_pending_slot_idx() is None:
                if self.config is None or not self.config.claim_rewards or self.rewards_claimed:
                    return True

        return self._is_sibling_finished_for_period('deadly_assault_battle', current_period)

    @property
    def is_done(self) -> bool:
        """一条龙运行判断：当前危局周期已完成时返回 True 并跳过本次运行。"""
        return self.is_finished_by_period

    @property
    def run_status_under_now(self) -> int:
        """基于当前时间与双周交替刷新规则显示的运行状态。"""
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
        """重置危局运行记录为待运行，并清空本周期槽位与领奖进度。"""
        AppRunRecord.reset_record(self)
        self.update('finished_slots', [], False)
        self.update('rewards_claimed', False, False)
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
            # 同一 14 天周期内上一天失败或中断，次日仅重置运行状态以便重试，保留已完成的首领槽位进度
            AppRunRecord.reset_record(self)
