"""
Ориентация блоков при постройке + интерактивные (переключаемые) блоки.

facing вычисляется по yaw игрока через официальную формулу Minecraft
(Entity#getDirection): idx = floor(yaw/90 + 0.5) & 3, порядок [south,
west, north, east]. "facing" блока - это направление, куда блок обращён
к игроку, т.е. ПРОТИВОПОЛОЖНОЕ направлению взгляда игрока.
"""
import math
from blockstates import BLOCK_BY_NAME, has_property, compute_state_id

_HORIZONTAL_ORDER = ["south", "west", "north", "east"]
_OPPOSITE = {"south": "north", "north": "south", "west": "east", "east": "west"}

# Суффиксы/имена блоков, которые реагируют на ПКМ переключением состояния,
# а не заменой блока. Значение - имя свойства, которое инвертируется.
_TOGGLE_SUFFIXES = {
    "_door": "open",
    "_trapdoor": "open",
    "_fence_gate": "open",
    "_button": "powered",
}
_TOGGLE_EXACT = {
    "lever": "powered",
}


def yaw_to_look_direction(yaw: float) -> str:
    idx = math.floor(yaw / 90.0 + 0.5) & 3
    return _HORIZONTAL_ORDER[idx]


def yaw_to_facing(yaw: float) -> str:
    """Направление, в которое должен "смотреть" блок (лицом к игроку)."""
    return _OPPOSITE[yaw_to_look_direction(yaw)]


def axis_from_face_direction(direction: int) -> str:
    if direction in (0, 1):
        return "y"
    if direction in (2, 3):
        return "z"
    return "x"


def get_toggle_property(block_name: str) -> str | None:
    if block_name in _TOGGLE_EXACT:
        return _TOGGLE_EXACT[block_name]
    for suffix, prop in _TOGGLE_SUFFIXES.items():
        if block_name.endswith(suffix):
            return prop
    return None


# --- Блоки, которые запрещено ставить (редстоун-механизмы, рельсы) ---
# Двери/люки/калитки НЕ входят сюда - они переключаются напрямую игроком,
# а не сигналом редстоуна, поэтому остаются разрешены.
_FORBIDDEN_KEYWORDS = ("redstone", "piston", "repeater", "comparator",
                       "observer", "rail", "tripwire", "button", "lever",
                       "daylight_detector", "target", "lectern")


def is_forbidden_block(block_name: str) -> bool:
    return any(keyword in block_name for keyword in _FORBIDDEN_KEYWORDS)


def is_door(block_name: str) -> bool:
    return block_name.endswith("_door") and has_property(block_name, "hinge")


def compute_placement_properties(block_name: str, yaw: float, direction: int,
                                  cursor_x: float, cursor_y: float, cursor_z: float) -> dict:
    """Вычисляет только те свойства, которые реально есть у этого блока."""
    props = {}

    if has_property(block_name, "axis"):
        props["axis"] = axis_from_face_direction(direction)
        return props  # брёвна/столбы - axis, больше ничего не нужно

    if has_property(block_name, "facing"):
        props["facing"] = yaw_to_facing(yaw)

    if has_property(block_name, "half") and has_property(block_name, "shape"):
        # лестницы: half по тому, в верхнюю или нижнюю половину блока кликнули
        props["half"] = "top" if cursor_y > 0.5 else "bottom"

    if has_property(block_name, "type") and not has_property(block_name, "shape"):
        # плиты (slab): type top/bottom (double - отдельная логика, не делаем)
        props["type"] = "top" if cursor_y > 0.5 else "bottom"

    if has_property(block_name, "open"):
        props["open"] = False
    if has_property(block_name, "powered"):
        props["powered"] = False

    return props


def compute_door_placement(block_name: str, yaw: float, cursor_x: float, cursor_z: float) -> tuple[dict, dict]:
    """Возвращает (свойства_нижней_половины, свойства_верхней_половины)."""
    facing = yaw_to_facing(yaw)
    # Упрощённая эвристика для hinge (в ваниле учитывается наличие соседних
    # блоков - здесь используем позицию клика как приближение).
    hinge = "left" if cursor_x < 0.5 else "right"

    lower = {"facing": facing, "half": "lower", "hinge": hinge, "open": False, "powered": False}
    upper = {"facing": facing, "half": "upper", "hinge": hinge, "open": False, "powered": False}
    return lower, upper
