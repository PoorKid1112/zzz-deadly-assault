# 危局强袭战 (Deadly Assault)

《绝区零》危局强袭战（试炼模式）全流程自动化挑战插件，遵循 OneDragon 第三方插件规范开发。

> 💡 若希望节省智能推荐配队约 5 分钟的验算等待时间，可搭配拆分版插件在「一条龙」中头尾并行运行：
> - 前置触发算队：[危局强袭战-预处理 (`deadly_assault_prepare`)](https://github.com/PoorKid1112/zzz-deadly-assault-prepare)
> - 末尾自动挑战：[危局强袭战-战斗 (`deadly_assault_battle`)](https://github.com/PoorKid1112/zzz-deadly-assault-battle)

---

## 核心功能

1. **入场与选关** (Step 1 - Step 5)：
   - 从大世界或快捷手册自动导航进入「快捷手册 - 作战」。
   - 点击左侧「危局强袭战」，选择「试炼」模式卡片中的「前往」，并确认传送。
   - 进入主界面后自动预检 3 个首领槽位：若均无 `EMPTY` 且已有成绩，自动跳过配队与战斗直接进入领奖/结束。

2. **智能配队推荐** (Step 6 - Step 12)：
   - 打开「配队指引」，检查并自动关闭可能出现的规则说明弹窗。
   - 切换到「智能推荐」页面，点击「生成更多」并等待验算完成（检测新卡片生成或超时保护）。
   - 检测目标区域内的「全部应用」按钮（若有多个生成结果则优先应用最下方），条件确认「阵容覆盖」后返回主界面。

3. **三首领挑战循环** (Step 13 - Step 25)：
   - 检测下方 3 个槽位的 `EMPTY` 状态与精确坐标，优先从左到右依次挑战未完成首领（排除邦布图标噪点 `0` 误判）。
   - 点击右下角「选定敌方」进入关卡详情，支持「下一步」与「开始挑战」OR 分支识别。
   - 多次防抖检测并确认可能出现的出战锁定提示（「是否确认出战？」），再完成通用出战确认。
   - 进战后**按住 `W` 键向前移动，同时动态检测右上角 Boss 血条（荧光绿像素 / 数字 `29`）**，血条一出现立即松开 `W` 并启动自动战斗。
   - 结算界面出现「退出」按钮时停止战斗并返回主界面，循环完成全部 3 个首领。

4. **荣誉酬赏领取与双周运行判断** (Step 26 - Step 29)：
   - 3 关全部完成后点击左下角「荣誉酬赏」宝箱图标，执行「全部领取」并确认奖励（若本期已手动领光则自动关闭挑战奖励窗口）。
   - **双周交替运行判断**：遵循绝区零「危局强袭战」与「式舆防卫战」每周五 04:00 双周交替刷新的规则（14 天一周期）。本周期完成后，式舆防卫战刷新周自动保持完成态并跳过，直到下一轮危局周五 04:00 刷新才自动重置；与拆分版「危局强袭战-战斗」共享本周期完成状态。

---

## 插件配置项

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :---: | :---: | :--- |
| `auto_team` | `bool` | `True` | 是否在挑战前自动执行智能推荐配队（步骤 6-12） |
| `wait_generate_seconds` | `int` | `300` | 点击生成更多后的最大等待时间（秒，步骤 9） |
| `walk_duration` | `float` | `5.0` | 进入战斗后向前移动的最大时间（秒，检测到 Boss 血条即提前停止） |
| `claim_rewards` | `bool` | `True` | 3 关挑战完成后是否领取荣誉酬赏（步骤 26-28） |
| `auto_battle_config` | `str` | `""` | 指定战斗策略名称，留空则跟随全局战斗助手配置 |
| `cycle_anchor_dt` | `str` | `"20261120"` | 双周周五刷新基准日（`yyyyMMdd`，默认对应周五 04:00 刷新锚点） |

---

## 插件规范与目录结构

- **零盲点点击**：所有点击动作均先在对应识别区域验证目标文本或图像特征，再按实时定位坐标触发点击。
- **数据隔离**：运行调试截图保存在 `plugin_data/deadly_assault/`，运行记录按 OneDragon 规范管理。

```text
deadly_assault/
├── __init__.py               # Python 包标识
├── const.py                  # APP_ID, APP_NAME, 优先级 (PRIORITY=1220) 与插件常量
├── factory.py                # 应用工厂 (DeadlyAssaultFactory)
├── config.py                 # 应用配置 (DeadlyAssaultConfig)
├── run_record.py             # 14 天双周周期运行判断与共享状态记录
├── screens.py                # 画面定义加载与校验
├── app.py                    # 状态机主逻辑 (DeadlyAssaultApp)
├── plugin.json               # 插件市场元数据清单
├── README.md                 # 中文说明文档
├── screen_info/              # 画面识别区域定义
│   ├── deadly_assault_battle.yml
│   ├── deadly_assault_guide.yml
│   ├── deadly_assault_main.yml
│   ├── deadly_assault_modes.yml
│   └── deadly_assault_rewards.yml
└── assets/
    └── cover.webp            # WebP 封面 (55KB)
```

---

## 安装与使用

1. 可直接在 OneDragon 插件市场安装，或将 `deadly_assault` 目录放入主程序 `plugins/` 下。
2. 启动或刷新 OneDragon 后，在「一条龙」日常列表或「应用运行」中启用「危局强袭战」即可。
