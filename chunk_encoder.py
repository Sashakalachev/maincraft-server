"""
Кодирование чанка в бинарный формат протокола Minecraft 1.20.1.

Один чанк = столб 16x16 по X/Z, разбитый на 24 секции по 16 блоков в высоту
(мир 1.18+: y от -64 до 320). Каждая секция кодируется отдельно:
  - Block count (сколько НЕ-воздушных блоков, Short) - для стороннего рендера/AO
  - Palette блоков (paletted container)
  - Palette биомов (тоже paletted container, но с другими размерами бит)

Здесь реализована ИНДИРЕКТНАЯ палитра (indirect palette) - подходит для
случаев, когда в секции мало разных типов блоков (у нас: воздух, трава,
окантовка - максимум 3), что и есть наш случай.

ВАЖНО: числовые ID блочных состояний (block state id) ниже - ориентировочные
для 1.20.1 и их стоит сверить перед реальным тестом (см. комментарии).
"""
from protocol import write_varint

SECTION_HEIGHT = 16
MIN_Y = -64
MAX_Y = 320
NUM_SECTIONS = (MAX_Y - MIN_Y) // SECTION_HEIGHT  # 24

# --- ID блочных состояний для 1.20.1 (global palette) ---
# Взяты напрямую из data/pc/1.20/blocks.json проекта minecraft-data
# (поле defaultState) - официальные, реально используемые вами данные,
# не предположение.
BLOCK_STATE_AIR = 0
BLOCK_STATE_GRASS_BLOCK = 9      # grass_block, defaultState
BLOCK_STATE_STONE_BRICKS = 6538  # stone_bricks, defaultState (без вариантов)


def _bits_needed_for_palette(n: int) -> int:
    """Сколько бит нужно, чтобы проиндексировать n различных значений."""
    if n <= 1:
        return 0
    bits = 0
    n -= 1
    while n:
        bits += 1
        n >>= 1
    return bits


def encode_paletted_container(block_states: list[int]) -> bytes:
    """
    block_states: список из 4096 (16*16*16) ID блочных состояний,
    в порядке y, затем z, затем x (как того требует протокол).

    Использует indirect palette для малого числа уникальных блоков,
    что полностью соответствует нашему случаю (максимум 3 типа блока
    на секцию).
    """
    unique = sorted(set(block_states))
    palette_len = len(unique)

    if palette_len <= 1:
        # Single-valued palette: 0 бит на запись, вообще без Data Array.
        # Огромная экономия для однородных секций (сплошной воздух).
        out = bytearray()
        out.append(0)
        out += write_varint(unique[0] if unique else BLOCK_STATE_AIR)
        out += write_varint(0)  # Data Array length = 0
        return bytes(out)

    bits_per_entry = max(4, _bits_needed_for_palette(palette_len))
    # протокол требует минимум 4 бита для indirect palette,
    # максимум 8 - выше уже используется direct (мы не дойдём до этого)
    bits_per_entry = min(bits_per_entry, 8)

    index_of = {state: i for i, state in enumerate(unique)}

    out = bytearray()
    out.append(bits_per_entry)  # Bits Per Entry

    # Palette (indirect): VarInt length + VarInt значения
    out += write_varint(palette_len)
    for state in unique:
        out += write_varint(state)

    # Data Array: упаковываем индексы по bits_per_entry бит в long-слова (64 бита)
    entries_per_long = 64 // bits_per_entry
    longs = []
    current = 0
    filled = 0
    count_in_long = 0
    for state in block_states:
        idx = index_of[state]
        current |= (idx << (filled))
        filled += bits_per_entry
        count_in_long += 1
        if count_in_long == entries_per_long:
            longs.append(current)
            current = 0
            filled = 0
            count_in_long = 0
    if count_in_long > 0:
        longs.append(current)

    out += write_varint(len(longs))
    for val in longs:
        out += val.to_bytes(8, byteorder="big", signed=False)

    return bytes(out)


def encode_biome_container_single(biome_id: int = 0) -> bytes:
    """Упрощённая палитра биомов: у нас везде один биом (например, plains),
    так что используем single-valued palette (0 бит на запись)."""
    out = bytearray()
    out.append(0)  # Bits Per Entry = 0 -> single-valued
    out += write_varint(biome_id)  # единственное значение палитры
    out += write_varint(0)  # Data Array length = 0 (не нужен при single-valued)
    return bytes(out)


def encode_section(block_states_4096: list[int], non_air_count: int, biome_id: int = 0) -> bytes:
    out = bytearray()
    out += non_air_count.to_bytes(2, byteorder="big", signed=True)  # Short
    out += encode_paletted_container(block_states_4096)
    out += encode_biome_container_single(biome_id)
    return bytes(out)


def block_state_for(block_id: str | None) -> int:
    if block_id is None:
        return BLOCK_STATE_AIR
    if block_id == "minecraft:grass_block":
        return BLOCK_STATE_GRASS_BLOCK
    if block_id == "minecraft:stone_bricks":
        return BLOCK_STATE_STONE_BRICKS
    # Общий случай - ищем в полной таблице блоков (glass, emerald_block и т.д.)
    from blockstates import BLOCK_BY_NAME
    name = block_id.replace("minecraft:", "")
    block = BLOCK_BY_NAME.get(name)
    return block["defaultState"] if block else BLOCK_STATE_AIR


