"""
Play-состояние: с этого момента игрок реально "в мире".

Фичи:
  - Креатив-режим с полётом (Player Abilities)
  - Разрушение/постройка блоков, видимые всем игрокам в реальном времени
  - Постройка ставит РЕАЛЬНЫЙ блок из руки игрока (отслеживаем хотбар через
    Held Item Change + Set Creative Mode Slot)
  - "Смерть" в пустоте под картой - на самом деле телепорт обратно на платформу
  - При выходе игрока вся его платформа стирается и становится пустотой
"""
import socket
import threading
import random
import json
import os

from protocol import (
    read_packet,
    send_packet,
    write_varint,
    write_double,
    write_float,
    write_byte,
    write_long,
    write_position,
    write_string,
)
from join_game import build_join_game_payload
from chunk_encoder import build_chunk_packet_body, BLOCK_STATE_AIR
from plots import PlotManager, PLOT_SIZE, PLOT_Y, LOBBY_Y
from nbt_reader import skip_nbt_from_packetbuffer
from nbt_writer import encode_nbt
from image_to_map import image_to_map_colors
from blockstates import compute_state_id, has_property
from block_placement import (
    compute_placement_properties,
    compute_door_placement,
    get_toggle_property,
    is_door,
    is_forbidden_block,
)
import world
import os

# --- ID пакетов (протокол 763 / 1.20.1), взяты из minecraft-data ---
CB_LOGIN = 0x28            # Join Game
CB_KEEP_ALIVE = 0x23
CB_MAP_CHUNK = 0x24        # Chunk Data and Update Light
CB_ABILITIES = 0x34
CB_ACK_BLOCK_CHANGE = 0x06
CB_GAME_STATE_CHANGE = 0x1F
CB_MAP_DATA = 0x29
CB_SET_SLOT = 0x14
CB_SYSTEM_CHAT = 0x64
CB_POSITION = 0x3C         # Player Position And Look

SB_KEEP_ALIVE = 0x12
SB_POSITION = 0x14
SB_POSITION_LOOK = 0x15
SB_LOOK = 0x16
SB_BLOCK_DIG = 0x1D
SB_HELD_ITEM_SLOT = 0x28
SB_BLOCK_PLACE = 0x31
SB_SET_CREATIVE_SLOT = 0x2B

FLINT_AND_STEEL_ITEM_ID = 758
TNT_ITEM_ID = 657
FIRE_DEFAULT_STATE = 2391
TNT_EXPLOSION_RADIUS = 3.5

RULES_TEXT = (
    "§e§lПРАВИЛА СЕРВЕРА\n"
    "§7Прочитай перед тем как начать:\n"
    "§c• §fНе взрывать чужие постройки\n"
    "§c• §fНе строить редстоун-механизмы\n"
    "§c• §fНе драться\n"
    "§c• §fНе мешать строить другим\n"
    "§a• §fСтроить вместе - можно и нужно!\n\n"
    "§b§lКликни по изумрудному блоку в полу, чтобы согласиться и начать игру."
)

VIEW_DISTANCE = 6  # в чанках - сколько чанков вокруг игрока грузим (небольшое, для скорости)
VOID_Y_THRESHOLD = -32  # ниже этого Y считаем, что игрок упал в пустоту

FACE_OFFSETS = {
    0: (0, -1, 0),  # -Y
    1: (0, 1, 0),   # +Y
    2: (0, 0, -1),  # -Z
    3: (0, 0, 1),   # +Z
    4: (-1, 0, 0),  # -X
    5: (1, 0, 0),   # +X
}

# Карта "item id -> block state id" - построена заранее из официальных
# данных Mojang (см. data/item_to_block_state.json), нужна чтобы понимать,
# какой именно блок класть, когда игрок жмёт ПКМ с предметом в руке.
_ITEM_TO_BLOCK_PATH = os.path.join(os.path.dirname(__file__), "data", "item_to_block_state.json")
with open(_ITEM_TO_BLOCK_PATH, "r", encoding="utf-8") as _f:
    ITEM_ID_TO_BLOCK_STATE = {int(k): v for k, v in json.load(_f).items()}

