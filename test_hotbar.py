import socket
from protocol import (
    write_varint, write_string, write_unsigned_short, write_position,
    send_packet, read_packet,
)

HOST = "127.0.0.1"
PORT = 25570

OAK_PLANKS_ITEM_ID = 23
OAK_PLANKS_BLOCK_STATE = 15


def login(username):
    sock = socket.create_connection((HOST, PORT), timeout=10)
    payload = write_varint(763) + write_string(HOST) + write_unsigned_short(PORT) + write_varint(2)
    send_packet(sock, 0x00, payload)
    send_packet(sock, 0x00, write_string(username))
    pkt = read_packet(sock); assert pkt.read_varint() == 0x02
    pkt = read_packet(sock); assert pkt.read_varint() == 0x28
    return sock


def drain_until(sock, target_id, max_packets=400):
    for _ in range(max_packets):
        pkt = read_packet(sock)
        pid = pkt.read_varint()
        if pid == target_id:
            return pkt
    return None


sock = login("BuilderTest")
drain_until(sock, 0x3C)
print("Заспавнился")

# 1. Кладём oak_planks в хотбар-слот 0 (инвентарный индекс 36)
def write_slot_present(item_id, count=1):
    return bytes([1]) + write_varint(item_id) + bytes([count]) + bytes([0])  # NBT: TAG_END (нет NBT)

payload = (36).to_bytes(2, "big", signed=True) + write_slot_present(OAK_PLANKS_ITEM_ID)
send_packet(sock, 0x2B, payload)  # SB_SET_CREATIVE_SLOT

# 2. Выбираем слот 0 как активный
send_packet(sock, 0x28, (0).to_bytes(2, "big", signed=True))  # SB_HELD_ITEM_SLOT

# 3. Ставим блок рядом с центром платформы (20,64,20), направление +Y (вверх)
payload = (
    write_varint(0)  # hand = main hand
    + write_position(20, 64, 20)
    + write_varint(1)  # direction = +Y (вверх)
    + bytes([0, 0, 0, 0])  # cursorX/Y/Z как float 0.0 каждый по 4 байта - упростим ниже
)
# соберём float 0.0 правильно через struct
import struct
cursor = struct.pack(">fff", 0.5, 0.5, 0.5)
payload = (
    write_varint(0)
    + write_position(20, 64, 20)
    + write_varint(1)
    + cursor
    + bytes([0])  # insideBlock = false
    + write_varint(99)  # sequence
)
send_packet(sock, 0x31, payload)  # SB_BLOCK_PLACE

# Читаем Acknowledge Block Change + Block Change и проверяем блок
pkt = drain_until(sock, 0x0A)  # Block Change
assert pkt is not None, "Block Change не получен!"
x, y, z = pkt.read_position()
state = pkt.read_varint()
print(f"Блок поставлен: ({x},{y},{z}) -> state {state}")
print(f"Ожидали: (20,65,20) -> state {OAK_PLANKS_BLOCK_STATE} (oak_planks)")

assert (x, y, z) == (20, 65, 20), f"Неверная позиция: {(x,y,z)}"
assert state == OAK_PLANKS_BLOCK_STATE, f"Поставлен не тот блок! state={state}, ожидали {OAK_PLANKS_BLOCK_STATE}"

print("ТЕСТ ПРОЙДЕН: ставится реальный блок из руки, а не рандомный камень")
sock.close()
