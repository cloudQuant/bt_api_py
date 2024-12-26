from websocket import create_connection

ws = create_connection("ws://echo.websocket.events/")
print(ws.recv())
ws.send("Hello, World")
result = ws.recv()
assert result == "Hello, World"
print("Received '%s'" % result)
ws.close()
