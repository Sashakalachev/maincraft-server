"""
Собирает пакет Join Game (clientbound Login play) для протокола 763 (1.20.1),
используя настоящие данные Mojang (registry codec и т.д.), взятые из
data/login_packet_1_20_1.json - это точная копия того, что реально шлёт
vanilla-сервер 1.20.1 (извлечено проектом minecraft-data через тесты
node-minecraft-protocol против настоящего сервера).
"""
import json
import os

# Проверяем оба возможных места для файла
possible_paths = [
    os.path.join(os.path.dirname(__file__), "login_packet_1_20_1.json.json"),
    os.path.join(os.path.dirname(__file__), "data", "login_packet_1_20_1.json"),
]

_DATA_PATH = None
for path in possible_paths:
    if os.path.exists(path):
        _DATA_PATH = path
        break

if _DATA_PATH is None:
    raise FileNotFoundError(
        "Не найден login_packet_1_20_1.json ни в папке с join_game.py, "
        "ни в папке data/ рядом с join_game.py"
    )

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _LOGIN_DATA = json.load(_f)

from nbt_writer import encode_nbt
from protocol import write_string, write_varint, write_long

_DIMENSION_CODEC_BYTES = encode_nbt(_LOGIN_DATA["dimensionCodec"], root_name="")


def build_join_game_payload(entity_id: int, max_players: int, view_distance: int = 10) -> bytes:
    out = bytearray()
    out += entity_id.to_bytes(4, byteorder="big", signed=True)   # Entity ID (Int)
    out += bytes([1 if _LOGIN_DATA["isHardcore"] else 0])        # Is Hardcore (bool)
    out += bytes([1])                                             # Gamemode: 1 = Creative (0=Survival, 2=Adventure!)
    out += (-1).to_bytes(1, byteorder="big", signed=True)         # Previous Gamemode: -1 = нет

    world_names = _LOGIN_DATA["worldNames"]
    out += write_varint(len(world_names))
    for name in world_names:
        out += write_string(name)

    out += _DIMENSION_CODEC_BYTES                       # Dimension Codec (NBT)
    out += write_string(_LOGIN_DATA["worldType"])        # Dimension Type (identifier string, 1.20.1-специфично)
    out += write_string(_LOGIN_DATA["worldName"])        # World Name (identifier)

    hashed_seed = _LOGIN_DATA["hashedSeed"]
    high, low = hashed_seed
    combined = ((high & 0xFFFFFFFF) << 32) | (low & 0xFFFFFFFF)
    if combined & (1 << 63):
        combined -= 1 << 64
    out += write_long(combined)                          # Hashed seed (Long)

    out += write_varint(max_players)                     # Max Players (VarInt)
    out += write_varint(view_distance)                    # View Distance (VarInt)
    out += write_varint(_LOGIN_DATA["simulationDistance"])  # Simulation Distance (VarInt)
    out += bytes([1 if _LOGIN_DATA["reducedDebugInfo"] else 0])
    out += bytes([1 if _LOGIN_DATA["enableRespawnScreen"] else 0])
    out += bytes([1 if _LOGIN_DATA["isDebug"] else 0])
    out += bytes([1 if _LOGIN_DATA["isFlat"] else 0])
    out += bytes([0])                                     # Has Death Location? (bool) - у нас нет
    out += write_varint(_LOGIN_DATA["portalCooldown"])     # Portal Cooldown (VarInt)

    return bytes(out)
