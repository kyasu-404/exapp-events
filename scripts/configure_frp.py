"""Configure the official HaRP FRP tunnel without exposing secrets in logs."""

import json
import os
from pathlib import Path

env = os.environ
text = f"""serverAddr = {json.dumps(env["HP_FRP_ADDRESS"])}
serverPort = {int(env["HP_FRP_PORT"])}
loginFailExit = false
metadatas.token = {json.dumps(env["HP_SHARED_KEY"])}
"""
certs = Path(env.get("HP_CERT_DIR", "/certs/frp"))
if certs.is_dir():
    text += f"""transport.tls.enable = true
transport.tls.certFile = {json.dumps(str(certs / "client.crt"))}
transport.tls.keyFile = {json.dumps(str(certs / "client.key"))}
transport.tls.trustedCaFile = {json.dumps(str(certs / "ca.crt"))}
transport.tls.serverName = "harp.nc"
"""
else:
    text += "transport.tls.enable = false\n"
text += f"""
[[proxies]]
remotePort = {int(env["APP_PORT"])}
type = "tcp"
name = {json.dumps(env["APP_ID"])}
[proxies.plugin]
type = "unix_domain_socket"
unixPath = {json.dumps(env.get("HP_EXAPP_SOCK", "/tmp/exapp.sock"))}
"""
Path("/tmp/frpc.toml").write_text(text, encoding="utf-8")