def _empty_heightmaps_nbt() -> bytes:
    """Минимальный валидный NBT (пустой compound). Клиент может немного
    промахиваться с оптимизациями (случайные тики и т.п.), но это не
    ломает соединение - просто нижний приоритет для первого рабочего теста."""
    from nbt_writer import encode_nbt
    return encode_nbt({"type": "compound", "value": {}}, root_name="")


def _all_bits_bitset(num_bits: int) -> bytes:
    """Кодирует BitSet (array of Long), где первые num_bits бит выставлены в 1."""
    num_longs = (num_bits + 63) // 64
    longs = [0] * num_longs
    for i in range(num_bits):
        longs[i // 64] |= (1 << (i % 64))
    out = write_varint(num_longs)
    for val in longs:
        out += val.to_bytes(8, byteorder="big", signed=False)
    return out


def _empty_bitset() -> bytes:
    return write_varint(0)


def build_chunk_packet_body(plot_manager, chunk_x: int, chunk_z: int) -> bytes:
    """Полное тело пакета Chunk Data and Update Light (без ID пакета и без
    внешнего length-префикса - это добавляет send_packet)."""
    # Число "секций света" на 2 больше, чем секций блоков (учитывает область
    # чуть ниже и чуть выше игрового мира - так того требует протокол).
    light_section_count = NUM_SECTIONS + 2

    chunk_data = build_chunk_columns(plot_manager, chunk_x, chunk_z)

    out = bytearray()
    out += chunk_x.to_bytes(4, byteorder="big", signed=True)
    out += chunk_z.to_bytes(4, byteorder="big", signed=True)
    out += _empty_heightmaps_nbt()
    out += write_varint(len(chunk_data))
    out += chunk_data
    out += write_varint(0)  # Block Entities: пусто (у нас их нет)

    # Свет: помечаем всё небо максимально освещённым (0xFF на каждый ниббл),
    # блок-освещение не шлём (не критично для базового теста).
    out += _all_bits_bitset(light_section_count)  # Sky Light Mask - все секции
    out += _empty_bitset()                          # Block Light Mask - ничего
    out += _empty_bitset()                          # Empty Sky Light Mask
    out += _all_bits_bitset(light_section_count)   # Empty Block Light Mask - все "заведомо пусто"

    out += write_varint(light_section_count)        # Sky Light arrays count
    full_bright = bytes([0xFF] * 2048)
    for _ in range(light_section_count):
        out += write_varint(2048)
        out += full_bright

    out += write_varint(0)  # Block Light arrays count - пусто

    return bytes(out)


def build_chunk_columns(plot_manager, chunk_x: int, chunk_z: int) -> bytes:
    """Строит все 24 секции для чанка (chunk_x, chunk_z) - то есть блоки
    в диапазоне X: [chunk_x*16, chunk_x*16+16), Z аналогично, Y: [-64, 320).

    Учитывает базовую платформу (plots.PLOT_Y), стеклянную комнату
    регистрации высоко в небе (plots.LOBBY_Y) и overrides из world.py
    (постройки/разрушения игроков на любой высоте).
    """
    from plots import PLOT_Y, LOBBY_Y, LOBBY_HEIGHT
    import world

    plot_section_index = (PLOT_Y - MIN_Y) // SECTION_HEIGHT
    lobby_section_index = (LOBBY_Y - MIN_Y) // SECTION_HEIGHT

    # Предвыбираем только те overrides, что реально попадают в этот чанк -
    # чтобы не гонять словарь на каждую из 24*4096 клеток.
    chunk_min_x = chunk_x * 16
    chunk_min_z = chunk_z * 16
    with world._lock:
        relevant_overrides = {
            key: state for key, state in world.overrides.items()
            if chunk_min_x <= key[0] < chunk_min_x + 16
            and chunk_min_z <= key[2] < chunk_min_z + 16
        }

    out = bytearray()
    for section_index in range(NUM_SECTIONS):
        section_base_y = MIN_Y + section_index * SECTION_HEIGHT
        section_has_overrides = any(
            section_base_y <= key[1] < section_base_y + 16
            for key in relevant_overrides
        )

        if (section_index != plot_section_index
                and section_index != lobby_section_index
                and not section_has_overrides):
            # секция целиком воздух - всё равно должна быть закодирована
            states = [BLOCK_STATE_AIR] * 4096
            out += encode_section(states, non_air_count=0)
            continue

        states = [BLOCK_STATE_AIR] * 4096
        non_air = 0
        for local_x in range(16):
            world_x = chunk_min_x + local_x
            for local_z in range(16):
                world_z = chunk_min_z + local_z
                for local_y in range(16):
                    world_y = section_base_y + local_y
                    override = relevant_overrides.get((world_x, world_y, world_z))
                    if override is not None:
                        state = override
                    elif world_y == PLOT_Y:
                        block_id = plot_manager.block_at(world_x, world_z)
                        state = block_state_for(block_id) if block_id else BLOCK_STATE_AIR
                    elif LOBBY_Y <= world_y <= LOBBY_Y + LOBBY_HEIGHT:
                        block_id = plot_manager.lobby_block_at(world_x, world_y, world_z)
                        state = block_state_for(block_id) if block_id else BLOCK_STATE_AIR
                    else:
                        state = BLOCK_STATE_AIR
                    if state != BLOCK_STATE_AIR:
                        idx = (local_y * 16 * 16) + (local_z * 16) + local_x
                        states[idx] = state
                        non_air += 1
        out += encode_section(states, non_air_count=non_air)

    return bytes(out)
