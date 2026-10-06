"""Isolated visual fixture; serves fake API data and built UI on loopback only.

Never imports production auth or connects to Nextcloud/SMTP.
Run after `pnpm build`: python -m tests.ui_preview
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ex_app.lib.models import Settings

ROOT = Path(__file__).resolve().parents[1]
settings = Settings().model_dump()
settings.update(
    source_owner="admin",
    calendar_owner="admin",
    source_path="/Мероприятия",
    source_id="7",
    smtp_mode="nextcloud",
    smtp_enabled=True,
)
shell = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Мероприятия — локальная проверка</title><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/events-main.css"><style>
:root{--color-main-background:#fff;--color-main-text:#222;--color-primary:#00679e;--color-primary-element:#00679e;--color-primary-element-text:#fff;--color-background-hover:#f4f6f8;--color-border:#dfe4e8;--color-border-maxcontrast:#707070;--default-grid-baseline:4px;--border-radius-element:8px;--color-text-maxcontrast:#58636e;--color-error:#bc2730;--default-clickable-area:44px;--header-height:50px;--border-radius:8px;--border-radius-large:12px;--color-background-dark:#ededed;--color-background-darker:#ddd;--color-primary-element-light:#e2f0fa;--color-primary-element-light-hover:#d1e8f6;--font-face:Arial,sans-serif;--default-font-size:15px;--color-placeholder-dark:#707070}*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;font-size:15px;color:#222}#header{height:50px;background:#00679e;color:white;padding:15px 24px}#content{height:calc(100vh - 50px)}a{color:inherit;text-decoration:none}button,input,select{font:inherit}h1,h2,p{margin-top:0}input{max-width:100%;padding:10px;border:1px solid #ddd;border-radius:8px}
</style><script>
window.OC={webroot:'',config:{version:'34.0.2'},requestToken:'fixture',getCurrentUser:()=>({uid:'admin',displayName:'Администратор',isAdmin:true}),get:()=>null};
window.OCA={}; window._oc_current_user='admin';window._oc_current_user_displayName='Администратор';window._oc_requesttoken='fixture';window._oc_debug=false;
window._oc_config={version:'34.0.2',modRewriteWorking:true};window._oc_webroot='';window._oc_appswebroots={app_api:'/apps/app_api',files:'/apps/files'};
</script></head><body><div id="skip-actions" style="display:none"></div><header id="header">Nextcloud · Локальная проверка на тестовых данных</header><div id="content"></div><script src="/events-main.js"></script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def output(self, content, content_type="application/json"):
        data = content.encode() if isinstance(content, str) else content
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.output(shell, "text/html; charset=utf-8")
        if path in ["/events-main.js", "/events-main.css"]:
            return self.output(
                (ROOT / "ex_app/js" / path.lstrip("/")).read_bytes(),
                "text/javascript" if path.endswith(".js") else "text/css",
            )
        route = path.split("/exapp_events")[-1]
        result = []
        if route == "/api/settings":
            result = {**settings, "smtp_password_set": False}
        elif route == "/api/users":
            result = ["admin", "calendar-owner"]
        elif route == "/api/status":
            result = {
                "enabled": True,
                "version": "0.1.2",
                "source_path": "/Мероприятия",
                "counts": {
                    "files": 0,
                    "events": 0,
                    "public": 0,
                    "email": [{"status": "skipped", "n": 1}],
                    "logs": [],
                    "issues": 1,
                },
                "issues": [
                    {
                        "file": "/Мероприятия/2026/Октябрь/Октябрь.xlsx",
                        "sheet": "08.10",
                        "row": 2,
                        "level": "Warning",
                        "message": "Не заполнены поля: Ответственный. Строка пропущена; прежние события файла сохранены.",
                    }
                ],
                "recent_emails": [
                    {
                        "id": 1,
                        "recipient": "admin@example.org",
                        "scheduled_at": "2026-10-06T10:00:00+00:00",
                        "status": "skipped",
                        "kind": "reminder",
                        "last_error": "Время напоминания прошло до постановки в очередь",
                    }
                ],
                "runs": [],
                "last_files_event": "",
                "last_full_reconciliation": "",
                "last_successful_sync": "",
            }
        elif route == "/api/calendars":
            result = [
                {"name": "Мероприятия", "url": "calendars/admin/events/", "writable": True},
                {"name": "Мероприятия сайта", "url": "calendars/admin/public/", "writable": True},
            ]
        self.output(json.dumps(result, ensure_ascii=False))

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        settings.update(body["settings"])
        self.output('{"saved":true}')

    def do_POST(self):
        if self.path.endswith("/diagnostics"):
            self.output(
                json.dumps(
                    [
                        {"name": name, "status": "ok"}
                        for name in ["AppAPI", "Events Listener", "SQLite", "Calendar API"]
                    ]
                )
            )
        else:
            self.output('{"sent":true}')


if __name__ == "__main__":
    print("Isolated visual fixture: http://127.0.0.1:23101", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 23101), Handler).serve_forever()
