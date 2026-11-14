"""
HomeKit 空调风速档位适配。

原空调是 HomeKit HeaterCooler 且转速按固定档位（minStep）调节时，
homekit_controller 按 33/66/100 换算风速：档位为 25 时中风会被写成 75、
读回又判定为高风，50 这一档也用不到。这里绕过它，直接读写转速特征值，
按档位映射风速名。非 HomeKit 空调、或档位数不在映射表里时返回 None，走普通流程。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.core import HomeAssistant

from .v_utils.vlog import VLog

_TAG = "homekit_fan"

# 转速档位数 -> 风速名（用 vivo 能识别的名字）
# 实测这批 HomeKit 空调网关：25=自动风，50/75/100=低/中/高；
# 自动风只能读，写 25 或 0 网关都不执行
STEP_FAN_MODES: dict[int, list[str]] = {
    3: ["low", "medium", "high"],
    4: ["auto", "low", "medium", "high"],
    5: ["low", "middle_low", "medium", "middle_high", "high"],
}
# 只显示、不能设置的风速
READ_ONLY_FAN_MODES = {"auto"}


@dataclass
class SteppedRotation:
    entity: Any
    char: Any
    levels: list[int]
    names: list[str]

    @property
    def settable_names(self) -> list[str]:
        return [n for n in self.names if n not in READ_ONLY_FAN_MODES]

    def fan_mode_for_current_value(self) -> str | None:
        value = self.char.value
        if not value:
            return None
        nearest = min(range(len(self.levels)), key=lambda i: abs(self.levels[i] - value))
        return self.names[nearest]

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if fan_mode not in self.names or fan_mode in READ_ONLY_FAN_MODES:
            raise ValueError(f"unsupported fan mode: {fan_mode}")
        from aiohomekit.model.characteristics import CharacteristicsTypes

        await self.entity.async_put_characteristics(
            {CharacteristicsTypes.ROTATION_SPEED: self.levels[self.names.index(fan_mode)]}
        )


def get_stepped_rotation(hass: HomeAssistant, entity_id: str) -> SteppedRotation | None:
    try:
        from aiohomekit.model.characteristics import CharacteristicsTypes

        entity = hass.data["climate"].get_entity(entity_id)
        char = entity.service[CharacteristicsTypes.ROTATION_SPEED]
    except Exception:  # noqa: BLE001 - 非 HomeKit 空调
        return None
    step = char.minStep
    if not step:
        return None
    low = char.minValue or 0
    high = char.maxValue or 100
    levels: list[int] = []
    value = low + step
    while value <= high:
        levels.append(value)
        value += step
    if (names := STEP_FAN_MODES.get(len(levels))) is None:
        return None
    return SteppedRotation(entity, char, levels, names)


def subscribe_rotation(st: SteppedRotation, callback: Callable[[], None]) -> Callable[[], None] | None:
    """遥控器在相邻档位（如 75/100）间切换时原实体状态不变，直接订阅转速特征值。"""
    try:
        return st.entity._accessory.async_subscribe(
            {(st.char.service.accessory.aid, st.char.iid)}, callback
        )
    except Exception:  # noqa: BLE001
        VLog.info(_TAG, "cannot subscribe rotation characteristic")
        return None
