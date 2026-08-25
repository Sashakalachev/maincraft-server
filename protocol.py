"""
Базовые утилиты протокола Minecraft (Java Edition).
Формат пакета: [Length: VarInt][Packet ID: VarInt][Data: byte...]
"""
import struct
import socket
import json


class PacketBuffer:
    """Обёртка для чтения байтов пакета с курсором."""

    def __init__(self, data: bytes = b""):
        self.data = data
        self.pos = 0

    def read(self, n: int) -> bytes:
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def remaining(self) -> bytes:
        return self.data[self.pos:]

    # ---- чтение примитивов ----

    def read_varint(self) -> int:
        num = 0
        shift = 0
        while True:
            byte = self.read(1)
            if not byte:
                raise EOFError("Неожиданный конец данных при чтении VarInt")
            byte = byte[0]
            num |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
            if shift > 35:
                raise ValueError("VarInt слишком длинный")
        # приводим к знаковому int32
        if num & 0x80000000:
            num -= 1 << 32
        return num

    def read_string(self) -> str:
        length = self.read_varint()
        return self.read(length).decode("utf-8")

    def read_unsigned_short(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def read_long(self) -> int:
        return struct.unpack(">q", self.read(8))[0]

    def read_bool(self) -> bool:
        return self.read(1)[0] != 0

    def read_uuid(self) -> str:
        raw = self.read(16)
        return raw.hex()

    def read_double(self) -> float:
        return struct.unpack(">d", self.read(8))[0]

    def read_float(self) -> float:
        return struct.unpack(">f", self.read(4))[0]

    def read_position(self):
        """Читает упакованный Long и возвращает (x, y, z) блок-координаты."""
        raw = struct.unpack(">Q", self.read(8))[0]

        def signed(v: int, bits: int) -> int:
            if v & (1 << (bits - 1)):
                v -= 1 << bits
            return v

        x = signed((raw >> 38) & 0x3FFFFFF, 26)
        z = signed((raw >> 12) & 0x3FFFFFF, 26)
        y = signed(raw & 0xFFF, 12)
        return x, y, z


# ---- запись примитивов (статические хелперы) ----

def write_varint(value: int) -> bytes:
    out = bytearray()
    value &= 0xFFFFFFFF  # работаем в 32-битном беззнаковом представлении
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def write_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return write_varint(len(encoded)) + encoded


def write_unsigned_short(value: int) -> bytes:
    return struct.pack(">H", value)


def write_long(value: int) -> bytes:
    return struct.pack(">q", value)


def write_double(value: float) -> bytes:
    return struct.pack(">d", value)


def write_float(value: float) -> bytes:
    return struct.pack(">f", value)


def write_byte(value: int) -> bytes:
    return struct.pack(">b", value)


def write_position(x: int, y: int, z: int) -> bytes:
    """Кодирует блок-координаты в упакованный Long, как того требует протокол
    (x: 26 бит, z: 26 бит, y: 12 бит, все со знаком)."""
    def mask(v: int, bits: int) -> int:
        return v & ((1 << bits) - 1)
    packed = (mask(x, 26) << 38) | (mask(z, 26) << 12) | mask(y, 12)
    return struct.pack(">Q", packed)


def write_json(obj) -> bytes:
    return write_string(json.dumps(obj))


# ---- отправка/приём целых пакетов через сокет ----

def send_packet(sock: socket.socket, packet_id: int, payload: bytes = b""):
    body = write_varint(packet_id) + payload
    full = write_varint(len(body)) + body
    sock.sendall(full)


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Соединение закрыто клиентом")
        buf += chunk
    return buf


def read_varint_from_socket(sock: socket.socket) -> int:
    num = 0
    shift = 0
    while True:
        byte = _read_exact(sock, 1)[0]
        num |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("VarInt слишком длинный")
    if num & 0x80000000:
        num -= 1 << 32
    return num


def read_packet(sock: socket.socket) -> PacketBuffer:
    """Читает один полный пакет из сокета и возвращает буфер БЕЗ packet_id (id читается отдельно снаружи, если нужно раздельно)."""
    length = read_varint_from_socket(sock)
    data = _read_exact(sock, length)
    return PacketBuffer(data)