# Карта item_id -> имя блока (нужна для вычисления ориентации: facing/axis/half)
_ITEM_TO_BLOCK_NAME_PATH = os.path.join(os.path.dirname(__file__), "data", "item_to_block_name.json")
with open(_ITEM_TO_BLOCK_NAME_PATH, "r", encoding="utf-8") as _f:
    ITEM_ID_TO_BLOCK_NAME = {int(k): v for k, v in json.load(_f).items()}

# Карта-картина "nazarchik" - готовится один раз при старте сервера,
# чтобы не пересчитывать дизеринг для каждого игрока.
NAZARCHIK_MAP_ID = 0
_NAZAR_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "data", "nazar.jpeg")
try:
    if not os.path.exists(_NAZAR_IMAGE_PATH):
        raise FileNotFoundError(f"Файл не найден: {_NAZAR_IMAGE_PATH}")
    NAZARCHIK_MAP_COLORS = image_to_map_colors(_NAZAR_IMAGE_PATH)
    print(f"[MAP] Карта 'nazarchik' готова ({len(NAZARCHIK_MAP_COLORS)} байт)")
except Exception as e:
    import traceback
    NAZARCHIK_MAP_COLORS = None
    print(f"[WARN] Не удалось подготовить карту 'nazarchik': {e}")
    traceback.print_exc()

# Общий на весь сервер менеджер плотов - все подключения делят одно состояние.
plot_manager = PlotManager()

_next_entity_id = 1
_entity_id_lock = threading.Lock()


def _allocate_entity_id() -> int:
    global _next_entity_id
    with _entity_id_lock:
        eid = _next_entity_id
        _next_entity_id += 1
        return eid


def read_slot_item_id(pkt):
    """Читает тип 'slot' (стек предмета) и возвращает item id, либо None
    если слот пуст."""
    present = pkt.read_bool()
    if not present:
        return None
    item_id = pkt.read_varint()
    pkt.read(1)  # itemCount (i8) - не нужен
    skip_nbt_from_packetbuffer(pkt)
    return item_id


def _intersects_player(block_x: int, block_y: int, block_z: int, player_pos) -> bool:
    """Проверяет, пересекается ли блок с хитбоксом игрока (чтобы нельзя было
    заблокировать самого себя). Хитбокс игрока: ширина ~0.6, высота ~1.8."""
    px, py, pz = player_pos
    half_width = 0.3
    p_min_x, p_max_x = px - half_width, px + half_width
    p_min_y, p_max_y = py, py + 1.8
    p_min_z, p_max_z = pz - half_width, pz + half_width

    b_min_x, b_max_x = block_x, block_x + 1
    b_min_y, b_max_y = block_y, block_y + 1
    b_min_z, b_max_z = block_z, block_z + 1

    return (
        p_min_x < b_max_x and p_max_x > b_min_x
        and p_min_y < b_max_y and p_max_y > b_min_y
        and p_min_z < b_max_z and p_max_z > b_min_z
    )


def send_chunks_around(sock: socket.socket, center_chunk_x: int, center_chunk_z: int):
    for dx in range(-VIEW_DISTANCE, VIEW_DISTANCE + 1):
        for dz in range(-VIEW_DISTANCE, VIEW_DISTANCE + 1):
            cx = center_chunk_x + dx
            cz = center_chunk_z + dz
            body = build_chunk_packet_body(plot_manager, cx, cz)
            send_packet(sock, CB_MAP_CHUNK, body)


def send_abilities_creative(sock: socket.socket):
    # flags: bit0=invulnerable, bit1=flying, bit2=allow flying, bit3=creative (instant break)
    flags = 0b0001 | 0b0010 | 0b0100 | 0b1000
    payload = bytes([flags]) + write_float(0.05) + write_float(0.1)
    send_packet(sock, CB_ABILITIES, payload)


def send_force_creative_gamemode(sock: socket.socket):
    """Дублируем смену режима отдельным Game State Change (reason=3,
    'change gamemode') на случай, если клиент почему-то не подхватил
    gameMode из Join Game."""
    reason = 3  # change gamemode
    payload = bytes([reason]) + write_float(1.0)  # 1.0 = Creative
    send_packet(sock, CB_GAME_STATE_CHANGE, payload)


