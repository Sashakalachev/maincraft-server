"""
Общее (на весь сервер) состояние мира и подключённых игроков.

- overrides: блоки, которые игроки построили/сломали поверх базовой платформы.
  Хранится как {(x, y, z): block_state_id}. Если координаты нет в overrides -
  берётся базовый блок платформы (трава/окантовка) или воздух.
- connections: реестр активных подключений для рассылки изменений блоков
  всем игрокам (чтобы все видели чужие постройки в реальном времени).
- players: реестр игроков-СУЩНОСТЕЙ (entity) - нужен, чтобы игроки видели
  друг друга (модельки), а не только чужие блоки.
"""
import threading
import socket

from protocol import send_packet, write_position, write_varint, write_double, write_byte

CB_BLOCK_CHANGE = 0x0A
CB_PLAYER_INFO = 0x3A
CB_PLAYER_REMOVE = 0x39
CB_NAMED_ENTITY_SPAWN = 0x03
CB_ENTITY_TELEPORT = 0x68
CB_ENTITY_DESTROY = 0x3E
CB_ENTITY_HEAD_ROTATION = 0x42

_lock = threading.Lock()
overrides: dict[tuple[int, int, int], int] = {}
connections: dict[str, socket.socket] = {}
players: dict[str, dict] = {}  # username -> {uuid, entity_id, sock, x, y, z}
functional_blocks: dict[tuple[int, int, int], dict] = {}  # (x,y,z) -> {"name": str, "props": dict}


def set_functional_block(x: int, y: int, z: int, block_name: str, props: dict):
    with _lock:
        functional_blocks[(x, y, z)] = {"name": block_name, "props": dict(props)}


def get_functional_block(x: int, y: int, z: int):
    with _lock:
        entry = functional_blocks.get((x, y, z))
        return None if entry is None else (entry["name"], dict(entry["props"]))


def clear_functional_block(x: int, y: int, z: int):
    with _lock:
        functional_blocks.pop((x, y, z), None)


def set_override(x: int, y: int, z: int, state: int):
    with _lock:
        overrides[(x, y, z)] = state


def get_override(x: int, y: int, z: int):
    with _lock:
        return overrides.get((x, y, z))


def clear_region(min_x: int, max_x: int, min_z: int, max_z: int):
    """Удаляет все overrides в диапазоне X/Z (любой Y) - используется при
    выходе игрока, чтобы стереть все его постройки."""
    with _lock:
        keys_to_remove = [
            key for key in overrides
            if min_x <= key[0] <= max_x and min_z <= key[2] <= max_z
        ]
        for key in keys_to_remove:
            del overrides[key]
        functional_keys = [
            key for key in functional_blocks
            if min_x <= key[0] <= max_x and min_z <= key[2] <= max_z
        ]
        for key in functional_keys:
            del functional_blocks[key]
        return keys_to_remove


def register_connection(username: str, sock: socket.socket):
    with _lock:
        connections[username] = sock


def unregister_connection(username: str):
    with _lock:
        connections.pop(username, None)


def broadcast_block_change(x: int, y: int, z: int, block_state: int, exclude_username: str | None = None):
    payload = write_position(x, y, z) + write_varint(block_state)
    with _lock:
        targets = list(connections.items())
    for username, sock in targets:
        if username == exclude_username:
            continue
        try:
            send_packet(sock, CB_BLOCK_CHANGE, payload)
        except OSError:
            pass  # соединение уже мертво, само подчистится при выходе того игрока


def broadcast_air_region(min_x: int, max_x: int, min_z: int, max_z: int, y: int):
    """Рассылает всем воздух для целого слоя (используется, чтобы стереть
    базовую платформу при выходе игрока)."""
    from chunk_encoder import BLOCK_STATE_AIR
    with _lock:
        targets = list(connections.items())
    for x in range(min_x, max_x + 1):
        for z in range(min_z, max_z + 1):
            payload = write_position(x, y, z) + write_varint(BLOCK_STATE_AIR)
            for username, sock in targets:
                try:
                    send_packet(sock, CB_BLOCK_CHANGE, payload)
                except OSError:
                    pass


def broadcast_new_plot(plot, exclude_username: str | None = None):
    """Рассылает уже подключённым игрокам блоки новой платформы, которая
    появилась ПОСЛЕ того, как их чанки в этой области уже были отправлены
    (иначе у них эта область так и останется пустотой навсегда)."""
    from chunk_encoder import block_state_for
    from plots import PLOT_SIZE, PLOT_Y
    with _lock:
        targets = list(connections.items())
    for local_x in range(PLOT_SIZE):
        world_x = plot.origin_x + local_x
        for local_z in range(PLOT_SIZE):
            world_z = plot.origin_z + local_z
            block_id = plot.block_at(world_x, world_z)
            if block_id is None:
                continue
            state = block_state_for(block_id)
            payload = write_position(world_x, PLOT_Y, world_z) + write_varint(state)
            for username, sock in targets:
                if username == exclude_username:
                    continue
                try:
                    send_packet(sock, CB_BLOCK_CHANGE, payload)
                except OSError:
                    pass


