import struct
import msgpack
import lz4.block

_seq = 0


def ext_hook(code, data):
    # type=1 у них bigint/int64
    if code == 1:
        try:
            return msgpack.unpackb(
                data,
                raw=False,
                strict_map_key=False
            )
        except Exception:
            return int.from_bytes(
                data[-8:],
                "big",
                signed=False
            )

    return msgpack.ExtType(code, data)


def bytes_to_payload(packet: bytes):
    if len(packet) < 10:
        raise ValueError("packet too short")

    magic = packet[0]
    cmd = packet[1]
    seq = struct.unpack(">h", packet[2:4])[0]
    opcode = struct.unpack(">h", packet[4:6])[0]
    compression = packet[6]

    payload_len = (
        (packet[7] << 16)
        | (packet[8] << 8)
        | packet[9]
    )

    payload = packet[10:10 + payload_len]

    if compression > 0:
        payload = lz4.block.decompress(
            payload,
            uncompressed_size=payload_len * compression
        )

    decoded = None
    if payload:
        decoded = msgpack.unpackb(
            payload,
            raw=False,
            strict_map_key=False,
            ext_hook=ext_hook
        )

    return {
        "magic": magic,
        "cmd": cmd,
        "seq": seq,
        "opcode": opcode,
        "compression": compression,
        "payload": decoded,
    }


def payload_to_bytes(opcode: int, payload):
    global _seq

    payload_bytes = msgpack.packb(
        payload,
        use_bin_type=True
    )
    if not type(payload_bytes) == bytes:
        raise ValueError("Something went wrong: not bytes")

    payload_len = len(payload_bytes)
    header = bytearray(10)
    header[0] = 10              # magic
    header[1] = 0               # cmd

    struct.pack_into(
        ">h",
        header,
        2,
        _seq
    )

    struct.pack_into(
        ">h",
        header,
        4,
        opcode
    )

    header[6] = 0
    header[7] = (payload_len >> 16) & 0xFF
    header[8] = (payload_len >> 8) & 0xFF
    header[9] = payload_len & 0xFF

    packet = bytes(header) + payload_bytes

    _seq += 1

    return packet