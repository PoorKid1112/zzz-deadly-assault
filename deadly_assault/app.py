from __future__ import annotations

import os
import re
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
from one_dragon.base.screen.screen_area import ScreenArea
from one_dragon.utils.log_utils import log
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld
from zzz_od.operation.deploy import Deploy

from . import const
from .config import DeadlyAssaultConfig
from .run_record import DeadlyAssaultRunRecord
from .screens import ensure_screens


class DeadlyAssaultApp(ZApplication):
    """危局强袭战自动挑战应用。

    完整支持：
    1. 从大世界/菜单进入快捷手册-作战-危局强袭战（试炼模式）
    2. 智能推荐配队：打开配队指引、智能推荐、生成更多并等待、应用全部阵容（若有多个优先最下方）、确认阵容覆盖
    3. 依次挑战3个首领：检测下方关卡槽位的 EMPTY 标识并精确点击（越往左优先度越高，依次左、中、右）
    4. 进战逻辑：点击选定敌方、点击下一步/开始挑战、出战确认、进战后按住W前进同时检测Boss血条并启动自动战斗
    5. 战斗结算：出现退出按钮时结束战斗并退出
    6. 荣誉酬赏：3关全部完成后，检测左下角荣誉酬赏并点击其上方图标、全部领取、确认、返回

    所有点击动作严格遵循规范：在目标区域检测到符合要求的特征/文本后再行点击，绝无盲点固定坐标操作。
    """

    STATUS_NEED_TEAM_GUIDE: ClassVar[str] = '需要配队'
    STATUS_SKIP_TEAM_GUIDE: ClassVar[str] = '跳过配队'
    STATUS_SKIP_GENERATE: ClassVar[str] = '跳过生成'
    STATUS_WAIT_GENERATE: ClassVar[str] = '等待生成'
    STATUS_PREPARE_ONLY_DONE: ClassVar[str] = '分天预处理完成'
    STATUS_BATTLE_TARGET: ClassVar[str] = '挑战首领'
    STATUS_ALL_BOSSES_DONE: ClassVar[str] = '全部首领完成'
    STATUS_BATTLE_END: ClassVar[str] = '战斗结束'
    STATUS_CLAIM_REWARD: ClassVar[str] = '领取奖励'
    STATUS_SKIP_REWARD: ClassVar[str] = '跳过领奖'

    SLOT_NAMES: ClassVar[list[str]] = ['槽位-左', '槽位-中', '槽位-右']

    def __init__(
        self,
        ctx: ZContext,
        config: DeadlyAssaultConfig | None = None,
        run_record: DeadlyAssaultRunRecord | None = None,
    ) -> None:
        super().__init__(
            ctx=ctx,
            app_id=const.APP_ID,
            op_name=const.APP_NAME,
            run_record=run_record,
        )
        self.config: DeadlyAssaultConfig = config or DeadlyAssaultConfig(
            instance_idx=self.ctx.current_instance_idx,
            group_id=application_const.DEFAULT_GROUP_ID,
        )
        self.run_record: DeadlyAssaultRunRecord | None

        ensure_screens(self.ctx.screen_loader)

        self.boss_cycle: int = 0             # 0: 左侧, 1: 中间, 2: 右侧, 3: 完成
        self.current_target_slot: int = -1    # 当前选中的槽位下标
        self.generate_start_time: float = 0.0 # 生成配队开始时间戳
        self.initial_apply_count: int = 0     # 点击生成更多前全部应用按钮数量
        self.initial_apply_ys: list[int] = [] # 点击生成更多前全部应用按钮的y坐标
        self.battle_start_time: float = 0.0   # 战斗开始时间戳
        self.is_prepare_only_run: bool = False  # 分天运行模式：第1天仅做预处理，第2天才完整战斗

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

    def is_in_deadly_assault_main(self, screen: MatLike | None) -> bool:
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

    def has_ocr_text(self, screen: MatLike | None, candidates: list[str]) -> bool:
        """检查截图中是否存在任一候选关键词（支持包含匹配，避免标点符号/空格导致字典键失配）。"""
        if screen is None:
            return False
        all_ocr = self.ctx.ocr.ocr(screen)
        for r in all_ocr:
            text = r.data.strip()
            if any(cand in text for cand in candidates):
                return True
        return False

    def click_red_feature_in_rect(
        self,
        screen: MatLike | None,
        rect: Rect,
        min_pts: int = 30,
        wait_sec: float = 1.8,
        label: str = '红色按钮',
    ) -> bool:
        """在指定区域内检测红色按键（如关闭叉号或左上角返回）并点击中心位置。"""
        if screen is None:
            return False
        h, w = screen.shape[:2]
        screen_1080 = cv2.resize(screen, (1920, 1080)) if (w, h) != (1920, 1080) else screen
        crop = screen_1080[rect.y1:rect.y2, rect.x1:rect.x2]
        r, g, b = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        red_pts = np.argwhere((r > 150) & (g < 70) & (b < 70))
        if len(red_pts) < min_pts:
            return False
        min_y, min_x = red_pts.min(axis=0)
        max_y, max_x = red_pts.max(axis=0)
        target = Point(rect.x1 + (min_x + max_x) // 2, rect.y1 + (min_y + max_y) // 2)
        log.info('特征检测到%s位置: (%d, %d)', label, target.x, target.y)
        self.ctx.controller.click(target)
        time.sleep(wait_sec)
        return True

    def scan_slot_states(
        self,
        screen: MatLike | None,
    ) -> tuple[list[bool], list[Point | None], list[bool]]:
        """扫描主界面下方左、中、右 3 个首领槽位的挑战状态。

        Returns:
            (empty_states, slot_empty_centers, scored_states):
            - empty_states: 各槽位是否为 EMPTY（待挑战）
            - slot_empty_centers: 各待挑战槽位 EMPTY 文字的精确中心坐标
            - scored_states: 各槽位是否已存在挑战分数（非 EMPTY 且检测到有效数字得分）
        """
        empty_states: list[bool] = []
        slot_empty_centers: list[Point | None] = []
        scored_states: list[bool] = []

        if screen is None:
            return [False, False, False], [None, None, None], [False, False, False]

        h, w = screen.shape[:2]
        screen_1080 = cv2.resize(screen, (1920, 1080)) if (w, h) != (1920, 1080) else screen

        for name in self.SLOT_NAMES:
            area = self.ctx.screen_loader.get_area('危局强袭战-主界面', name)
            crop = screen_1080[area.rect.y1:area.rect.y2, area.rect.x1:area.rect.x2]
            ocr_res = self.ctx.ocr.ocr(crop)

            empty_items = [
                r for r in ocr_res
                if 'EMPTY' in r.data.upper() or 'EMPT' in r.data.upper()
            ]
            # 得分数字位于槽位右半区域（局部 x >= 45）且通常为 3 位以上数字，避免邦布图标边缘噪点 '0' 干扰
            score_items = [
                r for r in ocr_res
                if r.rect.x1 >= 45 and re.fullmatch(r'[\d,]{3,}', r.data.strip())
            ]

            if empty_items:
                empty_states.append(True)
                slot_empty_centers.append(
                    Point(
                        area.rect.x1 + empty_items[0].rect.center.x,
                        area.rect.y1 + empty_items[0].rect.center.y,
                    )
                )
                scored_states.append(False)
            else:
                empty_states.append(False)
                slot_empty_centers.append(None)
                scored_states.append(len(score_items) > 0)

        return empty_states, slot_empty_centers, scored_states

    def record_scored_slots(self, scored_states: list[bool]) -> None:
        """将界面上已有挑战分数的槽位同步写入周期运行记录。"""
        if self.run_record is None:
            return
        for idx, scored in enumerate(scored_states):
            if scored:
                self.run_record.add_slot_finished(idx)

    # ---------------- 阶段 1: 入场导航 (Step 1 - Step 5) ----------------

    @operation_node(name='开始', is_start_node=True)
    def start_node(self) -> OperationRoundResult:
        """检查初始状态，确定本次运行模式，如果在主界面直接进入下一步，否则返回大世界。"""
        self.boss_cycle = 0
        self.current_target_slot = -1

        already_prepared = self.run_record is not None and self.run_record.has_prepare_done_in_period()
        self.is_prepare_only_run = bool(self.config.two_day_mode and not already_prepared)
        if self.config.two_day_mode:
            if self.is_prepare_only_run:
                log.info('分天运行模式已启用：当前为本周期第 1 天运行，仅触发配队生成后结束（不进战）')
            else:
                log.info('分天运行模式已启用：本周期已完成预处理，本次直接执行完整战斗与领奖流程')

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
        """等待危局强袭战主界面加载完毕，并预检当前周期 3 个首领槽位完成进度 (Step 6)。"""
        if self.is_in_deadly_assault_main(self.last_screenshot):
            # 缓冲 0.5 秒待下方首领卡片文字/分数完全渲染
            time.sleep(0.5)
            self.last_screenshot = self.screenshot()
            empty_states, _, scored_states = self.scan_slot_states(self.last_screenshot)
            self.record_scored_slots(scored_states)

            if not any(empty_states) and sum(scored_states) >= 2:
                # 二次防抖确认，避免切换过渡帧误判
                time.sleep(0.5)
                self.last_screenshot = self.screenshot()
                empty_states2, _, scored_states2 = self.scan_slot_states(self.last_screenshot)
                self.record_scored_slots(scored_states2)
                if not any(empty_states2) and sum(scored_states2) >= 2:
                    log.info(
                        '主界面预检：3 个首领槽位均无 EMPTY 且已有成绩 (%s)，本周期挑战已完成，跳过配队与战斗',
                        scored_states2,
                    )
                    self.is_prepare_only_run = False
                    if self.run_record is not None:
                        self.run_record.mark_all_slots_finished()
                    return self.round_success(self.STATUS_ALL_BOSSES_DONE)

            if self.is_prepare_only_run or self.config.auto_team:
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
        # 防抖轮询：最多等待 1.5 秒检查是否出现规则说明
        for _ in range(3):
            if self.has_ocr_text(self.last_screenshot, ['规则说明', '规则']):
                break
            if self.has_ocr_text(self.last_screenshot, ['智能推荐', '绳网精选', '生成更多']):
                break
            time.sleep(0.5)
            self.last_screenshot = self.screenshot()

        if self.has_ocr_text(self.last_screenshot, ['规则说明', '规则']):
            log.info('检测到规则说明弹窗，查找右上角关闭按钮')
            # 1. 优先尝试模板匹配
            click_res = self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-配队指引',
                '规则说明-关闭',
                success_wait=1.8,
            )
            if click_res.is_success:
                return self.round_success()

            # 2. 特征匹配：检测右上角关闭按钮区域内的红色关闭叉号按键
            close_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '规则说明-关闭')
            if close_area is not None and self.click_red_feature_in_rect(
                self.last_screenshot,
                close_area.rect,
                min_pts=30,
                wait_sec=1.8,
                label='规则说明关闭叉号',
            ):
                return self.round_success()

            return self.round_retry('检测到规则说明弹窗但未定位到关闭按钮', wait=1.5)
        else:
            log.info('未出现规则说明弹窗，直接进入下一步')
            return self.round_success()

    @node_from(from_name='检查规则说明弹窗')
    @operation_node(name='点击智能推荐', node_max_retry_times=5)
    def click_ai_recommend(self) -> OperationRoundResult:
        """点击智能推荐选项卡 (Step 8)。"""
        # 容错与兜底：若规则说明弹窗仍未关闭，先关闭
        if self.has_ocr_text(self.last_screenshot, ['规则说明', '规则']):
            log.info('点击智能推荐前检测到规则说明弹窗仍存在，先点击关闭')
            close_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '规则说明-关闭')
            if close_area is not None:
                self.click_red_feature_in_rect(
                    self.last_screenshot,
                    close_area.rect,
                    min_pts=30,
                    wait_sec=1.5,
                    label='规则说明关闭叉号',
                )

        area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '智能推荐')
        return self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='智能推荐',
            area=area,
            crop_first=False,
            success_wait=1.8,
            retry_wait=1.5,
        )

    @node_from(from_name='点击智能推荐')
    @operation_node(name='点击生成更多', node_max_retry_times=5)
    def click_generate_more(self) -> OperationRoundResult:
        """智能推荐生成分支 (Step 9)。

        规则：
        1. 先确认当前已处于「智能推荐」页（可见「生成更多」按钮），避免误识别「绳网精选」页中的全部应用按钮。
        2. 若分天模式处于第 1 天预处理：严格按 prepare 规则点击「生成更多」后不等待，直接返回主界面并退回大世界。
        3. 若处于普通全量模式或分天模式第 2 天：只要「全部应用-区域」检测到存在「全部应用」按钮，直接跳过 Step 9 进入 Step 10；
           仅当不存在任何「全部应用」按钮时，才点击「生成更多」并进入等待生成阶段。
        """
        gen_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '生成更多')
        gen_crop = self.last_screenshot[gen_area.rect.y1:gen_area.rect.y2, gen_area.rect.x1:gen_area.rect.x2]
        gen_ocr = [r for r in self.ctx.ocr.ocr(gen_crop) if '生成' in r.data]
        if not gen_ocr:
            log.warning('当前画面未检测到生成更多按钮，尝试重新点击智能推荐页签')
            rec_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '智能推荐')
            self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='智能推荐',
                area=rec_area,
                crop_first=False,
                success_wait=1.5,
            )
            return self.round_retry('未进入智能推荐页面', wait=1.5)

        if self.is_prepare_only_run:
            res = self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='生成更多',
                area=gen_area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
            if not res.is_success:
                return res
            log.info('分天运行第 1 天（预处理）：已点击生成更多触发后台算队，不等待直接返回')
            return self.round_success(self.STATUS_PREPARE_ONLY_DONE)

        apply_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '全部应用-区域')
        crop = self.last_screenshot[apply_area.rect.y1:apply_area.rect.y2, apply_area.rect.x1:apply_area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        apply_buttons = [r for r in ocr_res if '全部' in r.data and '应用' in r.data]
        if apply_buttons:
            apply_ys = [apply_area.rect.y1 + r.rect.center.y for r in apply_buttons]
            log.info(
                '检测到智能推荐页已存在 %d 个「全部应用」按钮 (y=%s)，跳过 Step 9 直接进入 Step 10',
                len(apply_buttons),
                apply_ys,
            )
            return self.round_success(self.STATUS_SKIP_GENERATE)

        self.initial_apply_count = 0
        self.initial_apply_ys = []
        log.info('当前智能推荐页暂无「全部应用」按钮，点击生成更多开始验算配队')

        res = self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='生成更多',
            area=gen_area,
            crop_first=False,
            success_wait=1.8,
            retry_wait=1.5,
        )
        if not res.is_success:
            return res

        self.generate_start_time = time.time()
        log.info('已触发生成更多，进入生成等待（最大 %d 秒）', self.config.wait_generate_seconds)
        return self.round_success(self.STATUS_WAIT_GENERATE)

    @node_from(from_name='点击生成更多', status=STATUS_WAIT_GENERATE)
    @operation_node(name='等待生成配队')
    def wait_generation(self) -> OperationRoundResult:
        """等待配队验算生成出现「全部应用」按钮或达到最大等待时间 (Step 9)。"""
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

        if current_count > 0:
            log.info('检测到已生成「全部应用」按钮（共 %d 个，已等待 %.1f 秒），进入 Step 10',
                     current_count, elapsed)
            return self.round_success()

        if elapsed >= self.config.wait_generate_seconds:
            log.info('已达到最大等待时间 %d 秒，继续执行后续应用操作', self.config.wait_generate_seconds)
            return self.round_success()

        return self.round_wait(f'等待配队生成中 (已等待 {int(elapsed)}/{self.config.wait_generate_seconds}s)', wait=3.0)

    @node_from(from_name='点击生成更多', status=STATUS_SKIP_GENERATE)
    @node_from(from_name='等待生成配队')
    @operation_node(name='应用全部阵容', node_max_retry_times=5)
    def apply_all_teams(self) -> OperationRoundResult:
        """在全部应用区域检测并点击按钮，若有多个则优先最下方 (Step 10)。"""
        apply_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '全部应用-区域')
        crop = self.last_screenshot[apply_area.rect.y1:apply_area.rect.y2, apply_area.rect.x1:apply_area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        apply_buttons = [r for r in ocr_res if '全部' in r.data and '应用' in r.data]

        if apply_buttons:
            apply_buttons.sort(key=lambda item: item.rect.center.y, reverse=True)
            chosen = apply_buttons[0]
            target_pt = Point(apply_area.rect.x1 + chosen.rect.center.x, apply_area.rect.y1 + chosen.rect.center.y)
            log.info('在全部应用区域检测到 %d 个按钮，点击最下方按钮 (%d, %d)',
                     len(apply_buttons), target_pt.x, target_pt.y)
            self.ctx.controller.click(target_pt)
            time.sleep(2.0)
            return self.round_success()

        # 严格遵守规范：绝不盲目点击固定坐标！若未检测到按钮文字，进行重试
        log.warning('未在配队指引的指定区域内检测到全部应用按钮，等待重试')
        return self.round_retry('未检测到全部应用按钮', wait=1.5)

    @node_from(from_name='应用全部阵容')
    @operation_node(name='确认阵容覆盖')
    def confirm_cover_team(self) -> OperationRoundResult:
        """如果存在阵容覆盖确认对话框，检测那一条水平线的一小块区域并点击确认 (Step 11)。"""
        # 防抖轮询：最多等待 1.5 秒检查是否出现阵容覆盖弹窗
        for _ in range(3):
            if self.has_ocr_text(self.last_screenshot, ['阵容覆盖', '覆盖', '是否确认']):
                break
            time.sleep(0.5)
            self.last_screenshot = self.screenshot()

        if self.has_ocr_text(self.last_screenshot, ['阵容覆盖', '覆盖', '是否确认']):
            log.info('检测到阵容覆盖确认弹窗，检测底部水平线区域并点击确认按钮')
            # 底部按钮水平线区域 [x: 650..1300, y: 780..880]，完美兼顾单按钮居中 (center ~960) 与双按钮居右 (center ~1115)
            strip_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '阵容覆盖-确认')
            if strip_area is None or strip_area.rect.x1 > 700:
                strip_area = ScreenArea(
                    area_name='阵容覆盖-确认-水平线',
                    pc_rect=Rect(650, 780, 1300, 880),
                    text='确认',
                )

            return self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='确认',
                area=strip_area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
        else:
            log.info('未出现阵容覆盖确认弹窗，直接进入下一步')
            return self.round_success()

    @node_from(from_name='确认阵容覆盖')
    @node_from(from_name='点击生成更多', status=STATUS_PREPARE_ONLY_DONE)
    @operation_node(name='返回危局主界面', node_max_retry_times=5)
    def back_from_team_guide(self) -> OperationRoundResult:
        """点击左上角返回按钮回到危局强袭战主界面 (Step 12)。"""
        # 容错与兜底：若阵容覆盖弹窗仍未确认，先点击确认
        if self.has_ocr_text(self.last_screenshot, ['阵容覆盖', '覆盖', '是否确认']):
            log.info('返回主界面前检测到阵容覆盖确认弹窗仍存在，先点击确认')
            strip_area = self.ctx.screen_loader.get_area('危局强袭战-配队指引', '阵容覆盖-确认')
            if strip_area is None or strip_area.rect.x1 > 700:
                strip_area = ScreenArea(
                    area_name='阵容覆盖-确认-水平线',
                    pc_rect=Rect(650, 780, 1300, 880),
                    text='确认',
                )
            confirm_res = self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='确认',
                area=strip_area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
            if confirm_res.is_success:
                return self.round_wait('已确认阵容覆盖弹窗，继续返回主界面', wait=1.0)
            return confirm_res

        done_status = self.STATUS_PREPARE_ONLY_DONE if self.is_prepare_only_run else None

        # 检查是否已在危局主界面
        if self.is_in_deadly_assault_main(self.last_screenshot):
            log.info('已回到危局强袭战主界面')
            return self.round_success(done_status)

        # 1. 尝试框架通用返回区域匹配
        res = self.round_by_find_and_click_area(
            self.last_screenshot,
            '画面-通用',
            '返回',
            success_wait=2.0,
            retry_wait=1.5,
        )
        if res.is_success:
            return self.round_success(done_status)

        # 2. 特征检测：配队指引左上角红色药丸状返回按键检测（位置 [60, 15, 160, 85]）
        if self.click_red_feature_in_rect(
            self.last_screenshot,
            Rect(60, 15, 160, 85),
            min_pts=50,
            wait_sec=2.0,
            label='左上角红色返回按键',
        ):
            return self.round_success(done_status)

        return self.round_retry('未检测到左上角返回按钮', wait=1.5)

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
        - 检测并验证目标槽位确实包含 EMPTY 标识后，点击其精确中心坐标；全部完成则进入荣誉酬赏。
        """
        if self.boss_cycle >= 3:
            log.info('3 轮关卡已全部挑战完毕，进入领奖流程')
            if self.run_record is not None:
                self.run_record.mark_all_slots_finished()
            return self.round_success(self.STATUS_ALL_BOSSES_DONE)

        empty_states, slot_empty_centers, scored_states = self.scan_slot_states(self.last_screenshot)
        self.record_scored_slots(scored_states)

        slot_labels = [
            'EMPTY' if empty_states[i] else ('SCORED' if scored_states[i] else 'NONE')
            for i in range(3)
        ]
        log.info('当前 3 个槽位状态: 左=%s, 中=%s, 右=%s (当前第 %d 轮)',
                 slot_labels[0], slot_labels[1], slot_labels[2], self.boss_cycle + 1)

        if not any(empty_states):
            if self.boss_cycle > 0 or sum(scored_states) >= 2:
                log.info('画面上已无未完成的 EMPTY 关卡，进入领奖结算流程')
                if self.run_record is not None:
                    self.run_record.mark_all_slots_finished()
                return self.round_success(self.STATUS_ALL_BOSSES_DONE)
            log.warning('未检测到任何 EMPTY 槽位或已有成绩，等待界面稳定后重试')
            return self.round_retry('未检测到关卡槽位状态', wait=1.5)

        target_slot = self.boss_cycle
        if not (0 <= target_slot < 3 and empty_states[target_slot]):
            target_slot = empty_states.index(True)

        self.current_target_slot = target_slot
        chosen_slot_name = self.SLOT_NAMES[target_slot]
        chosen_center = slot_empty_centers[target_slot]

        if chosen_center is not None:
            log.info('在 %s 区域检测到 EMPTY 标识，点击其中心坐标 (%d, %d)',
                     chosen_slot_name, chosen_center.x, chosen_center.y)
            self.ctx.controller.click(chosen_center)
            time.sleep(2.0)
            self.boss_cycle += 1
            return self.round_success(self.STATUS_BATTLE_TARGET)
        else:
            log.warning('%s 区域内未检测到 EMPTY 标识，等待重试', chosen_slot_name)
            return self.round_retry(f'未在{chosen_slot_name}检测到EMPTY标识', wait=1.5)

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

        不同账号或关卡完成状态下，右下角按钮文本可能为'下一步'或'开始挑战'，OR 分支检测满足其一即点击。
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

        # 备选：全屏匹配目标文字
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

        # 严格遵守规范：绝不盲目点击固定坐标！若未识别到文字，进行重试
        log.warning('未在关卡详情界面检测到下一步或开始挑战按钮，等待重试')
        return self.round_retry('未识别到下一步或开始挑战文字', wait=1.5)

    @node_from(from_name='点击下一步')
    @operation_node(name='检查锁定出战弹窗')
    def check_lock_deploy_dialog(self) -> OperationRoundResult:
        """检查并确认可能出现的出战锁定提示弹窗 (Step 14 之后，'是否确认出战？')。

        本弹窗为条件出现（并非一定出现）：若弹出，点击右侧'确认'继续；若未弹出，直接进入后续阶段。
        由于该任务是 3 次首领循环（Step 14->15, 18->19, 22->23 共用此节点），必须具有足够的防抖等待和多重兜底。
        """
        # 防抖轮询：最多等待 2.0 秒（4次 x 0.5s）检测弹窗渲染，若已进入通用出战或战斗画面则提前退出
        for _ in range(4):
            if self.has_ocr_text(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定', '锁定后']):
                break
            if self.round_by_find_area(self.last_screenshot, '通用-出战', '按钮-出战').is_success:
                break
            if self.ctx.auto_battle_context.is_normal_attack_btn_available(self.last_screenshot):
                break
            time.sleep(0.5)
            self.last_screenshot = self.screenshot()

        if self.has_ocr_text(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定', '锁定后']):
            log.info('检测到锁定出战提示弹窗（是否确认出战？），检测并点击确认按钮')
            area = self.ctx.screen_loader.get_area('危局强袭战-关卡详情', '锁定确认-确认')
            if area is None:
                area = ScreenArea(
                    area_name='锁定确认-确认-水平条',
                    pc_rect=Rect(1000, 620, 1250, 710),
                    text='确认',
                )
            return self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='确认',
                area=area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )
        else:
            log.info('未出现锁定出战确认弹窗，直接进入出战阶段')
            return self.round_success()

    @node_from(from_name='检查锁定出战弹窗')
    @operation_node(name='出战确认', node_max_retry_times=5)
    def check_deploy(self) -> OperationRoundResult:
        """如果在通用出战界面，点击出战并确认。"""
        # 容错兜底：若前一步因网络或动画延迟，弹窗在此刻才渲染出来，先点击确认
        if self.has_ocr_text(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定', '锁定后']):
            log.info('出战确认节点检测到残留的锁定出战弹窗，检测并点击确认')
            area = self.ctx.screen_loader.get_area('危局强袭战-关卡详情', '锁定确认-确认')
            if area is None:
                area = ScreenArea(
                    area_name='锁定确认-确认-水平条',
                    pc_rect=Rect(1000, 620, 1250, 710),
                    text='确认',
                )
            return self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='确认',
                area=area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
            )

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
            return self.round_by_ocr_and_click(self.last_screenshot, target_cn='出战', success_wait=2.0)

        return self.round_success()

    @node_from(from_name='出战确认')
    @operation_node(name='等待战斗画面')
    def wait_battle_screen(self) -> OperationRoundResult:
        """等待战斗画面加载完毕 (Step 15, 19, 23)。"""
        # 容错兜底：等待战斗期间若出现锁定弹窗阻挡，点击确认
        if self.has_ocr_text(self.last_screenshot, ['是否确认出战', '确认出战', '将被锁定', '锁定后']):
            log.info('等待战斗画面时检测到锁定出战弹窗，检测并点击确认')
            area = self.ctx.screen_loader.get_area('危局强袭战-关卡详情', '锁定确认-确认')
            if area is None:
                area = ScreenArea(
                    area_name='锁定确认-确认-水平条',
                    pc_rect=Rect(1000, 620, 1250, 710),
                    text='确认',
                )
            self.round_by_ocr_and_click(
                self.last_screenshot,
                target_cn='确认',
                area=area,
                crop_first=False,
                success_wait=2.0,
                retry_wait=1.5,
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
        self._last_end_ocr_time = 0.0
        self._out_of_battle_since = None
        return self.round_success()

    @node_from(from_name='向前走并启动战斗')
    @operation_node(name='自动战斗中', mute=True)
    def auto_battle_loop(self) -> OperationRoundResult:
        """自动战斗循环，直至结算退出按钮出现 (Step 15 -> Step 16)。"""
        now = time.time()
        if now - self.battle_start_time > 600:
            log.warning('战斗超时 (600s)，强制停止战斗')
            self.ctx.auto_battle_context.stop_auto_battle()
            return self.round_fail('战斗超时')

        in_battle = self.ctx.auto_battle_context.check_battle_state(self.last_screenshot, self.last_screenshot_time)

        if in_battle:
            self._out_of_battle_since = None
        else:
            if self._out_of_battle_since is None:
                self._out_of_battle_since = now

            exit_area = self.ctx.screen_loader.get_area('战斗画面', '战斗结果-退出')
            if exit_area is not None:
                res = self.round_by_find_area(self.last_screenshot, '战斗画面', '战斗结果-退出')
                if res.is_success:
                    log.info('检测到结算退出按钮，停止自动战斗')
                    self.ctx.auto_battle_context.stop_auto_battle()
                    return self.round_success(self.STATUS_BATTLE_END)

            # 仅在连续离开战斗画面 >= 4s 后才触发全屏 OCR 兜底，避免大招/连携技期间误触发造成卡顿
            if (now - self._out_of_battle_since >= 4.0) and (now - getattr(self, '_last_end_ocr_time', 0.0) >= 3.0):
                self._last_end_ocr_time = now
                ocr_results = self.ctx.ocr.match_words(self.last_screenshot, ['退出', '挑战结果', '再来一次'], threshold=0.6)
                if '退出' in ocr_results or '挑战结果' in ocr_results or '再来一次' in ocr_results:
                    log.info('OCR 识别到结算画面 (%s)，停止自动战斗', list(ocr_results.keys()))
                    self.ctx.auto_battle_context.stop_auto_battle()
                    return self.round_success(self.STATUS_BATTLE_END)

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
        if self.is_in_deadly_assault_main(self.last_screenshot):
            log.info('已成功返回危局强袭战主界面')
            if self.run_record is not None and 0 <= self.current_target_slot < 3:
                self.run_record.add_slot_finished(self.current_target_slot)
            return self.round_success()

        return self.round_wait('等待返回主界面', wait=1.5)

    # ---------------- 阶段 4: 荣誉酬赏与完成 (Step 26 - Step 29) ----------------

    @node_from(from_name='等待主界面加载', status=STATUS_ALL_BOSSES_DONE)
    @node_from(from_name='选择首领关卡', status=STATUS_ALL_BOSSES_DONE)
    @operation_node(name='点击荣誉酬赏', node_max_retry_times=5)
    def click_reward_icon(self) -> OperationRoundResult:
        """检测左下角荣誉酬赏并点击其上方图标 (Step 26)。"""
        if not self.config.claim_rewards:
            log.info('配置设置为不领取荣誉酬赏，跳过')
            return self.round_success(self.STATUS_SKIP_REWARD)

        # 检测左下角固定区域是否包含“荣誉酬赏”文字
        area = self.ctx.screen_loader.get_area('危局强袭战-主界面', '荣誉酬赏')
        crop = self.last_screenshot[area.rect.y1:area.rect.y2, area.rect.x1:area.rect.x2]
        ocr_res = self.ctx.ocr.ocr(crop)
        reward_items = [r for r in ocr_res if '荣誉' in r.data or '酬赏' in r.data]

        if not reward_items:
            # 扩大至左下角全量区域检测
            all_ocr = self.ctx.ocr.ocr(self.last_screenshot[950:1080, 30:250])
            reward_items = [r for r in all_ocr if '荣誉' in r.data or '酬赏' in r.data]
            base_x, base_y = 30, 950
        else:
            base_x, base_y = area.rect.x1, area.rect.y1

        if reward_items:
            r = reward_items[0]
            # 图标位于文字上方约 15 像素
            icon_x = base_x + r.rect.center.x
            icon_y = base_y + r.rect.y1 - 15
            target_pt = Point(icon_x, icon_y)
            log.info('检测到荣誉酬赏文字 (%s)，点击其上方图标位置 (%d, %d)',
                     r.data, target_pt.x, target_pt.y)
            self.ctx.controller.click(target_pt)
            time.sleep(2.0)
            return self.round_success(self.STATUS_CLAIM_REWARD)

        log.warning('未在左下角检测到荣誉酬赏文字，等待重试')
        return self.round_retry('未检测到荣誉酬赏文字', wait=1.5)

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
        """点击奖励获得确认按钮；若奖励已领完无弹窗，则关闭挑战奖励窗口 (Step 28)。"""
        area = self.ctx.screen_loader.get_area('危局强袭战-荣誉酬赏', '确认')
        res = self.round_by_ocr_and_click(
            self.last_screenshot,
            target_cn='确认',
            area=area,
            crop_first=False,
            success_wait=1.8,
        )
        if res.is_success:
            if self.run_record is not None:
                self.run_record.rewards_claimed = True
            return res

        # 若仍停留在挑战奖励弹窗（例如本期奖励之前已被手动领光，点击全部领取不会再弹获得窗口）
        if self.has_ocr_text(self.last_screenshot, ['挑战奖励', '已领取']):
            log.info('挑战奖励已处于全部领取完成状态，关闭奖励弹窗')
            close_res = self.round_by_find_and_click_area(
                self.last_screenshot,
                '危局强袭战-荣誉酬赏',
                '关闭',
                success_wait=1.5,
            )
            closed = close_res.is_success
            if not closed:
                close_area = self.ctx.screen_loader.get_area('危局强袭战-荣誉酬赏', '关闭')
                if close_area is not None:
                    closed = self.click_red_feature_in_rect(
                        self.last_screenshot,
                        close_area.rect,
                        min_pts=30,
                        wait_sec=1.5,
                        label='挑战奖励关闭按钮',
                    )
            if closed:
                if self.run_record is not None:
                    self.run_record.rewards_claimed = True
                return self.round_success('奖励已领完并关闭窗口')
            return self.round_retry('检测到挑战奖励弹窗但未定位到关闭按钮', wait=1.5)

        return self.round_retry('未检测到奖励确认或挑战奖励窗口', wait=1.5)

    @node_from(from_name='点击确认奖励')
    @node_from(from_name='点击荣誉酬赏', status=STATUS_SKIP_REWARD)
    @node_from(from_name='返回危局主界面', status=STATUS_PREPARE_ONLY_DONE)
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

        if self.is_prepare_only_run:
            if self.run_record is not None:
                self.run_record.mark_prepare_done()
            log.info('危局强袭战-分天预处理完成：已触发配队生成，下次运行将跳过 Step 9 等待并执行挑战')
            return self.round_success('危局强袭战-分天预处理完成')

        if self.run_record is not None:
            self.run_record.mark_full_completed()
            log.info(
                '危局强袭战全流程执行完成，本周期记录已保存（下次危局刷新日: %s 04:00）',
                self.run_record.get_next_refresh_dt(),
            )
        else:
            log.info('危局强袭战全流程执行完成')
        return self.round_success('危局强袭战全部挑战及领奖完成')
