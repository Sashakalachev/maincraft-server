"""
Минимальный NBT-reader. Нам не нужно интерпретировать содержимое NBT предметов
(зачарования и т.д.) - только правильно понять, сколько байт он занимает,
чтобы дальше читать остаток пакета. Поддерживает все типы тегов.
"""
import struct

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


def _read_string(buf) -> str:
    length = struct.unpack(">H", buf.read(2))[0]
    return buf.read(length).decode("utf-8", errors="replace")


def _skip_payload(buf, tag_type: int):
    if tag_type == TAG_BYTE:
        buf.read(1)
    elif tag_type == TAG_SHORT:
        buf.read(2)
    elif tag_type == TAG_INT:
        buf.read(4)
    elif tag_type == TAG_LONG:
        buf.read(8)
    elif tag_type == TAG_FLOAT:
        buf.read(4)
    elif tag_type == TAG_DOUBLE:
        buf.read(8)
    elif tag_type == TAG_STRING:
        _read_string(buf)
    elif tag_type == TAG_BYTE_ARRAY:
        n = struct.unpack(">i", buf.read(4))[0]
        buf.read(n)
    elif tag_type == TAG_INT_ARRAY:
        n = struct.unpack(">i", buf.read(4))[0]
        buf.read(n * 4)
    elif tag_type == TAG_LONG_ARRAY:
        n = struct.unpack(">i", buf.read(4))[0]
        buf.read(n * 8)
    elif tag_type == TAG_COMPOUND:
        while True:
            child_type = buf.read(1)[0]
            if child_type == TAG_END:
                break
            _read_string(buf)  # имя тега
            _skip_payload(buf, child_type)
    elif tag_type == TAG_LIST:
        inner_type = buf.read(1)[0]
        count = struct.unpack(">i", buf.read(4))[0]
        for _ in range(count):
            _skip_payload(buf, inner_type)
    else:
        raise ValueError(f"Неизвестный тип NBT-тега при пропуске: {tag_type}")


def skip_nbt_from_packetbuffer(pkt):
    """pkt - объект PacketBuffer из protocol.py. Читает и отбрасывает один
    NBT-тег, начиная с байта типа (может быть TAG_END = 0, если NBT отсутствует)."""
    tag_type = pkt.read(1)[0]
    if tag_type == TAG_END:
        return  # NBT отсутствует (0x00 = отсутствие данных для optionalNbt)
    _read_string(pkt)  # имя корневого тега (обычно пустое)
    _skip_payload(pkt, tag_type)
