"""
Движок для работы с block states (facing, half, open, hinge, axis и т.д.).

Формула вычисления числового ID подтверждена экспериментально на 3
независимых блоках (furnace, oak_door, oak_stairs) - совпадает с
defaultState из официальных данных Mojang во всех случаях:

    state_id = minStateId + Σ (value_index_i * multiplier_i)

где multiplier_i = произведение num_values всех СЛЕДУЮЩИХ свойств
(первое свойство в списке - самое "медленное", последнее - самое "быстрое").
Для bool-свойств порядок такой: index 0 = True, index 1 = False
(да, наоборот от интуиции - но так фактически хранит Mojang).
"""
import json
import os

_DATA_PATH = os.path.join(
    os.path.dirname(__file__), "data", "blocks_full_1_20_1.json"
)

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _BLOCKS = json.load(_f)

BLOCK_BY_NAME = {b["name"]: b for b in _BLOCKS}
BLOCK_BY_ID = {b["id"]: b for b in _BLOCKS}


def compute_state_id(block_name: str, **properties) -> int:
    """properties: например facing='north', half='lower', open=False.
    Свойства, которые не переданы, берутся из defaultState (распакованного)."""
    block = BLOCK_BY_NAME[block_name]
    states = block.get("states", [])
    if not states:
        return block["defaultState"]

    default_props = decode_state(block_name, block["defaultState"])
    merged = {**default_props, **properties}

    multipliers = []
    running = 1
    for s in reversed(states):
        multipliers.insert(0, running)
        running *= s["num_values"]

    offset = 0
    for s, mult in zip(states, multipliers):
        val = merged[s["name"]]
        if s["type"] == "bool":
            idx = 0 if val else 1
        else:
            idx = s["values"].index(val)
        offset += idx * mult

    return block["minStateId"] + offset


def decode_state(block_name: str, state_id: int) -> dict:
    """Обратная операция: по числовому ID восстанавливает словарь свойств."""
    block = BLOCK_BY_NAME[block_name]
    states = block.get("states", [])
    if not states:
        return {}

    offset = state_id - block["minStateId"]

    multipliers = []
    running = 1
    for s in reversed(states):
        multipliers.insert(0, running)
        running *= s["num_values"]

    result = {}
    for s, mult in zip(states, multipliers):
        idx = (offset // mult) % s["num_values"]
        if s["type"] == "bool":
            result[s["name"]] = (idx == 0)
        else:
            result[s["name"]] = s["values"][idx]
    return result


def has_property(block_name: str, prop_name: str) -> bool:
    block = BLOCK_BY_NAME.get(block_name)
    if block is None:
        return False
    return any(s["name"] == prop_name for s in block.get("states", []))
