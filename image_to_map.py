"""
Конвертирует произвольное изображение в 128x128 массив map_color_id,
готовый для отправки в Map Data пакете. Использует дизеринг
Флойда-Стейнберга для лучшей передачи оттенков ограниченной палитрой.
"""
from PIL import Image
from map_colors import PALETTE, closest_map_color_id


def image_to_map_colors(image_path: str) -> bytes:
    img = Image.open(image_path).convert("RGB")
    # Приводим к квадрату 128x128 (обрезаем по центру, затем масштабируем)
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((128, 128), Image.LANCZOS)

    pixels = [[list(img.getpixel((x, y))) for x in range(128)] for y in range(128)]
    output = bytearray(128 * 128)

    for y in range(128):
        for x in range(128):
            r, g, b = pixels[y][x]
            r = max(0, min(255, round(r)))
            g = max(0, min(255, round(g)))
            b = max(0, min(255, round(b)))

            map_id = closest_map_color_id(r, g, b)
            # найдём фактический цвет этого id для расчёта ошибки дизеринга
            actual = next(p for p in PALETTE if p[0] == map_id)
            _, ar, ag, ab = actual

            output[y * 128 + x] = map_id

            err_r = r - ar
            err_g = g - ag
            err_b = b - ab

            # Floyd-Steinberg: разносим ошибку на соседние ещё не обработанные пиксели
            def add_error(px, py, factor):
                if 0 <= px < 128 and 0 <= py < 128:
                    pixels[py][px][0] += err_r * factor
                    pixels[py][px][1] += err_g * factor
                    pixels[py][px][2] += err_b * factor

            add_error(x + 1, y, 7 / 16)
            add_error(x - 1, y + 1, 3 / 16)
            add_error(x, y + 1, 5 / 16)
            add_error(x + 1, y + 1, 1 / 16)

    return bytes(output)
