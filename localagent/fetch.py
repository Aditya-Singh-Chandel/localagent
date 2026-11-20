"""Downloads you can trust: resumable, checked against the publisher's checksum, unpacked safely.

Used by `localagent setup` and `localagent models` to fetch the local model (several GB from
Hugging Face) and llama.cpp (from GitHub). Every file is compared with the SHA-256 checksum its
publisher states; a file that does not match is deleted, never used. An interrupted download
keeps its ``.part`` file and continues where it stopped the next time.
"""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__

USER_AGENT = f"localagent/{__version__} (setup)"
CHUNK = 1 << 20                                  # read and hash 1 MB at a time
Progress = Callable[[int, "int | None"], None]   # (bytes so far, total bytes or None)


class DownloadError(Exception):
    """A download failed or did not match its checksum; the message says what to do."""


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> object:
    """GET ``url`` and parse the JSON answer; every failure becomes a DownloadError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise DownloadError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DownloadError(f"cannot read {url}: {exc}") from None


def file_sha256(path: Path) -> str:
    """The SHA-256 checksum of a file, read 1 MB at a time (models are gigabytes)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, sha256: str, size: int | None = None,
             on_progress: Progress = lambda _done, _total: None) -> Path:
    """Fetch ``url`` into ``target`` and verify it; returns ``target``.

    A file already in place with the right checksum is kept. Otherwise the download goes to
    ``target.part``: a part left by an interrupted run is continued with an HTTP Range request
    (a server that ignores it simply sends the whole file again).
    """
    expected = sha256.lower()
    if target.is_file() and (size is None or target.stat().st_size == size) \
            and file_sha256(target) == expected:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    done = part.stat().st_size if part.is_file() else 0
    if size is not None and done > size:                  # not a part of this file: start over
        part.unlink()
        done = 0
    if size is None or done < size:
        done = _fetch(url, part, done, size, on_progress)
    if size is not None and done != size:
        raise DownloadError(f"the download of {target.name} stopped at {done} of {size} bytes; "
                            "run the same command again to continue it")
    if file_sha256(part) != expected:
        part.unlink()
        raise DownloadError(f"{target.name} does not match its published checksum, so it was "
                            "deleted (a damaged or altered download); try again")
    os.replace(part, target)
    return target


def _fetch(url: str, part: Path, done: int, size: int | None, on_progress: Progress) -> int:
    """Append the rest of ``url`` to ``part`` (from byte ``done``); returns the bytes now there."""
    headers = {"User-Agent": USER_AGENT}
    if done:
        headers["Range"] = f"bytes={done}-"
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise DownloadError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(f"cannot download {url} ({exc}); check the internet connection and "
                            "run the same command again to continue") from None
    with response:
        if done and response.status != 206:              # the server ignored the Range request
            done = 0
        with part.open("ab" if done else "wb") as out:
            try:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    done += len(chunk)
                    on_progress(done, size)
            except (OSError, TimeoutError) as exc:
                raise DownloadError(f"the connection broke after {done} bytes ({exc}); run the same "
                                    "command again to continue") from None
    return done


def _check_inside(archive: Path, folder: Path, names: list[str]) -> None:
    """Refuse an archive with a member that would land outside ``folder`` (an attack called
    "zip slip": a name such as ``../../x`` or an absolute path)."""
    for name in names:
        landing = (folder / name).resolve()
        if landing != folder and folder not in landing.parents:
            raise DownloadError(f"{archive.name}: '{name}' would land outside {folder}; "
                                "the archive was not unpacked")


def extract_archive(archive: Path, folder: Path) -> None:
    """Unpack a ``.zip`` (Windows builds) or ``.tar.gz`` (Linux and macOS builds) into ``folder``,
    after checking that every member stays inside it."""
    folder = folder.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            _check_inside(archive, folder, bundle.namelist())
            bundle.extractall(folder)
        return
    with tarfile.open(archive, "r:*") as bundle:
        _check_inside(archive, folder, bundle.getnames())
        if hasattr(tarfile, "data_filter"):           # also refuses links out and device files
            bundle.extractall(folder, filter="data")
        else:                                         # very old Pythons: our own check only
            bundle.extractall(folder)