def send_nazarchik_map(sock: socket.socket, username: str):
    """Отправляет данные карты 'nazarchik' (Map Data) и кладёт саму карту
    игроку в последний слот хотбара (9-й, индекс инвентаря 44)."""
    if NAZARCHIK_MAP_COLORS is None:
        print(f"[MAP] Пропускаю выдачу карты для {username} - карта не была подготовлена при старте (см. WARN выше в логе)")
        return

    payload = (
        write_varint(NAZARCHIK_MAP_ID)
        + write_byte(0)      # scale
        + bytes([1])         # locked = true
        + bytes([0])         # icons: option = отсутствует
        + bytes([128])       # columns = 128
        + bytes([128])       # rows = 128
        + bytes([0])         # x = 0
        + bytes([0])         # y = 0
        + write_varint(len(NAZARCHIK_MAP_COLORS))
        + NAZARCHIK_MAP_COLORS
    )
    send_packet(sock, CB_MAP_DATA, payload)

    map_nbt = encode_nbt({"type": "compound", "value": {
        "map": {"type": "int", "value": NAZARCHIK_MAP_ID}
    }}, root_name="")

    item_payload = bytes([1]) + write_varint(941) + bytes([1]) + map_nbt  # filled_map x1
    slot_payload = write_byte(0) + write_varint(0) + (44).to_bytes(2, "big", signed=True) + item_payload
    send_packet(sock, CB_SET_SLOT, slot_payload)
    print(f"[MAP] Карта 'nazarchik' выдана {username}")


def teleport(sock: socket.socket, x: float, y: float, z: float):
    payload = (
        write_double(x) + write_double(y) + write_double(z)
        + write_float(0.0) + write_float(0.0)
        + write_byte(0) + write_varint(0)
    )
    send_packet(sock, CB_POSITION, payload)


def send_chat(sock: socket.socket, text_json: str):
    import json as _json
    content = _json.dumps({"text": text_json})
    payload = write_string(content) + bytes([0])  # isActionBar = false
    send_packet(sock, CB_SYSTEM_CHAT, payload)


def _keep_alive_loop(sock: socket.socket, stop_event: threading.Event):
    while not stop_event.is_set():
        if stop_event.wait(timeout=10):
            return
        try:
            keep_id = random.randint(0, 2**63 - 1)
            send_packet(sock, CB_KEEP_ALIVE, write_long(keep_id))
        except OSError:
            return


def _explode_tnt(x: int, y: int, z: int):
    """Поджигает и взрывает тротил: короткая задержка ("фитиль"), затем
    очищает сферическую область вокруг взрыва. Упрощение: без урона игрокам,
    без разлетающихся предметов, без цепной детонации соседнего тротила -
    полноценная физика взрыва потребовала бы отдельной системы сущностей."""
    world.set_override(x, y, z, FIRE_DEFAULT_STATE)
    world.broadcast_block_change(x, y, z, FIRE_DEFAULT_STATE)

    def _detonate():
        r = TNT_EXPLOSION_RADIUS
        r_int = int(r) + 1
        for dx in range(-r_int, r_int + 1):
            for dy in range(-r_int, r_int + 1):
                for dz in range(-r_int, r_int + 1):
                    if dx * dx + dy * dy + dz * dz > r * r:
                        continue
                    bx, by, bz = x + dx, y + dy, z + dz
                    world.set_override(bx, by, bz, BLOCK_STATE_AIR)
                    world.clear_functional_block(bx, by, bz)
                    world.broadcast_block_change(bx, by, bz, BLOCK_STATE_AIR)

    timer = threading.Timer(1.2, _detonate)
    timer.daemon = True
    timer.start()


def _wipe_plot(plot, username: str):
    """Полностью стирает платформу игрока (постройки + саму базу) и
    рассылает это всем подключённым, чтобы платформа реально исчезла у всех."""
    min_x = plot.origin_x
    max_x = plot.origin_x + PLOT_SIZE - 1
    min_z = plot.origin_z
    max_z = plot.origin_z + PLOT_SIZE - 1

    cleared = world.clear_region(min_x, max_x, min_z, max_z)
    for (x, y, z) in cleared:
        world.broadcast_block_change(x, y, z, BLOCK_STATE_AIR)

    plot_manager.release(username)
    world.broadcast_air_region(min_x, max_x, min_z, max_z, PLOT_Y)


