import socket
import os
import socketserver
import selectors
import json
from common.properties import Properties
from common.handshake import handshake
from common.iputils import get_real_ip
from common.message import Message
from common.message import pack_message
from common.message import OPCODE_BINARY
from common.strings import decode_utf8
import queue

import base64

buffer_size = 1024
socket_buffer_size = 1024000

if hasattr(os, "fork"):
    from socketserver import ForkingTCPServer

    _TCPServer = ForkingTCPServer
else:
    from socketserver import ThreadingTCPServer

    _TCPServer = ThreadingTCPServer


# 图像服务器
class WebSocketServer(_TCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler):
        self.clients = {}
        super().__init__(server_address, handler)

    def handle_request(self):
        print("请求进来了。。。")

    def serve_forever(self, poll_interval=0.5):
        print("Server start.")
        super().serve_forever(poll_interval)

    def new_client(self, handler):
        # handler  # type: WebSocketServerRequestHandler
        self.clients[handler.xid] = handler

    def get_client_request(self, xid: str):
        handler = self.clients[xid]  # type: WebSocketServerRequestHandler
        return handler.request

    def get_client(self, xid: str):
        return self.clients.get(xid)


"""
相关术语描述：
控制机：指的是用户可以访问到那台机器。
用户机：指的是用户正在使用的那台机器。

"""


