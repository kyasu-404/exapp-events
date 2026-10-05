import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..models import Settings


@dataclass
class SourceFile:
    file_id: str
    path: str
    etag: str = ""
    mtime: str = ""


class NextcloudFiles:
    def __init__(self, nc):
        self.nc = nc

    async def resolve_root(self, settings: Settings):
        root = (
            await self.nc.files.by_id(settings.source_id)
            if settings.source_id
            else await self.nc.files.by_path(settings.source_path)
        )
        if root is None or not root.is_dir:
            raise ValueError("Папка-источник недоступна; удаление событий запрещено")
        return root

    async def scan(self, settings: Settings) -> tuple[str, list[SourceFile]]:
        root = await self.resolve_root(settings)
        pending, visited, files = [root], set(), []
        while pending:
            folder = pending.pop()
            if str(folder.info.fileid) in visited:
                continue
            visited.add(str(folder.info.fileid))
            for node in await self.nc.files.listdir(folder):
                if node.is_dir:
                    if settings.recursive:
                        pending.append(node)
                elif (
                    node.name.casefold().endswith(".xlsx")
                    and fnmatch.fnmatch(node.name.casefold(), settings.include.casefold())
                    and not any(
                        fnmatch.fnmatch(node.name.casefold(), p.strip().casefold())
                        for p in settings.exclude.split(";")
                        if p.strip()
                    )
                ):
                    files.append(
                        SourceFile(
                            str(node.info.fileid),
                            node.user_path,
                            node.etag,
                            node.info.last_modified.isoformat(),
                        )
                    )
        return root.user_path, sorted(files, key=lambda n: n.path)

    async def read(self, file: SourceFile) -> bytes:
        node = await self.nc.files.by_id(file.file_id)
        if node is None or node.is_dir:
            raise ValueError("Файл недоступен")
        if node.info.content_length > 32 * 1024 * 1024:
            raise ValueError("XLSX превышает 32 МБ")
        content = await self.nc.files.download(node)
        after = await self.nc.files.by_id(file.file_id)
        if after is None or after.etag != node.etag or after.user_path != node.user_path:
            raise ValueError("Файл изменился во время чтения; повторите синхронизацию")
        # Use the current stable metadata even after rename.
        file.path, file.etag = node.user_path, node.etag
        return content

    async def preview_file(self, file_id: str, settings: Settings) -> SourceFile:
        root = await self.resolve_root(settings)
        node = await self.nc.files.by_id(file_id)
        if node is None or node.is_dir or not node.name.casefold().endswith(".xlsx"):
            raise ValueError("Выберите XLSX")
        if PurePosixPath(root.user_path) not in PurePosixPath(node.user_path).parents:
            raise ValueError("XLSX должен находиться в папке-источнике")
        return SourceFile(str(node.info.fileid), node.user_path, node.etag)