def handle_play(sock: socket.socket, addr, username: str, uuid_bytes: bytes):
    entity_id = _allocate_entity_id()
    plot = plot_manager.get_or_assign(username)

    # Игрок сначала попадает в стеклянную комнату регистрации высоко в небе,
    # а не сразу на платформу - должен прочитать правила и согласиться.
    lobby_x, lobby_y, lobby_z = plot.lobby_spawn_position()
    spawn_x, spawn_y, spawn_z = plot.spawn_position()
    chunk_x = int(lobby_x) // 16
    chunk_z = int(lobby_z) // 16
    registered = [False]  # пока False - ломать/строить нельзя нигде

    # Состояние хотбара игрока: какой слот выбран и что в каждом из 9 слотов
    selected_slot = 0
    hotbar_items: dict[int, int] = {}  # slot(0-8) -> item_id
    player_pos = [lobby_x, lobby_y, lobby_z]  # обновляется по мере движения - нужно для проверки коллизии при постройке
    player_yaw = [0.0]
    player_pitch = [0.0]

    print(f"[PLAY] {username} ({addr}) заходит в игру, entity_id={entity_id}, "
          f"плот #{plot.index}, комната регистрации=({lobby_x:.1f}, {lobby_y}, {lobby_z:.1f})")

    join_payload = build_join_game_payload(entity_id=entity_id, max_players=20, view_distance=VIEW_DISTANCE)
    send_packet(sock, CB_LOGIN, join_payload)

    send_abilities_creative(sock)
    send_force_creative_gamemode(sock)
    send_nazarchik_map(sock, username)

    send_chunks_around(sock, chunk_x, chunk_z)
    teleport(sock, lobby_x, lobby_y, lobby_z)
    send_chat(sock, RULES_TEXT)

    world.register_connection(username, sock)
    # Показываем игрока всем остальным (и наоборот) - это чинит баг с
    # невидимыми игроками, которого не было бы, если бы мы раньше отправляли
    # Player Info + Spawn Player.
    world.register_player(username, uuid_bytes, entity_id, sock, lobby_x, lobby_y, lobby_z)

    # Чинит баг "не видно платформу другого игрока": все, кто уже был
    # подключён и чьи чанки в этой области уже отправлены (и теперь навсегда
    # останутся пустыми без этого), получают блоки новой платформы явно.
    world.broadcast_new_plot(plot, exclude_username=username)

    stop_event = threading.Event()
    ka_thread = threading.Thread(target=_keep_alive_loop, args=(sock, stop_event), daemon=True)
    ka_thread.start()

    try:
        while True:
            pkt = read_packet(sock)
            packet_id = pkt.read_varint()

            if packet_id == SB_KEEP_ALIVE:
                continue

            if packet_id == SB_POSITION:
                x = pkt.read_double()
                y = pkt.read_double()
                z = pkt.read_double()
                player_pos[0], player_pos[1], player_pos[2] = x, y, z
                world.update_player_position(username, x, y, z, player_yaw[0], player_pitch[0])
                if y < VOID_Y_THRESHOLD:
                    target = (spawn_x, spawn_y, spawn_z) if registered[0] else (lobby_x, lobby_y, lobby_z)
                    print(f"[VOID] {username} упал в пустоту, возвращаю обратно")
                    teleport(sock, *target)
                continue

            if packet_id == SB_POSITION_LOOK:
                x = pkt.read_double()
                y = pkt.read_double()
                z = pkt.read_double()
                yaw = pkt.read_float()
                pitch = pkt.read_float()
                player_pos[0], player_pos[1], player_pos[2] = x, y, z
                player_yaw[0], player_pitch[0] = yaw, pitch
                world.update_player_position(username, x, y, z, yaw, pitch)
                if y < VOID_Y_THRESHOLD:
                    target = (spawn_x, spawn_y, spawn_z) if registered[0] else (lobby_x, lobby_y, lobby_z)
                    print(f"[VOID] {username} упал в пустоту, возвращаю обратно")
                    teleport(sock, *target)
                continue

            if packet_id == SB_LOOK:
                yaw = pkt.read_float()
                pitch = pkt.read_float()
                player_yaw[0], player_pitch[0] = yaw, pitch
                world.update_player_look(username, yaw, pitch)
                continue

            if packet_id == SB_HELD_ITEM_SLOT:
                slot_id = pkt.read(2)
                selected_slot = int.from_bytes(slot_id, byteorder="big", signed=True)
                continue

            if packet_id == SB_SET_CREATIVE_SLOT:
                slot = pkt.read(2)
                slot_index = int.from_bytes(slot, byteorder="big", signed=True)
                item_id = read_slot_item_id(pkt)
                # Слоты хотбара в инвентаре игрока - индексы 36..44 (0..8 хотбар)
                if 36 <= slot_index <= 44:
                    hotbar_slot = slot_index - 36
                    if item_id is None:
                        hotbar_items.pop(hotbar_slot, None)
                    else:
                        hotbar_items[hotbar_slot] = item_id
                continue

            if packet_id == SB_BLOCK_DIG:
                status = pkt.read_varint()
                bx, by, bz = pkt.read_position()
                pkt.read(1)  # face
                sequence = pkt.read_varint()

                if not registered[0]:
                    # Пока не согласился с правилами - ломать нельзя вообще,
                    # кроме клика по изумрудному блоку (это и есть "согласен").
                    if status == 0 and (bx, by, bz) == plot.agree_block_position():
                        registered[0] = True
                        print(f"[REGISTER] {username} согласился с правилами")
                        send_chat(sock, "§a✔ Спасибо! Добро пожаловать на платформу.")
                        teleport(sock, spawn_x, spawn_y, spawn_z)
                        player_pos[0], player_pos[1], player_pos[2] = spawn_x, spawn_y, spawn_z
                    send_packet(sock, CB_ACK_BLOCK_CHANGE, write_varint(sequence))
                    continue

                if status == 0:
                    existing = world.get_functional_block(bx, by, bz)
                    world.set_override(bx, by, bz, BLOCK_STATE_AIR)
                    world.clear_functional_block(bx, by, bz)
                    world.broadcast_block_change(bx, by, bz, BLOCK_STATE_AIR)

                    # Двери - 2 связанных блока: если сломали одну половину,
                    # вторая тоже должна исчезнуть (иначе "висит" в воздухе).
                    if existing is not None and is_door(existing[0]):
                        other_y = by + 1 if existing[1].get("half") == "lower" else by - 1
                        other = world.get_functional_block(bx, other_y, bz)
                        if other is not None and other[0] == existing[0]:
                            world.set_override(bx, other_y, bz, BLOCK_STATE_AIR)
                            world.clear_functional_block(bx, other_y, bz)
                            world.broadcast_block_change(bx, other_y, bz, BLOCK_STATE_AIR)
                send_packet(sock, CB_ACK_BLOCK_CHANGE, write_varint(sequence))
                continue

            if packet_id == SB_BLOCK_PLACE:
                pkt.read_varint()  # hand
                bx, by, bz = pkt.read_position()
                direction = pkt.read_varint()
                cursor_x = pkt.read_float()
                cursor_y = pkt.read_float()
                cursor_z = pkt.read_float()
                pkt.read_bool()   # insideBlock
                sequence = pkt.read_varint()

                if not registered[0]:
                    # До согласия с правилами строить нельзя вообще.
                    send_packet(sock, CB_ACK_BLOCK_CHANGE, write_varint(sequence))
                    continue

                # --- Огниво: поджигает тротил (если целимся в TNT) или
                # просто ставит огонь на грани блока, как в ванили. ---
                held_item_id = hotbar_items.get(selected_slot)
                if held_item_id == FLINT_AND_STEEL_ITEM_ID:
                    target_block_name = None
                    target_override = world.get_override(bx, by, bz)
                    # у нас нет обратного маппинга state->имя для НЕ-функциональных
                    # блоков, но TNT всегда кладём как функциональный маркер не нужен -
                    # проверяем по числовому состоянию через block_placement.
                    from blockstates import BLOCK_BY_NAME
                    tnt_state = BLOCK_BY_NAME["tnt"]["defaultState"]
                    if target_override == tnt_state:
                        _explode_tnt(bx, by, bz)
                    else:
                        dx, dy, dz = FACE_OFFSETS.get(direction, (0, 0, 0))
                        fx, fy, fz = bx + dx, by + dy, bz + dz
                        if not _intersects_player(fx, fy, fz, player_pos):
                            world.set_override(fx, fy, fz, FIRE_DEFAULT_STATE)
                            world.broadcast_block_change(fx, fy, fz, FIRE_DEFAULT_STATE)
                    send_packet(sock, CB_ACK_BLOCK_CHANGE, write_varint(sequence))
                    continue

                # --- Клик по УЖЕ существующему интерактивному блоку
                # (дверь/люк/калитка)? Тогда переключаем его, а не ставим новый. ---
                existing = world.get_functional_block(bx, by, bz)
                handled_as_toggle = False
                if existing is not None:
                    block_name, props = existing
                    toggle_prop = get_toggle_property(block_name)
                    if toggle_prop is not None:
                        props[toggle_prop] = not props.get(toggle_prop, False)
                        new_state = compute_state_id(block_name, **props)
                        world.set_override(bx, by, bz, new_state)
                        world.set_functional_block(bx, by, bz, block_name, props)
                        world.broadcast_block_change(bx, by, bz, new_state)

                        # Дверь - две половинки должны открываться синхронно
                        if is_door(block_name):
                            other_y = by + 1 if props.get("half") == "lower" else by - 1
                            other = world.get_functional_block(bx, other_y, bz)
                            if other is not None and other[0] == block_name:
                                other_props = other[1]
                                other_props[toggle_prop] = props[toggle_prop]
                                other_state = compute_state_id(block_name, **other_props)
                                world.set_override(bx, other_y, bz, other_state)
                                world.set_functional_block(bx, other_y, bz, block_name, other_props)
                                world.broadcast_block_change(bx, other_y, bz, other_state)
                        handled_as_toggle = True

                if not handled_as_toggle:
                    dx, dy, dz = FACE_OFFSETS.get(direction, (0, 0, 0))
                    px, py, pz = bx + dx, by + dy, bz + dz

                    item_id = hotbar_items.get(selected_slot)
                    block_name = ITEM_ID_TO_BLOCK_NAME.get(item_id) if item_id is not None else None

                    if block_name is not None and is_forbidden_block(block_name):
                        block_name = None  # запрещённый блок (редстоун/рельсы) - тихо игнорируем

                    if block_name is not None and not _intersects_player(px, py, pz, player_pos):
                        if is_door(block_name):
                            lower_props, upper_props = compute_door_placement(
                                block_name, player_yaw[0], cursor_x, cursor_z)
                            if not _intersects_player(px, py + 1, pz, player_pos):
                                lower_state = compute_state_id(block_name, **lower_props)
                                upper_state = compute_state_id(block_name, **upper_props)
                                world.set_override(px, py, pz, lower_state)
                                world.set_override(px, py + 1, pz, upper_state)
                                world.set_functional_block(px, py, pz, block_name, lower_props)
                                world.set_functional_block(px, py + 1, pz, block_name, upper_props)
                                world.broadcast_block_change(px, py, pz, lower_state)
                                world.broadcast_block_change(px, py + 1, pz, upper_state)
                        else:
                            props = compute_placement_properties(
                                block_name, player_yaw[0], direction, cursor_x, cursor_y, cursor_z)
                            block_state = compute_state_id(block_name, **props)
                            world.set_override(px, py, pz, block_state)
                            if get_toggle_property(block_name) is not None:
                                world.set_functional_block(px, py, pz, block_name, props)
                            world.broadcast_block_change(px, py, pz, block_state)
                    # если в руке пусто, предмет без блочного эквивалента, или
                    # блок пересекается с игроком - просто ничего не ставим
                send_packet(sock, CB_ACK_BLOCK_CHANGE, write_varint(sequence))
                continue

            # остальные пакеты (чат и т.д.) пока игнорируем

    except (ConnectionError, EOFError):
        print(f"[PLAY] {username} отключился")
    finally:
        stop_event.set()
        world.unregister_connection(username)
        world.unregister_player(username)
        _wipe_plot(plot, username)
        print(f"[PLAY] Платформа {username} (#{plot.index}) стёрта и освобождена")

