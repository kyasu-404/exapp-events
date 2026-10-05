import http.client
import json
import os
import socket

if os.environ.get("HP_SHARED_KEY"):
    connection = http.client.HTTPConnection("localhost", timeout=3)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(3)
    connection.sock.connect(os.environ.get("HP_EXAPP_SOCK", "/tmp/exapp.sock"))
else:
    connection = http.client.HTTPConnection("127.0.0.1", int(os.environ.get("APP_PORT", "23000")), timeout=3)
connection.request("GET", "/heartbeat")
response = connection.getresponse()
assert response.status == 200 and json.loads(response.read())["status"] == "ok"
connection.close()
