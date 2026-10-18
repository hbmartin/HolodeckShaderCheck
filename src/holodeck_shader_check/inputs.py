"""Descriptor-pinned reads for disposable fixtures; checkout paths never authorize traversal."""
import contextlib
import os
import stat
from pathlib import PurePosixPath


class InputReader:
    def __init__(self, root):
        self.root = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.root)

    @staticmethod
    def parts(path):
        if not isinstance(path, str) or PurePosixPath(path).is_absolute():
            raise ValueError(f"Invalid fixture input path: {path}")
        parts = path.split('/')
        if any(part in ('', '.', '..') or '\0' in part for part in parts):
            raise ValueError(f"Invalid fixture input path: {path}")
        return parts

    @contextlib.contextmanager
    def directory(self, parts):
        descriptor = os.dup(self.root)
        try:
            for part in parts:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def read_file(parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError(f"Fixture input must be a regular file: {name}")
            return source.read()

    def read(self, path):
        parts = self.parts(path)
        try:
            with self.directory(parts[:-1]) as parent:
                return self.read_file(parent, parts[-1])
        except OSError as error:
            raise ValueError(f"Cannot read fixture input without following symbolic links: {path}: {error}") from error

    def template_files(self, kind):
        def walk(parent, prefix):
            for name in sorted(os.listdir(parent)):
                relative = prefix + (name,)
                # Original inventories are authoritative, even for hidden upstream files.
                if relative[0] != 'original' and name.startswith('.'): continue
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError(f"Template symlink forbidden: templates/{kind}/{'/'.join(relative)}")
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try: yield from walk(child, relative)
                    finally: os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    yield '/'.join(relative), self.read_file(parent, name)
                else:
                    raise ValueError(f"Invalid template file: templates/{kind}/{'/'.join(relative)}")
        with self.directory(['templates', kind]) as parent:
            yield from walk(parent, ())

    def template_kinds(self, kinds):
        try:
            parent = os.open('templates', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root)
        except FileNotFoundError:
            return []
        except OSError as error:
            raise ValueError(f"Template symlink forbidden or invalid directory: templates: {error}") from error
        try:
            result = []
            for name in sorted(os.listdir(parent)):
                if name.startswith('.'): continue
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError(f"Template symlink forbidden: templates/{name}")
                if stat.S_ISDIR(info.st_mode):
                    if name not in kinds: raise ValueError(f"Unknown template kind: {name}")
                    result.append(name)
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"Invalid template file: templates/{name}")
            return result
        except OSError as error:
            raise ValueError(f"Cannot inspect template directory: {error}") from error
        finally:
            os.close(parent)
