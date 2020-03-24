import struct

'''
+-+-+-+-+-------+-+-------------+-------------------------------+
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-------+-+-------------+-------------------------------+
|F|R|R|R| opcode|M| Payload len |    Extended payload length    |
|I|S|S|S|  (4)  |A|     (7)     |             (16/64)           |
|N|V|V|V|       |S|             |   (if payload len==126/127)   |
| |1|2|3|       |K|             |                               |
+-+-+-+-+-------+-+-------------+ - - - - - - - - - - - - - - - +
|     Extended payload length continued, if payload len == 127  |
+ - - - - - - - - - - - - - - - +-------------------------------+
|                     Payload Data continued ...                |
+---------------------------------------------------------------+
'''
FIN = 0x80
OPCODE = 0x0f
MASKED = 0x80
PAYLOAD_LEN = 0x7f
PAYLOAD_LEN_EXT16 = 0x7e
PAYLOAD_LEN_EXT64 = 0x7f

OPCODE_TEXT = 0x01
OPCODE_BINARY = 0x02
OPCODE_PING = 0x9
OPCODE_PONG = 0xA
CLOSE_CONN = 0x8


# https://blog.csdn.net/yangzai187/article/details/93905594

# 打包消息
# 参考：https://www.cnblogs.com/ssyfj/p/9245150.html
def pack_message(data, opcode=OPCODE_TEXT):
    # 参考 websocket-server模块(pip3 install websocket-server)
    """
    :param data: 需要打包的数据
    :param opcode:  打包类型：OPCODE_TEXT：文本，OPCODE_BINARY：二进制
    :return: bytes
    """
    if opcode == OPCODE_TEXT:
        # 文本
        if isinstance(data, bytes):
            try:
                msg = data.decode('utf-8')
            except UnicodeDecodeError:
                print('Can\'t send message, message is not valid UTF-8!')
                return
        elif isinstance(data, str):
            msg = data
        else:
            msg = str(data)

        payload = msg.encode(encoding='utf-8')

    elif opcode == OPCODE_BINARY:
        # 二进制
        payload = data
    else:
        # 不支持其他类型
        raise ValueError("unknown opcode value of {}".format(opcode))

    header = bytearray()
    header.append(FIN | opcode)
    # header.append(FIN | OPCODE_TEXT)

    payload_length = len(payload)
    if payload_length <= 125:
        header.append(payload_length)
    elif 126 <= payload_length <= 65535:
        header.append(PAYLOAD_LEN_EXT16)
        header.extend(struct.pack('>H', payload_length))
    elif payload_length < 18446744073709551616:
        header.append(PAYLOAD_LEN_EXT64)
        header.extend(struct.pack('>Q', payload_length))
    else:
        raise Exception("Message is too big. Consider breaking it into chunks.")

    return header + payload


# 客户端消息处理
class Message:

    def __init__(self):
        self.read_pos = 0

    def reset_pos(self):
        if self.read_pos != 0:
            self.read_pos = 0

    def backward_pos(self):
        self.read_pos -= 1

    def update_pos(self, pos):
        self.read_pos = pos

    # 消息解包
    # struct.pack struct.unpack
    # https://blog.csdn.net/qq_30638831/article/details/80421019
    # 参考：https://www.cnblogs.com/ssyfj/p/9245150.html
    def unpack_message(self, data):
        """
        :param data: bytes
        :return: str
        """
        # print("message, ", message)
        b1, b2 = self.read_bytes(data, 2)
        # fin = b1 & FIN
        opcode = b1 & OPCODE
        masked = b2 & MASKED
        payload_length = b2 & PAYLOAD_LEN

        if not b1 or opcode == CLOSE_CONN:
            raise ValueError('Message.unpack_message: Client closed connection.')

        if not masked:
            print("not masked: ", data)
            raise ValueError('Message.unpack_message: Client must always be masked.')

        if payload_length == 126:
            # (132,)
            payload_length = struct.unpack('>H', self.read_bytes(data, 2))[0]
        elif payload_length == 127:
            # (132,)
            payload_length = struct.unpack('>Q', self.read_bytes(data, 8))[0]

        masks = self.read_bytes(data, 4)

        decoded = bytearray()
        for c in self.read_bytes(data, payload_length):
            c ^= masks[len(decoded) % 4]
            decoded.append(c)

        return opcode, decoded

    # 读取字节
    def read_bytes(self, data, num):
        """
        :param data: bytes
        :param num: int
        :return: bytes
        """
        bs = data[self.read_pos:num + self.read_pos]
        self.read_pos += num
        return bs


if __name__ == "__main__":
    message = Message()
    print(message.unpack_message(b'\x88\x82\x83\xb7!\x12\x80_'))
