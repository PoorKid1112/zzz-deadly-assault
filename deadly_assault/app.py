from __future__ import annotations

import os
import time
from typing import ClassVar

import cv2
from cv2.typing import MatLike
import numpy as np

from one_dragon.base.geometry.point import Point
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_notify import NotifyTiming, node_notify
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld
from zzz_od.operation.deploy import Deploy

from . import const
from .config import DeadlyAssaultConfig
from .screens import ensure_screens


class DeadlyAssaultApp(ZApplication):
    """危局强袭战自动挑战应用。

    完整支持：
    1. 从大世界/菜单进入快捷手册-作战-危局强袭战（试炼模式）
    2. 智能推荐配队：打开配队指引、智能推荐、生成更多并等待约5分钟、应用全部阵容（若有多个优先最下方）、确认阵容覆盖
    3. 依次挑战3个首领：点击EMPTY关卡槽（越往左优先度越高，依次左、中、右）
    4. 进战逻辑：点击下一步、出战确认、进战后按住W前进5秒并启动自动战斗
    5. 战斗结算：出现退出按钮时结束战斗并退出
    6. 荣誉酬赏：3关全部完成后，点击荣誉酬赏上方图标、全部领取、确认、返回
    """

    STATUS_NEED_TEAM_GUIDE: ClassVar[str] = '需要配队'
    STATUS_SKIP_TEAM_GUIDE: ClassVar[str] = '跳过配队'
    STATUS_BATTLE_TARGET: ClassVar[str] = '挑战首领'
    STATUS_ALL_BOSSES_DONE: ClassVar[str] = '全部首领完成'
    STATUS_BATTLE_END: ClassVar[str] = '战斗结束'
    STATUS_CLAIM_REWARD: ClassVar[str] = '领取奖励'
    STATUS_SKIP_REWARD: ClassVar[str] = '跳过领奖'

    SLOT_NAMES: ClassVar[list[str]] = ['槽位-左', '槽位-中', '槽位-右']

    def __init__(self, ctx: ZContext, config: DeadlyAssaultConfig | None = None) -> None:
        super().__init__(
            ctx=ctx,
            app_id=const.APP_ID,
            op_name=const.APP_NAME,
        )
        self.config: DeadlyAssaultConfig = config or DeadlyAssaultConfig(
            instance_idx=self.ctx.current_instance_idx,
            group_id=application_const.DEFAULT_GROUP_ID,
        )

        ensure_screens(self.ctx.screen_loader)

        self.boss_cycle: int = 0             # 0: 左侧, 1: 中间, 2: 右侧, 3: 完成
        self.current_target_slot: int = -1    # 当前选中的槽位下标
        self.generate_start_time: float = 0.0 # 生成配队开始时间戳
        self.initial_apply_count: int = 0     # 点击生成更多前全部应用按钮数量
        self.initial_apply_ys: list[int] = [] # 点击生成更多前全部应用按钮的y坐标
        self.battle_start_time: float = 0.0   # 战斗开始时间戳

    def save_screenshot(self, prefix: str | None = None) -> str:
        """保存运行调试/错误截图至插件专属数据目录 plugin_data/deadly_assault，避免污染主仓库。"""
        if self.last_screenshot is None:
            return ''
        if prefix is None:
            prefix = self.__class__.__name__

        data_dir = self.ctx.get_plugin_data_dir(const.APP_ID)
        os.makedirs(data_dir, exist_ok=True)
        file_name = f"{prefix}_{round(time.time() * 1000)}"
        file_path = os.path.join(data_dir, f"{file_name}.png")

        bgr_image = cv2.cvtColor(self.last_screenshot, cv2.COLOR_RGB2BGR)
        cv2.imwrite(file_path, bgr_image)
        log.info('插件运行截图已保存至: %s', file_path)
        return file_path

    def is_in_deadly_assault_main(self, screen: MatLike) -> bool:
        """判断是否处于危局强袭战主界面。"""
        if screen is None:
            return False
        result = self.round_by_find_area(screen, '危局强袭战-主界面', '选定敌方')
        if result.is_success:
            return True
        results = self.ctx.ocr.match_words(
            screen,
            ['选定敌方', '配队指引', '重置全部关卡', '荣誉酬赏'],
            threshold=0.6,
        )
        return len(results) >= 2

    # ---------------- 阶段 1: 入场导航 (Step 1 - Step 5) ----------------

    @operation_node(name='开始', is_start_node=True)
    def start_node(self) -> OperationRoundResult:
        """检查初始状态，如果在主界面直接进入下一步，否则返回大世界。"""
        self.boss_cycle = 0
        self.current_target_slot = -1

        if self.is_in_deadly_assault_main(self.last_screenshot):
            log.info('当前已在危局强袭战主界面')
            return self.round_success('已在危局强袭战主界面')

        # 检查是否已在快捷手册
        res = self.round_by_find_area(self.last_screenshot, '快捷手册', 'TAB列表')
        if res.is_success:
            log.info('当前已在快捷手册')
            return self.round_success('已在快捷手册')

        log.info('返回大世界以开始危局强袭战导航')
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True)
        return self.round_by_op_result(op.execute(), status='已返回大世界')

    @node_from(from_name='开始', status='已返回大世界')
    @node_from(from_name='开始', status='已在快捷手册')
    @operation_node(name='进入快捷手册-作战')
    def goto_compendium(self) -> OperationRoundResult:
        """导航到快捷手册-作战。"""
        return self.round_by_goto_screen(screen_name='快捷手册-作战', success_wait=2.0, retry_wait=1.5)

    @node_from(from_name='进入快捷手册-作战')
    @operation_node(name='选择危局强袭战', node_max_retry_times=5)
    def choose_deadly_assault_tab(self) -> OperationRoundResult:
        """在快捷手册-作战左侧列表中点击危局强袭战 (Step 3)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-模式选择', '列表-危局强袭战')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='危局强袭战',
            area=area,
            crop_first=False,
            success_wait=2.0,
            retry_wait=1.5,
        )

    @node_from(from_name='选择危局强袭战')
    @operation_node(name='点击试炼前往', node_max_retry_times=5)
    def enter_trial(self) -> OperationRoundResult:
        """在危局强袭战卡片中点击试炼所在框的前往 (Step 4)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-模式选择', '试炼-前往')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='前往',
            area=area,
            crop_first=False,
            success_wait=2.0,
            retry_wait=1.5,
        )

    @node_from(from_name='点击试炼前往')
    @operation_node(name='确认传送', node_max_retry_times=5)
    def confirm_tp(self) -> OperationRoundResult:
        """点击传送确认弹窗中的确认按钮 (Step 5)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-模式选择', '传送-确认')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='确认',
            area=area,
            crop_first=False,
            success_wait=2.5,
            retry_wait=1.5,
        )

    @node_from(from_name='确认传送')
    @node_from(from_name='开始', status='已在危局强袭战主界面')
    @operation_node(name='等待主界面加载')
    def wait_main_screen(self) -> OperationRoundResult:
        """等待危局强袭战主界面加载完毕 (Step 6)。"""
        res = self.round_by_find_area(self.last_screenshot, '危局强袭战-主界面', '选定敌方')
        if res.is_success:
            if self.config.auto_team:
                return self.round_success(self.STATUS_NEED_TEAM_GUIDE)
            return self.round_success(self.STATUS_SKIP_TEAM_GUIDE)

        return self.round_wait('等待危局强袭战主界面加载', wait=1.5)

    # ---------------- 阶段 2: 智能推荐配队 (Step 6 - Step 12) ----------------

    @node_from(from_name='等待主界面加载', status=STATUS_NEED_TEAM_GUIDE)
    @operation_node(name='打开配队指引', node_max_retry_times=5)
    def open_team_guide(self) -> OperationRoundResult:
        """点击主界面右下方配队指引 (Step 6)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-主界面', '配队指引')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='配队指引',
            area=area,
            crop_first=False,
            success_wait=2.0,
            retry_wait=1.5,
        )

    @node_from(from_name='打开配队指引')
    @operation_node(name='检查规则说明弹窗')
    def check_rule_dialog(self) -> OperationRoundResult:
        """检查并关闭可能出现的规则说明弹窗 (Step 7)。"""
        find_res = self.round_by_find_area(self.last_screenshot, '危局强袭战-配队指引', '规则说明')
        words = self.ctx.ocr.match_words(self.last_screenshot, ['规则说明'], threshold=0.6)
        if find_res.is_success or '规则说明' in words:
            log.info('检测到规则说明弹窗，点击右上角叉号关闭')
            click_res = self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-配队指引',
                '规则说明-关闭',
                success_wait=1.8,
            )
            if not click_res.is_success:
                self.round_by_click_area('危局强袭战-配队指引', '规则说明-关闭', success_wait=1.8)
        else:
            log.info('未出现规则说明弹窗，直接进入下一步')
        return self.round_success()

    @node_from(from_name='检查规则说明弹窗')
    @operation_node(name='点击智能推荐', node_max_retry_times=5)
    def click_ai_recommend(self) -> OperationRoundResult:
        """点击智能推荐选项卡 (Step 8)。"""
        # 容错与兜底：若规则说明弹窗仍未关闭，先点击关闭
        words = self.ctx.ocr.match_words(self.last_screenshot, ['规则说明'], threshold=0.6)
        if '规则说明' in words:
            log.info('点击智能推荐前检测到规则说明弹窗仍存在，先点击关闭')
            self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-配队指引',
                '规则说明-关闭',
                success_wait=1.8,
            )
            time.sleep(1.0)

        area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '智能推荐')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='智能推荐',
            area=area,
            crop_first=False,
            success_wait=1.5,
            retry_wait=1.2,
        )

    @node_from(from_name='点击智能推荐')
    @operation_node(name='点击生成更多', node_max_retry_times=5)
    def click_generate_more(self) -> OperationRoundResult:
        """点击生成更多 (Step 9)。"""
        apply_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '全部应用-区域')
        crop = self.last_screenshot[apply_area.rect.y1:apply_area.rect.y2, apply_area.rect.x1:apply_area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        apply_buttons = [r for r in ocr_res if '全部' in r.data and '应用' in r.data]
        self.initial_apply_count = len(apply_buttons)
        self.initial_apply_ys = [apply_area.rect.y1 + r.rect.center.y for r in apply_buttons]
        log.info('生成前全部应用按钮数量=%d, 纵坐标=%s', self.initial_apply_count, self.initial_apply_ys)

        area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '生成更多')
        res = self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='生成更多',
            area=area,
            crop_first=False,
            success_wait=1.8,
            retry_wait=1.5,
        )

        self.generate_start_time = time.time()
        log.info('已触发生成更多，进入生成等待（最大 %d 秒）', self.config.wait_generate_seconds)
        return self.round_success()

    @node_from(from_name='点击生成更多')
    @operation_node(name='等待生成配队')
    def wait_generation(self) -> OperationRoundResult:
        """等待配队验算生成约5分钟 (Step 9)。

        逻辑说明：
        1. 至少缓冲等待 5 秒防抖，避免因网络短暂波动或刚点击的过渡帧造成误判。
        2. 若配置等待时间为 0，直接跳过等待。
        3. 若初始进入时界面上已有 >= 2 组推荐阵容（说明当前已有完整的新老生成阵容），在缓冲 5 秒后直接应用最新最下方阵容。
        4. 若初始仅有 1 组或 0 组阵容：
           持续轮询检测：当新卡片生成（“全部应用”按钮数增多，或下方 y > 750 出现新按钮）时，判定生成完毕，进入应用。
        5. 若达到配置的最大等待时间（默认 300 秒/5分钟），超时保护触发，应用当前画面上的最优阵容。
        """
        elapsed = time.time() - self.generate_start_time

        if self.config.wait_generate_seconds <= 0:
            log.info('用户配置等待时间为 0 秒，跳过等待')
            return self.round_success()

        if elapsed < 5.0:
            return self.round_wait(f'等待配队生成中 (缓冲 {elapsed:.1f}s/5s)', wait=2.0)

        apply_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '全部应用-区域')
        crop = self.last_screenshot[apply_area.rect.y1:apply_area.rect.y2, apply_area.rect.x1:apply_area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        current_buttons = [r for r in ocr_res if '全部' in r.data and '应用' in r.data]
        current_count = len(current_buttons)

        if self.initial_apply_count >= 2:
            log.info('界面上已包含多组推荐配队（共 %d 个全部应用），无需继续等待，直接应用', current_count)
            return self.round_success()

        has_new_card = False
        if current_count > self.initial_apply_count:
            has_new_card = True
        else:
            for r in current_buttons:
                abs_y = apply_area.rect.y1 + r.rect.center.y
                if abs_y > 750 and not any(abs(abs_y - y0) < 50 for y0 in self.initial_apply_ys):
                    has_new_card = True
                    break

        if has_new_card:
            log.info('检测到新生成的配队（已等待 %.1f 秒，按钮数: %d -> %d）',
                     elapsed, self.initial_apply_count, current_count)
            return self.round_success()

        if elapsed >= self.config.wait_generate_seconds:
            log.info('已达到最大等待时间 %d 秒，继续执行后续应用操作', self.config.wait_generate_seconds)
            return self.round_success()

        return self.round_wait(f'等待配队生成中 (已等待 {int(elapsed)}/{self.config.wait_generate_seconds}s)', wait=3.0)

    @node_from(from_name='等待生成配队')
    @operation_node(name='应用全部阵容', node_max_retry_times=5)
    def apply_all_teams(self) -> OperationRoundResult:
        """点击全部应用，若有多个则优先最下方 (Step 10)。"""
        apply_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '全部应用-区域')
        crop = self.last_screenshot[apply_area.rect.y1:apply_area.rect.y2, apply_area.rect.x1:apply_area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        apply_buttons = [r for r in ocr_res if '全部' in r.data and '应用' in r.data]

        if apply_buttons:
            apply_buttons.sort(key=lambda item: item.rect.center.y, reverse=True)
            chosen = apply_buttons[0]
            target_pt = Point(apply_area.rect.x1 + chosen.rect.center.x, apply_area.rect.y1 + chosen.rect.center.y)
            log.info('找到 %d 个全部应用按钮，点击最下方按钮 (%d, %d)',
                     len(apply_buttons), target_pt.x, target_pt.y)
            self.ctx.controller.click(target_pt)
            time.sleep(1.8)
            return self.round_success()

        log.info('未在区域内解析到全部应用文字，点击区域推荐位置')
        self.ctx.controller.click(Point(apply_area.center.x, apply_area.rect.y2 - 30))
        time.sleep(1.8)
        return self.round_success()

    @node_from(from_name='应用全部阵容')
    @operation_node(name='确认阵容覆盖')
    def confirm_cover_team(self) -> OperationRoundResult:
        """如果存在阵容覆盖确认对话框，点击确认 (Step 11)。"""
        find_res = self.round_by_find_area(self.last_screenshot, '危局强袭战-配队指引', '阵容覆盖')
        words = self.ctx.ocr.match_words(self.last_screenshot, ['阵容覆盖', '是否确认'], threshold=0.6)
        if find_res.is_success or '阵容覆盖' in words or '是否确认' in words:
            log.info('检测到阵容覆盖确认弹窗，点击确认')
            click_res = self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-配队指引',
                '阵容覆盖-确认',
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
            if not click_res.is_success:
                self.round_by_click_area('危局强袭战-配队指引', '阵容覆盖-确认', success_wait=2.0)
        else:
            log.info('未出现阵容覆盖确认弹窗，直接进入下一步')
        return self.round_success()

    @node_from(from_name='确认阵容覆盖')
    @operation_node(name='返回危局主界面', node_max_retry_times=5)
    def back_from_team_guide(self) -> OperationRoundResult:
        """点击左上角返回按钮回到危局强袭战主界面 (Step 12)。

        遵循规范：优先复用框架通用“画面-通用”的“返回”区域（模板匹配 menu/back，标准坐标 [82, 13, 150, 90]，中心 116, 51）。
        """
        # 容错与兜底：若阵容覆盖弹窗仍未确认，先点击确认
        words = self.ctx.ocr.match_words(self.last_screenshot, ['阵容覆盖', '是否确认'], threshold=0.6)
        if '阵容覆盖' in words or '是否确认' in words:
            log.info('返回主界面前检测到阵容覆盖确认弹窗仍存在，先点击确认')
            self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-配队指引',
                '阵容覆盖-确认',
                crop_first=False,
                success_wait=2.0,
            )
            time.sleep(1.0)

        res = self.round_by_find_and_click_area(
            self.last_screenshot,
            '画面-通用',
            '返回',
            success_wait=2.0,
            retry_wait=1.5,
        )
        if res.is_success:
            return self.round_success()

        # 兜底使用配队指引中配置的返回区域
        return self.round_by_click_area('危局强袭战-配队指引', '返回', success_wait=2.0, retry_wait=1.5)

    # ---------------- 阶段 3: 循环挑战 3 个首领 (Step 13 - Step 25) ----------------

    @node_from(from_name='返回危局主界面')
    @node_from(from_name='等待主界面加载', status=STATUS_SKIP_TEAM_GUIDE)
    @node_from(from_name='战斗后等待主界面')
    @operation_node(name='选择首领关卡', node_max_retry_times=5)
    def choose_boss(self) -> OperationRoundResult:
        """选择首领关卡 (Step 13, 17, 21)。

        规则：
        - 第 1 轮 (Step 13): 下方灰色的 3 个 empty，越往左优先度越高 -> 点击左侧槽位
        - 第 2 轮 (Step 17): 点击中间的 empty -> 点击中间槽位
        - 第 3 轮 (Step 21): 点击左右侧剩余 empty -> 点击右侧槽位
        - 全部挑战完毕后进入荣誉酬赏领取阶段
        """
        if self.boss_cycle >= 3:
            log.info('3 轮关卡已全部挑战完毕，进入领奖流程')
            return self.round_success(self.STATUS_ALL_BOSSES_DONE)

        empty_states: list[bool] = []
        for name in self.SLOT_NAMES:
            area = self.ctx.screen_loader.get_area('危局强袭战-主界面', name)
            crop = self.last_screenshot[area.rect.y1:area.rect.y2, area.rect.x1:area.rect.x2]
            ocr_res = self.ctx.ocr.ocr(crop)
            is_empty = any('EMPTY' in r.data.upper() or '0' in r.data for r in ocr_res)
            empty_states.append(is_empty)

        log.info('当前 3 个槽位状态: 左=%s, 中=%s, 右=%s (当前第 %d 轮)',
                 'EMPTY' if empty_states[0] else 'DONE',
                 'EMPTY' if empty_states[1] else 'DONE',
                 'EMPTY' if empty_states[2] else 'DONE',
                 self.boss_cycle + 1)

        target_slot = self.boss_cycle
        if not empty_states[target_slot] and any(empty_states):
            target_slot = empty_states.index(True)
        elif not any(empty_states) and self.boss_cycle > 0:
            log.info('画面上已无未完成的 EMPTY 关卡，提前结束挑战')
            return self.round_success(self.STATUS_ALL_BOSSES_DONE)

        self.current_target_slot = target_slot
        chosen_slot_name = self.SLOT_NAMES[target_slot]
        log.info('点击关卡槽位: %s', chosen_slot_name)
        self.round_by_click_area('危局强袭战-主界面', chosen_slot_name, success_wait=2.0)

        self.boss_cycle += 1
        return self.round_success(self.STATUS_BATTLE_TARGET)

    @node_from(from_name='选择首领关卡', status=STATUS_BATTLE_TARGET)
    @operation_node(name='点击选定敌方', node_max_retry_times=5)
    def click_select_enemy(self) -> OperationRoundResult:
        """在危局主界面右下角点击选定敌方 (Step 13/17/21 点击 EMPTY 槽位后，进入 Step 14/18/22 关卡详情)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-主界面', '选定敌方')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='选定敌方',
            area=area,
            crop_first=False,
            success_wait=2.0,
            retry_wait=1.5,
        )

    @node_from(from_name='点击选定敌方')
    @operation_node(name='点击下一步', node_max_retry_times=5)
    def click_next(self) -> OperationRoundResult:
        """在关卡详情页面点击下一步或开始挑战 (Step 14, 18, 22)。

        不同账号或关卡完成状态下，右下角按钮文本可能为'下一步'或'开始挑战'，OR 分支满足其一即点击。
        """
        area = self.ctx.screen_loader.get_area('危局强袭战-关卡详情', '下一步')
        res = self.round_by_ocr_and_click_by_priority(
            target_cn_list=['下一步', '开始挑战', '挑战'],
            screen=self.last_screenshot,
            area=area,
            crop_first=False,
            success_wait=2.0,
            retry_wait=1.5,
        )
        if res.is_success:
            log.info('成功点击右下角进战按钮: %s', res.status)
            return self.round_success()

        # 备选：全屏匹配
        words = self.ctx.ocr.match_words(self.last_screenshot, ['下一步', '开始挑战', '挑战'], threshold=0.6)
        for k in ['下一步', '开始挑战', '挑战']:
            if k in words:
                log.info('全屏 OCR 找到 %s，点击目标位置', k)
                return self.round_by_ocr_and_click(
                    self.last_screenshot,
                    target_cn=k,
                    crop_first=False,
                    success_wait=2.0,
                    retry_wait=1.5,
                )

        # 兜底直接点击右下角按钮坐标 (1716, 1028)
        log.info('未识别到下一步或开始挑战文字，点击右下角按钮兜底坐标 (1716, 1028)')
        self.ctx.controller.click(Point(1716, 1028))
        time.sleep(2.0)
        return self.round_success()

    @node_from(from_name='点击下一步')
    @operation_node(name='检查锁定出战弹窗')
    def check_lock_deploy_dialog(self) -> OperationRoundResult:
        """检查并确认可能出现的出战锁定提示弹窗 (Step 14 之后，'是否确认出战？')。

        本弹窗为条件出现（并非一定出现）：若弹出，点击右侧'确认'继续；若未弹出，直接进入后续阶段。
        """
        find_res = self.round_by_find_area(self.last_screenshot, '危局强袭战-关卡详情', '锁定确认-标题')
        words = self.ctx.ocr.match_words(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定'], threshold=0.6)
        if find_res.is_success or any(k in words for k in ['是否确认出战', '确认出战', '将被锁定']):
            log.info('检测到锁定出战提示弹窗（是否确认出战？），点击确认')
            click_res = self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-关卡详情',
                '锁定确认-确认',
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
            if not click_res.is_success:
                self.round_by_click_area('危局强袭战-关卡详情', '锁定确认-确认', success_wait=2.0)
        else:
            log.info('未出现锁定出战确认弹窗，直接进入出战阶段')

        return self.round_success()

    @node_from(from_name='检查锁定出战弹窗')
    @operation_node(name='出战确认', node_max_retry_times=5)
    def check_deploy(self) -> OperationRoundResult:
        """如果在通用出战界面，点击出战并确认。"""
        # 容错兜底：若前一步因网络或动画延迟，弹窗在此刻才渲染出来，先点击确认
        words = self.ctx.ocr.match_words(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定'], threshold=0.6)
        if any(k in words for k in ['是否确认出战', '确认出战', '将被锁定']):
            log.info('出战确认节点检测到残留的锁定出战弹窗，点击确认')
            self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-关卡详情',
                '锁定确认-确认',
                crop_first=False,
                success_wait=2.0,
            )
            time.sleep(1.0)

        res = self.round_by_find_area(self.last_screenshot, '通用-出战', '按钮-出战')
        if res.is_success:
            log.info('检测到通用出战按钮，执行 Deploy 操作')
            op = Deploy(self.ctx)
            op.execute()
            time.sleep(2.0)
            return self.round_success()

        ocr_results = self.ctx.ocr.match_words(self.last_screenshot, ['出战'], threshold=0.6)
        if '出战' in ocr_results:
            log.info('OCR 识别到出战文字，点击出战')
            self.round_by_ocr_and_click(self.last_screenshot, target_cn='出战', success_wait=2.0)

        return self.round_success()

    @node_from(from_name='出战确认')
    @operation_node(name='等待战斗画面')
    def wait_battle_screen(self) -> OperationRoundResult:
        """等待战斗画面加载完毕 (Step 15, 19, 23)。"""
        # 容错兜底：等待战斗期间若出现锁定弹窗阻挡，点击确认
        words = self.ctx.ocr.match_words(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定'], threshold=0.6)
        if any(k in words for k in ['是否确认出战', '确认出战', '将被锁定']):
            log.info('等待战斗画面时检测到锁定出战弹窗，点击确认')
            self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-关卡详情',
                '锁定确认-确认',
                crop_first=False,
                success_wait=2.0,
            )
            return self.round_wait('已点击锁定出战确认，等待战斗画面加载', wait=1.5)

        if self.ctx.auto_battle_context.is_normal_attack_btn_available(self.last_screenshot):
            log.info('战斗画面已就绪 (普攻按键可用)')
            return self.round_success()

        res = self.round_by_find_area(self.last_screenshot, '战斗画面', '按键-普通攻击')
        if res.is_success:
            log.info('战斗画面已就绪 (模板匹配成功)')
            return self.round_success()

        return self.round_wait('等待战斗画面加载', wait=1.5)

    def check_boss_hp_bar_appear(self, screen: MatLike | None) -> tuple[bool, str]:
        """检测右上角是否出现 Boss 血条。

        检测条件（满足其一即生效）：
        1. 一大片绿色：右上角 Boss 血条区域检测大片荧光绿（HSV 或 RGB 绿色像素达到阈值且形成条状）
        2. 数字 29：右上角区域 OCR 识别到 '29'
        """
        if screen is None:
            return False, ''

        h, w = screen.shape[:2]
        if w != 1920 or h != 1080:
            screen_1080 = cv2.resize(screen, (1920, 1080))
        else:
            screen_1080 = screen

        # 1. 颜色检测：右上角 Boss 血条区域 [y: 35~95, x: 1100~1750]
        # Boss 出现前为 0 绿色像素，Boss 出现后包含 7000+ 荧光绿像素
        roi_rgb = screen_1080[35:95, 1100:1750]
        roi_hsv = cv2.cvtColor(roi_rgb, cv2.COLOR_RGB2HSV)
        lower_green = np.array([35, 100, 100], dtype=np.uint8)
        upper_green = np.array([85, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(roi_hsv, lower_green, upper_green)
        green_count = cv2.countNonZero(mask)

        # 辅以 RGB 显式绿色通道判断
        r = roi_rgb[:, :, 0].astype(int)
        g = roi_rgb[:, :, 1].astype(int)
        b = roi_rgb[:, :, 2].astype(int)
        direct_green = int(np.sum((g > 150) & (g > r + 30) & (g > b + 30)))

        if green_count >= 1500 or direct_green >= 1500:
            return True, f'一大片绿色 (像素数={max(green_count, direct_green)})'

        # 2. 数字 29 检测：右上角区域 [y: 20~130, x: 1000~1850]
        roi_ocr = screen_1080[20:130, 1000:1850]
        ocr_res = self.ctx.ocr.ocr(roi_ocr)
        for r_item in ocr_res:
            text = r_item.data.strip()
            if '29' in text:
                return True, f'数字29 (OCR识别: {text})'

        return False, ''

    @node_from(from_name='等待战斗画面')
    @operation_node(name='向前走并启动战斗')
    def walk_forward_and_start_battle(self) -> OperationRoundResult:
        """按住 W 向前移动，同时动态检测右上角 Boss 血条（一大片绿色或数字 29）。

        一旦检测到 Boss 血条出现（或达到最大行进时间），立即停止 W 并启动自动战斗 (Step 15, 19, 23)。
        """
        max_walk_sec = max(0.5, self.config.walk_duration)
        log.info('开始按住 W 键向前移动，同时检测右上角 Boss 血条（一大片绿色/数字29，最大持续 %.1f 秒）', max_walk_sec)

        start_time = time.time()
        stopped_reason = ''
        self.ctx.controller.move_w(press=True)

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed >= max_walk_sec:
                    stopped_reason = f'达到最大移动时间 ({max_walk_sec:.1f}s)'
                    break

                screen = self.screenshot()
                detected, reason = self.check_boss_hp_bar_appear(screen)
                if detected:
                    stopped_reason = f'检测到 Boss 血条 ({reason}, 移动耗时 {elapsed:.2f}s)'
                    break

                time.sleep(0.08)
        finally:
            self.ctx.controller.move_w(release=True)
            log.info('向前移动已结束：%s，松开 W 键', stopped_reason)

        time.sleep(0.3)

        auto_op_name = self.config.auto_battle_config or self.ctx.battle_assistant_config.auto_battle_config or '全配队通用'
        log.info('启动自动战斗策略: %s', auto_op_name)
        self.ctx.auto_battle_context.init_auto_op(sub_dir='auto_battle', op_name=auto_op_name)
        self.ctx.auto_battle_context.start_auto_battle()

        self.battle_start_time = time.time()
        return self.round_success()

    @node_from(from_name='向前走并启动战斗')
    @operation_node(name='自动战斗中', mute=True)
    def auto_battle_loop(self) -> OperationRoundResult:
        """自动战斗循环，直至结算退出按钮出现 (Step 15 -> Step 16)。"""
        exit_area = self.ctx.screen_loader.get_area('战斗画面', '战斗结果-退出')
        if exit_area is not None:
            res = self.round_by_find_area(self.last_screenshot, '战斗画面', '战斗结果-退出')
            if res.is_success:
                log.info('检测到结算退出按钮，停止自动战斗')
                self.ctx.auto_battle_context.stop_auto_battle()
                return self.round_success(self.STATUS_BATTLE_END)

        ocr_results = self.ctx.ocr.match_words(self.last_screenshot, ['退出', '挑战结果', '再来一次'], threshold=0.6)
        if '退出' in ocr_results or '挑战结果' in ocr_results or '再来一次' in ocr_results:
            log.info('OCR 识别到结算画面 (%s)，停止自动战斗', list(ocr_results.keys()))
            self.ctx.auto_battle_context.stop_auto_battle()
            return self.round_success(self.STATUS_BATTLE_END)

        if time.time() - self.battle_start_time > 600:
            log.warning('战斗超时 (600s)，强制停止战斗')
            self.ctx.auto_battle_context.stop_auto_battle()
            return self.round_fail('战斗超时')

        self.ctx.auto_battle_context.check_battle_state(self.last_screenshot, self.last_screenshot_time)
        interval = self.ctx.battle_assistant_config.screenshot_interval or 0.05
        return self.round_wait(wait=interval)

    @node_from(from_name='自动战斗中', status=STATUS_BATTLE_END)
    @operation_node(name='点击退出结算', node_max_retry_times=5)
    def click_exit_settlement(self) -> OperationRoundResult:
        """点击结算画面的退出按钮 (Step 16, 20, 24)。"""
        res = self.round_by_find_and_click_area(
            self.last_screenshot,
            '战斗画面',
            '战斗结果-退出',
            success_wait=2.5,
            retry_wait=1.5,
        )
        if res.is_success:
            return self.round_success()

        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='退出',
            success_wait=2.5,
            retry_wait=1.5,
        )

    @node_from(from_name='点击退出结算')
    @operation_node(name='战斗后等待主界面')
    def wait_after_battle(self) -> OperationRoundResult:
        """战斗结束后等待返回危局强袭战主界面 (Step 25)。"""
        res = self.round_by_find_area(self.last_screenshot, '危局强袭战-主界面', '选定敌方')
        if res.is_success:
            log.info('已成功返回危局强袭战主界面')
            return self.round_success()

        return self.round_wait('等待返回主界面', wait=1.5)

    # ---------------- 阶段 4: 荣誉酬赏与完成 (Step 26 - Step 29) ----------------

    @node_from(from_name='选择首领关卡', status=STATUS_ALL_BOSSES_DONE)
    @operation_node(name='点击荣誉酬赏', node_max_retry_times=5)
    def click_reward_icon(self) -> OperationRoundResult:
        """点击左下角荣誉酬赏图标（文字上方约10像素）(Step 26)。"""
        if not self.config.claim_rewards:
            log.info('配置设置为不领取荣誉酬赏，跳过')
            return self.round_success(self.STATUS_SKIP_REWARD)

        res = self.round_by_click_area(
            '危局强袭战-主界面',
            '荣誉酬赏-图标',
            success_wait=2.0,
            retry_wait=1.5,
        )
        if res.is_success:
            return self.round_success(self.STATUS_CLAIM_REWARD)

        return self.round_retry('未能成功点击荣誉酬赏图标', wait=1.5)

    @node_from(from_name='点击荣誉酬赏', status=STATUS_CLAIM_REWARD)
    @operation_node(name='点击全部领取', node_max_retry_times=5)
    def click_claim_all(self) -> OperationRoundResult:
        """在挑战奖励窗口点击全部领取 (Step 27)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-荣誉酬赏', '全部领取')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='全部领取',
            area=area,
            crop_first=False,
            success_wait=1.8,
            retry_wait=1.5,
        )

    @node_from(from_name='点击全部领取')
    @operation_node(name='点击确认奖励', node_max_retry_times=5)
    def click_confirm_reward(self) -> OperationRoundResult:
        """点击奖励获得确认按钮 (Step 28)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-荣誉酬赏', '确认')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='确认',
            area=area,
            crop_first=False,
            success_wait=1.8,
            retry_wait=1.5,
        )

    @node_from(from_name='点击确认奖励')
    @node_from(from_name='点击荣誉酬赏', status=STATUS_SKIP_REWARD)
    @node_notify(when=NotifyTiming.CURRENT_DONE, detail=True)
    @operation_node(name='返回大世界并结束')
    def back_to_world_finish(self) -> OperationRoundResult:
        """点击左上角返回并返回大世界，结束流程 (Step 29)。"""
        self.round_by_find_and_click_area(
            self.last_screenshot,
            '画面-通用',
            '返回',
            success_wait=2.0,
        )

        op = BackToNormalWorld(self.ctx, ensure_normal_world=True)
        op.execute()

        log.info('危局强袭战全流程执行完成')
        return self.round_success('危局强袭战全部挑战及领奖完成')