# --- Видимость игроков друг для друга (Player Info + Spawn Player) ---

def _player_info_add_payload(username: str, uuid_bytes: bytes) -> bytes:
    action_flags = 0x01  # только add_player
    entry = uuid_bytes + write_varint(len(username)) + username.encode("utf-8") + write_varint(0)  # game_profile: name + 0 properties
    return bytes([action_flags]) + write_varint(1) + entry


def _spawn_player_payload(entity_id: int, uuid_bytes: bytes, x: float, y: float, z: float) -> bytes:
    yaw_byte = 0
    pitch_byte = 0
    return (
        write_varint(entity_id) + uuid_bytes
        + write_double(x) + write_double(y) + write_double(z)
        + write_byte(yaw_byte) + write_byte(pitch_byte)
    )


def register_player(username: str, uuid_bytes: bytes, entity_id: int, sock: socket.socket, x: float, y: float, z: float):
    """Регистрирует нового игрока-сущность и:
    1) показывает его всем уже подключённым,
    2) показывает всех уже подключённых новому игроку."""
    with _lock:
        existing = list(players.items())
        players[username] = {"uuid": uuid_bytes, "entity_id": entity_id, "sock": sock, "x": x, "y": y, "z": z}

    # Новому игроку - всех остальных
    for other_username, info in existing:
        try:
            send_packet(sock, CB_PLAYER_INFO, _player_info_add_payload(other_username, info["uuid"]))
            send_packet(sock, CB_NAMED_ENTITY_SPAWN, _spawn_player_payload(
                info["entity_id"], info["uuid"], info["x"], info["y"], info["z"]))
        except OSError:
            pass

    # Всем остальным - нового игрока
    info_payload = _player_info_add_payload(username, uuid_bytes)
    spawn_payload = _spawn_player_payload(entity_id, uuid_bytes, x, y, z)
    for other_username, other_sock in [(u, i["sock"]) for u, i in existing]:
        try:
            send_packet(other_sock, CB_PLAYER_INFO, info_payload)
            send_packet(other_sock, CB_NAMED_ENTITY_SPAWN, spawn_payload)
        except OSError:
            pass


def unregister_player(username: str):
    with _lock:
        info = players.pop(username, None)
        remaining = [i["sock"] for i in players.values()]
    if info is None:
        return
    uuid_bytes = info["uuid"]
    entity_id = info["entity_id"]
    remove_payload = write_varint(1) + uuid_bytes
    destroy_payload = write_varint(1) + write_varint(entity_id)
    for sock in remaining:
        try:
            send_packet(sock, CB_PLAYER_REMOVE, remove_payload)
            send_packet(sock, CB_ENTITY_DESTROY, destroy_payload)
        except OSError:
            pass


def _angle_to_byte(degrees: float) -> int:
    """Протокол кодирует углы поворота как один байт: 256 шагов на 360°."""
    val = int(degrees * 256 / 360) % 256
    if val >= 128:
        val -= 256
    return val


def update_player_position(username: str, x: float, y: float, z: float, yaw: float = 0.0, pitch: float = 0.0):
    """Рассылает всем остальным, что этот игрок передвинулся (и куда смотрит)."""
    with _lock:
        info = players.get(username)
        if info is None:
            return
        info["x"], info["y"], info["z"] = x, y, z
        info["yaw"], info["pitch"] = yaw, pitch
        entity_id = info["entity_id"]
        targets = [i["sock"] for u, i in players.items() if u != username]

    yaw_byte = _angle_to_byte(yaw)
    pitch_byte = _angle_to_byte(pitch)

    payload = (
        write_varint(entity_id)
        + write_double(x) + write_double(y) + write_double(z)
        + write_byte(yaw_byte) + write_byte(pitch_byte) + bytes([0])
    )
    head_payload = write_varint(entity_id) + write_byte(yaw_byte)

    for sock in targets:
        try:
            send_packet(sock, CB_ENTITY_TELEPORT, payload)
            send_packet(sock, CB_ENTITY_HEAD_ROTATION, head_payload)
        except OSError:
            pass


def update_player_look(username: str, yaw: float, pitch: float):
    """Игрок только повернул голову/тело, не сдвинувшись с места."""
    with _lock:
        info = players.get(username)
        if info is None:
            return
        info["yaw"], info["pitch"] = yaw, pitch
        x, y, z = info["x"], info["y"], info["z"]
        entity_id = info["entity_id"]
        targets = [i["sock"] for u, i in players.items() if u != username]

    yaw_byte = _angle_to_byte(yaw)
    pitch_byte = _angle_to_byte(pitch)

    payload = (
        write_varint(entity_id)
        + write_double(x) + write_double(y) + write_double(z)
        + write_byte(yaw_byte) + write_byte(pitch_byte) + bytes([0])
    )
    head_payload = write_varint(entity_id) + write_byte(yaw_byte)

    for sock in targets:
        try:
            send_packet(sock, CB_ENTITY_TELEPORT, payload)
            send_packet(sock, CB_ENTITY_HEAD_ROTATION, head_payload)
        except OSError:
            pass
