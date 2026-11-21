"""危局强袭战插件设置页。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon

from one_dragon.base.config.config_item import ConfigItem
from one_dragon_qt.services.app_setting.app_setting_provider import GroupIdMixin
from one_dragon_qt.utils.config_utils import get_prop_adapter
from one_dragon_qt.widgets.setting_card.combo_box_setting_card import (
    ComboBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.spin_box_setting_card import (
    DoubleSpinBoxSettingCard,
    SpinBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard
from one_dragon_qt.widgets.vertical_scroll_interface import VerticalScrollInterface
from zzz_od.application.battle_assistant.auto_battle_config import (
    get_auto_battle_op_config_list,
)

from . import const
from .config import DeadlyAssaultConfig

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class DeadlyAssaultSettingInterface(VerticalScrollInterface, GroupIdMixin):
    """危局强袭战配置界面。"""

    def __init__(self, ctx: ZContext, parent: QWidget | None = None) -> None:
        super().__init__(
            content_widget=None,
            object_name='deadly_assault_setting_interface',
            nav_text_cn='危局强袭战',
            parent=parent,
        )
        self.ctx: ZContext = ctx
        self.config: DeadlyAssaultConfig | None = None

        self.two_day_mode_opt: SwitchSettingCard | None = None
        self.auto_team_opt: SwitchSettingCard | None = None
        self.wait_generate_opt: SpinBoxSettingCard | None = None
        self.walk_duration_opt: DoubleSpinBoxSettingCard | None = None
        self.claim_rewards_opt: SwitchSettingCard | None = None
        self.auto_battle_opt: ComboBoxSettingCard | None = None
        self.anchor_dt_opt: TextSettingCard | None = None

    def get_content_widget(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = CaptionLabel(
            '遵循「危局强袭战」与「式舆防卫战」每周五 04:00 双周交替刷新机制（14 天一周期）。\n'
            '进入配队指引智能推荐页后，只要检测到已存在「全部应用」按钮，会自动跳过 Step 9 生成等待并直接进入 Step 10。',
            widget,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.two_day_mode_opt = SwitchSettingCard(
            icon=FluentIcon.CALENDAR,
            title='分天运行模式（第 1 天仅预处理，第 2 天再战斗）',
            content='默认关闭。启用后本周期第 1 天仅点击「生成更多」后返回省去等待；第 2 天（含跨式舆周五）仅执行这一次挑战并结算，通关后本周期不再运行',
        )
        layout.addWidget(self.two_day_mode_opt)

        self.auto_team_opt = SwitchSettingCard(
            icon=FluentIcon.PEOPLE,
            title='自动智能推荐配队',
            content='挑战前进入配队指引 -> 智能推荐，存在「全部应用」则直接应用，否则触发生成并等待',
        )
        layout.addWidget(self.auto_team_opt)

        self.wait_generate_opt = SpinBoxSettingCard(
            icon=FluentIcon.HISTORY,
            title='配队生成最大等待秒数（Step 9）',
            content='仅当智能推荐页尚无「全部应用」按钮时生效（默认 300 秒）',
            step=10,
            minimum=0,
            maximum=600,
        )
        layout.addWidget(self.wait_generate_opt)

        self.walk_duration_opt = DoubleSpinBoxSettingCard(
            icon=FluentIcon.RIGHT_ARROW,
            title='进战前压最大秒数',
            content='按住 W 向前走动的最长时长；右上角检测到 Boss 血条或数字 29 即提前停止并开打',
            step=0.5,
            minimum=0.5,
            maximum=20.0,
        )
        layout.addWidget(self.walk_duration_opt)

        self.claim_rewards_opt = SwitchSettingCard(
            icon=FluentIcon.CHECKBOX,
            title='挑战结束后领取荣誉酬赏',
            content='3 关全部完成后打开左下角荣誉酬赏并点击全部领取',
        )
        layout.addWidget(self.claim_rewards_opt)

        self.auto_battle_opt = ComboBoxSettingCard(
            icon=FluentIcon.GAME,
            title='自动战斗策略',
            content='默认跟随全局战斗助手配置',
        )
        layout.addWidget(self.auto_battle_opt)

        self.anchor_dt_opt = TextSettingCard(
            icon=FluentIcon.DATE_TIME,
            title='双周周五刷新基准日 (yyyyMMdd)',
            content='用于计算 14 天危局刷新周期锚点，默认 20261120（周五 04:00 危局刷新）',
            input_placeholder='20261120',
        )
        layout.addWidget(self.anchor_dt_opt)

        layout.addStretch(1)
        return widget

    def on_interface_shown(self) -> None:
        super().on_interface_shown()

        self.config = self.ctx.run_context.get_config(
            app_id=const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=self.group_id,
        )

        self.two_day_mode_opt.init_with_adapter(get_prop_adapter(self.config, 'two_day_mode'))
        self.auto_team_opt.init_with_adapter(get_prop_adapter(self.config, 'auto_team'))
        self.wait_generate_opt.init_with_adapter(get_prop_adapter(self.config, 'wait_generate_seconds'))
        self.walk_duration_opt.init_with_adapter(get_prop_adapter(self.config, 'walk_duration'))
        self.claim_rewards_opt.init_with_adapter(get_prop_adapter(self.config, 'claim_rewards'))

        battle_options = [ConfigItem('跟随全局配置', '')]
        try:
            battle_options.extend(get_auto_battle_op_config_list(sub_dir='auto_battle'))
        except Exception:
            pass
        self.auto_battle_opt.set_options_by_list(battle_options)
        self.auto_battle_opt.init_with_adapter(get_prop_adapter(self.config, 'auto_battle_config'))

        self.anchor_dt_opt.init_with_adapter(get_prop_adapter(self.config, 'cycle_anchor_dt'))
