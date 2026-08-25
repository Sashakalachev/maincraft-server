import socket
import threading
import time
from protocol import (
    write_varint, write_string, write_unsigned_short, write_position,
    send_packet, read_packet,
)

HOST = "127.0.0.1"
PORT = 25569


def login(username):
    sock = socket.create_connection((HOST, PORT), timeout=10)
    payload = write_varint(763) + write_string(HOST) + write_unsigned_short(PORT) + write_varint(2)
    send_packet(sock, 0x00, payload)
    send_packet(sock, 0x00, write_string(username))

    pkt = read_packet(sock)
    assert pkt.read_varint() == 0x02, "Login Success ожидался"

    pkt = read_packet(sock)
    assert pkt.read_varint() == 0x28, "Join Game ожидался"

    return sock


def drain_until(sock, target_id, max_packets=400):
    for _ in range(max_packets):
        pkt = read_packet(sock)
        pid = pkt.read_varint()
        if pid == target_id:
            return pkt
    return None


# Игрок 1 заходит
sock1 = login("Alice")
print("Alice зашла")

# Игрок 2 заходит
sock2 = login("Bob")
print("Bob зашёл")

# Дожидаемся, пока оба получат чанки + позицию (не проверяем строго, просто выжидаем)
drain_until(sock1, 0x3C)
drain_until(sock2, 0x3C)
print("Оба получили позицию (заспавнились)")

# Alice ломает блок на своей платформе - окантовку в углу (0, 64, 0)
sock1.settimeout(5)
sock2.settimeout(5)

from protocol import PacketBuffer

def send_block_dig(sock, x, y, z):
    payload = (
        write_varint(0)  # status = started digging
        + write_position(x, y, z)
        + bytes([1])  # face
        + write_varint(42)  # sequence
    )
    send_packet(sock, 0x1D, payload)

send_block_dig(sock1, 1, 64, 1)  # окантовка платформы Alice (origin 0,0)

# Bob должен получить Block Change (0x0A) с этими координатами
pkt = drain_until(sock2, 0x0A)
assert pkt is not None, "Bob не получил трансляцию разрушения блока!"
x, y, z = pkt.read_position()
state = pkt.read_varint()
print(f"Bob увидел Block Change: ({x},{y},{z}) -> state {state} (ожидали air=0)")
assert (x, y, z) == (1, 64, 1)
assert state == 0

# Alice выходит - её платформа должна стереться, Bob должен получить кучу Block Change (air)
sock1.close()
time.sleep(1)

# Считаем сколько Block Change (air) пакетов пришло Bob'у после этого (должно быть много - вся платформа)
sock2.settimeout(3)
air_count = 0
try:
    while True:
        pkt = read_packet(sock2)
        pid = pkt.read_varint()
        if pid == 0x0A:
            air_count += 1
except (socket.timeout, ConnectionError, EOFError):
    pass

print(f"Bob получил {air_count} Block Change пакетов после выхода Alice (ожидаем ~1600 - вся платформа 40x40)")
assert air_count > 1000, "Платформа не была стёрта полностью!"

print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
