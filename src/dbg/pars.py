import base64
import msgpack
import lz4.block
import struct

def ext_hook(code, data):
    if code == 1:
        return msgpack.unpackb(
            data,
            raw=False,
            strict_map_key=False
        )

    return msgpack.ExtType(code, data)

def parse_packet(buf):

    magic = buf[0]
    cmd = buf[1]

    seq = struct.unpack(">h", buf[2:4])[0]
    opcode = struct.unpack(">h", buf[4:6])[0]

    ratio = buf[6]

    payload_len = (
        (buf[7] << 16)
        | (buf[8] << 8)
        | buf[9]
    )

    payload = buf[10:10 + payload_len]

    if ratio > 0:
        payload = lz4.block.decompress(
            payload,
            uncompressed_size=payload_len * ratio
        )

    payload_obj = None

    if payload:
        payload_obj = msgpack.unpackb(
            payload,
            raw=False,
            ext_hook=ext_hook,
            strict_map_key=False
        )

    return {
        "magic": magic,
        "cmd": cmd,
        "seq": seq,
        "opcode": opcode,
        "payload": payload_obj
    }

def main():
    while True:
        print(parse_packet(base64.b64decode(input("\nB64: "))))

if __name__ == "__main__": main()