from server import websocketserver

server = websocketserver.WebSocketServer(("0.0.0.0", 8890), websocketserver.WebSocketServerRequestHandler)
server.server_activate()
server.serve_forever()