# 请求处理
class WebSocketServerRequestHandler(socketserver.BaseRequestHandler):

    def __init__(self, request, client_address, server):
        self.keep_alive = True
        self.handshake_done = False

        self.is_master = False      # 是否是控制机
        self.is_client = True       # 是否是用户机

        self.xid = None             # 绑定的编号
        self.pwd = None             # 绑定的密码

        self.master_xid = None      # 控制机的编号
        self.master_pwd = None      # 控制机的密码

        self.json_type = True       # 默认以JSON类型传输数据，False: 接收到的数据不能解析为json数据了。

        self.last_send_message = None   # 上次发送的数据

        self.selector = selectors.DefaultSelector()
        self.selector.register(request, selectors.EVENT_READ)
        self.server = server  # type: WebSocketServer
        self.message = Message()

        self.queue = queue.Queue()          # 默认队列

        self.request = request
        self.master_request = None  # type: socket.socket
        self.master_handler = None  # type: WebSocketServerRequestHandler
        self.client_requests = []    # 所有用户机的socket

        super().__init__(request, client_address, server)

    def reg_send(self):
        # print("reg_send.....{}".format(self.request.getpeername()))
        self.selector.modify(self.request, selectors.EVENT_WRITE)

    def reg_read(self):
        # print("reg_read.....{}".format(self.request.getpeername()))
        self.selector.modify(self.request, selectors.EVENT_READ)

    def handle(self):

        # print("handle.....{}".format(self.request.getpeername()))

        while self.keep_alive:
            # 获取图像数据
            # print("selector.select>>>>>>{}".format(self.request.getpeername()))

            selection_keys = self.selector.select()

            for key, events in selection_keys:
                if key.fileobj == self.request:
                    if events == selectors.EVENT_READ:
                        # 客户端读事件
                        # print("read_message。。。。")
                        self.read_message()
                    elif events == selectors.EVENT_WRITE:
                        # 客户端写事件
                        # print("send_message。。。。")
                        self.send_message()

    def handshake(self, request_header):
        # 从请求的数据中获取 Sec-WebSocket-Key, Upgrade
        sock = self.request  # type: socket.socket

        request, payload = request_header.split("\r\n", 1)

        maps = Properties(separator=':', ignore_case=True).load(payload)  # type: dict

        # try:
        self.handshake_done = handshake(self.request, maps)

        if self.handshake_done > 0:
            self.server.new_client(self)

            ip = get_real_ip(maps)
            if not ip or ip == "unknown":
                ip = sock.getpeername()[0]

            print('handshake success...', ip)

        else:
            self.keep_alive = False

        # except ValueError as ve:
        #     print("handshake.ve", ve)

    def read_message(self):
        """
        读取客户端的信息
        :return:
        """
        try:
            # 客户端
            message = self.request.recv(socket_buffer_size)

            if not message:
                print('read_message: client disconnect. received empty data!')
                self.keep_alive = False
                return

            if self.handshake_done is False:
                try:
                    request_header = str(message, encoding='ascii')
                except UnicodeDecodeError as e:
                    print("read_message.UnicodeDecodeError:", e)
                    self.keep_alive = False
                    return
                self.handshake(request_header)
            else:

                # 如果是用户机的话，就通过解包的来获取相关的操作信息
                self.message.reset_pos()
                # 解析文本
                # 在读下一个字符，看看有没有客户端两次传入，一次解析的。
                # 考虑两份数据一次接收的问题。需要先看一下第一个字符是否有效。
                while self.message.read_bytes(message, 1):
                    self.message.backward_pos()
                    try:
                        # 1，控制机只负责上传图片数据，接收JSON数据
                        # 2，用户机只负责上传JSON数据，接收图片数据
                        # 当没有确定是什么类型的机器，默认都是通过JSON类型上传/接收数据。
                        opcode, decoded = self.message.unpack_message(message)

                        if opcode == 0x01:
                            # 文本
                            user_data = json.loads(str(decoded, encoding="utf-8"))  # type: dict
                            self.handle_client_message(user_data)
                        elif opcode == 0x02:
                            # 二进制
                            if self.is_master:
                                # 控制机器
                                # 接收到了图像数据，将图像发送给用户机
                                # 发送数据给用户机
                                if len(self.client_requests) > 0:
                                    # encode_message = pack_message(decoded, OPCODE_BINARY)
                                    # print("encode_message:", encode_message)
                                    self.client_send_message(pack_message(decoded, OPCODE_BINARY))
                                    # 响应数据给控制机，告诉控制机，我已经将数据发送给对方了。
                                    reply_message = {
                                        "size": {
                                            "width": 0,
                                            "height": 0
                                        },
                                        "action": "capture",
                                        "emit": "change"
                                    }
                                else:
                                    reply_message = {
                                        "size": {
                                            "width": 0,
                                            "height": 0
                                        },
                                        "action": "pause"
                                    }

                                self.queue.put(json.dumps(reply_message).encode(), block=False)
                                self.reg_send()

                    except ValueError as e:
                        print(e)
                        self.keep_alive = False


                    # print('read next....')
        except (ConnectionAbortedError, ConnectionResetError, TimeoutError) as es:
            # [Errno 54] Connec tion reset by peer
            # self.shutdown_request()
            print(es)
            self.finish()

    def handle_client_message(self, user_data: dict):
        """
        处理用户机的消息
        :param user_data: 用户机提交过来的信息
        :return:
        """
        print("收到用户信息：", user_data)
        if "xid" in user_data:
            # 客户端上传xid信息
            self.xid = user_data.get("xid", None)
            self.server.new_client(self)
        if "pwd" in user_data:
            # 客户端上传密码
            self.pwd = user_data.get("pwd", None)

        if "master" in user_data:
            master = user_data.get("master", None)  # type: dict
            if master:
                self.master_xid = master.get("xid", None)
                self.master_pwd = master.get("pwd", None)
                # 可以进行校验密码
                # 密码校验成功
                # 可以开始连通目标机器

                handler = self.server.get_client(self.master_xid)  # type: WebSocketServerRequestHandler
                if handler and handler.request:
                    print("找到目标机器..")

                    handler.is_master = True  # 设置为控制机
                    handler.is_client = False  # 当前机器不能作为客户端，意思是不能连接到其他机器
                    handler.json_type = False  # 接收到的数据不能解析为json数据了。

                    self.master_request = handler.request  # type: socket.socket
                    self.master_handler = handler

                    # 如果关闭的socket没有在client_requests中移除的话，那么就会一直在收。
                    print("handler.client_requests: ", handler.client_requests)
                    if len(handler.client_requests) > 0:
                        # 已经有请求存在，说明已经有在交互了，无需再次重复发送给控制机。
                        handler.client_requests.append(self.request)
                    else:
                        handler.client_requests.append(self.request)

                        print("准备发送向{}数据".format(self.master_request.getpeername()))

                        # 发送数据给对方
                        # 请求数据
                        reply_message = {
                            "size": {
                                "width": 0,
                                "height": 0
                            },
                            "action": "capture",
                            "emit": "full"
                        }

                        # 将数据发送给控制机
                        print("将数据发送给控制机")
                        self.master_send_message(json.dumps(reply_message))

                else:
                    print("无法找到目标机器！")

        if "key" in user_data and not self.is_master and self.is_client:
            # 客户端传过来的按键
            pass

        if "mouse" in user_data and not self.is_master and self.is_client:
            # 客户端传过来的鼠标位置
            pass

    def master_send_message(self, data: str):
        """
        发送消息给控制机
        :param data:
        :return:
        """
        try:
            payload = pack_message(data)
            self.master_request.sendall(payload)
        except BrokenPipeError as bpe:
            print("master_send_message.BrokenPipeError:", bpe)

    def client_send_message(self, payload: bytes):
        """
        发送数据给所有用户机
        :param payload: 控制机传过来的二进制数据
        :return:
        """
        # output: bytearray = self.message.unpack_message(data)
        # print("requests:", self.client_requests)
        for request in self.client_requests:
            try:
                request.sendall(payload)
            except OSError as ose:
                self.client_requests.remove(request)
                print("OSError:(client_request.sendall)", ose)

    def send_message(self):

        """
        发送消息给用户机
        :return:
        """

        message = b''
        while not self.queue.empty():
            message += self.queue.get_nowait()

        presentation = decode_utf8(message, flag='replace', replace_str='?')
        payload = pack_message(presentation)

        try:
            # print("发送数据", self.request)
            self.request.send(payload)

            self.reg_read()
        except BrokenPipeError as bpe:
            print("bpe:", bpe)

    def send_raw_message(self, data: bytes):
        """
        发送裸数据给用户机
        :param data:
        :return:
        """
        try:
            self.request.send(data)
            self.reg_read()
        except BrokenPipeError as bpe:
            print("bpe:", bpe)

    def finish(self):
        if self.master_handler:
            if len(self.master_handler.client_requests) > 0:
                # 将自己从master中删除。
                print("self.master_handler.client_requests", self.master_handler.client_requests)
                self.master_handler.client_requests.remove(self.request)

        print("关闭连接了", self.request)
        self.keep_alive = False
