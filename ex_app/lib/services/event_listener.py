import time

from ..db.store import dumps, now
from .nextcloud_files import within_folder

EVENTS = ["NodeCreatedEvent", "NodeWrittenEvent", "NodeDeletedEvent", "NodeRenamedEvent"]
CALLBACK = "/events/files"


async def register_listener(nc):
    # In NC34 the old AppAPI EventsListener controller is gone. Register supported webhooks,
    # with relative callback URI; WebhookCall uses AppAPI exAppRequest and supplies AppAPIAuth.
    existing = await nc.webhooks.get_list(CALLBACK)
    ours = {hook.event for hook in existing if hook.app_id == nc.app_cfg.app_name}
    for subtype in EVENTS:
        event = "OCP\\Files\\Events\\Node\\" + subtype
        if event not in ours:
            await nc.webhooks.register("POST", CALLBACK, event)


def enqueue(store, payload: dict, settings):
    event = payload.get("event", payload.get("event_data", {}))
    if not isinstance(event, dict):
        raise ValueError("Некорректные данные события")
    subtype = event.get("class", payload.get("event_subtype", "")).split("\\")[-1]
    if subtype not in EVENTS:
        return False
    # Save only node metadata; do not persist webhook ephemeral tokens or authentication payloads.
    nodes = [event.get(key, {}) for key in ("node", "target", "source")]
    root = "/" + settings.source_path.strip("/") if settings.source_path else ""
    known = {r["file_id"] for r in store.rows("SELECT file_id FROM source_files")}
    archive = store.meta("archive_path", settings.archive_path) if settings.archive_path else ""
    relevant = False
    node_id = "tree"
    for node in nodes:
        if not isinstance(node, dict) or not node:
            continue
        node_id = str(node.get("id", node.get("fileid", "tree")))
        path = str(node.get("path", ""))
        # Serializer can use /<owner>/files/<path>; events triggered by collaborators use owner metadata.
        if "/files/" in path:
            path = "/" + path.split("/files/", 1)[1]
        path = "/" + path.lstrip("/")
        # Moves into the archive still reconcile via the source node/path. Writes confined
        # to the archive do not queue another scan or parse the archived workbook.
        if within_folder(path, archive):
            continue
        if (
            node_id in known
            or node_id == settings.source_id
            or (root and (root == "/" or path == root or path.startswith(root + "/")))
        ):
            relevant = True
    # Unknown metadata must not discard directory moves/shared-tree notifications. Reconcile safely by ID.
    if not any(isinstance(n, dict) and n for n in nodes):
        relevant = bool(settings.source_id)
    if not relevant:
        return False
    store.set_meta("last_files_event", now())
    store.execute(
        "INSERT OR REPLACE INTO event_queue VALUES(?,?,?)",
        (node_id, time.time() + settings.debounce_seconds, dumps({"subtype": subtype})),
    )
    store.log("Info", "Events", "Изменение источника поставлено в очередь", operation=subtype)
    return True
