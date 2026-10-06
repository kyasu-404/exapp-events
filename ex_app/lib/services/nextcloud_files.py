import fnmatch
from dataclasses import dataclass

from ..models import Settings


def within_folder(path: str, folder: str) -> bool:
    if not folder:
        return False
    path, folder = "/" + path.strip("/"), "/" + folder.strip("/")
    return folder == "/" or path == folder or path.startswith(folder + "/")


@dataclass
class SourceFile:
    file_id: str
    path: str
    etag: str = ""
    mtime: str = ""


class NextcloudFiles:
    def __init__(self, nc):
        self.nc = nc
        self.archive_path = ""
        self.archive_id = ""

    async def resolve_root(self, settings: Settings):
        root = (
            await self.nc.files.by_id(settings.source_id)
            if settings.source_id
            else await self.nc.files.by_path(settings.source_path)
        )
        if root is None or not root.is_dir:
            raise ValueError("Папка-источник недоступна; удаление событий запрещено")
        return root

    async def resolve_archive(self, settings: Settings, root=None):
        if not settings.archive_path:
            self.archive_path = ""
            self.archive_id = ""
            return None
        archive = (
            await self.nc.files.by_id(settings.archive_id)
            if settings.archive_id
            else await self.nc.files.by_path(settings.archive_path)
        )
        if archive is None or not archive.is_dir:
            raise ValueError("Архивная папка недоступна; сверка отложена")
        root = root or await self.resolve_root(settings)
        if within_folder(root.user_path, archive.user_path):
            raise ValueError("Архивная папка не может совпадать с источником или содержать его")
        self.archive_path = archive.user_path
        self.archive_id = str(archive.info.fileid)
        return archive

    async def archived_files(self, file_ids: set[str]) -> dict[str, str]:
        """Resolve metadata of previously tracked files, without listing or reading the archive."""
        if not self.archive_path:
            return {}
        result = {}
        for file_id in sorted(file_ids):
            node = await self.nc.files.by_id(file_id)
            if node and not node.is_dir and within_folder(node.user_path, self.archive_path):
                result[file_id] = node.user_path
        return result

    async def scan(self, settings: Settings) -> tuple[str, list[SourceFile]]:
        root = await self.resolve_root(settings)
        await self.resolve_archive(settings, root)
        pending, visited, files = [root], set(), []
        while pending:
            folder = pending.pop()
            if str(folder.info.fileid) in visited:
                continue
            visited.add(str(folder.info.fileid))
            for node in await self.nc.files.listdir(folder):
                if str(node.info.fileid) == self.archive_id or within_folder(node.user_path, self.archive_path):
                    continue
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
        if within_folder(node.user_path, self.archive_path):
            raise ValueError("Файл перемещён в архив; чтение пропущено")
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
        await self.resolve_archive(settings, root)
        node = await self.nc.files.by_id(file_id)
        if node is None or node.is_dir or not node.name.casefold().endswith(".xlsx"):
            raise ValueError("Выберите XLSX")
        if not within_folder(node.user_path, root.user_path):
            raise ValueError("XLSX должен находиться в папке-источнике")
        if within_folder(node.user_path, self.archive_path):
            raise ValueError("Файлы архивной папки не обрабатываются")
        return SourceFile(str(node.info.fileid), node.user_path, node.etag)
