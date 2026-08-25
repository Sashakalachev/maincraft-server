"""
Палитра цветов Minecraft-карт для протокола 763 (1.20.1).

Данные взяты из data/map_colors_1_20_1.json - точной копии официальной
таблицы (62 базовых цвета x 4 оттенка = до 248 итоговых цветов),
извлечённой проектом cerus/minecraft-map-colors.

map_color_id (0-255, как в байте карты) = base_index*4 + shade_index (0-3).
id=0..3 - служебный "прозрачный" цвет (база 0), не используем для картинок.
"""
import json
import os

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "map_colors_1_20_1.json")

with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW = json.load(_f)

# palette: список (map_color_id, r, g, b), без прозрачного (base=0)
PALETTE: list[tuple[int, int, int, int]] = []

for base_index_str, entry in _RAW.items():
    base_index = int(base_index_str)
    if base_index == 0:
        continue  # прозрачный - пропускаем, для фото не подходит
    for shade_index, packed_rgb in enumerate(entry["colors"]):
        r = (packed_rgb >> 16) & 0xFF
        g = (packed_rgb >> 8) & 0xFF
        b = packed_rgb & 0xFF
        map_color_id = base_index * 4 + shade_index
        PALETTE.append((map_color_id, r, g, b))


def closest_map_color_id(r: int, g: int, b: int) -> int:
    """Находит ближайший (по евклидову расстоянию в RGB) цвет из палитры карт."""
    best_id = PALETTE[0][0]
    best_dist = float("inf")
    for map_id, pr, pg, pb in PALETTE:
        dist = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if dist < best_dist:
            best_dist = dist
            best_id = map_id
    return best_id
