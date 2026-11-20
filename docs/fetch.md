# `localagent/fetch.py` — downloads you can trust

**Role:** the only code that downloads programs or models: llama.cpp from GitHub, a model from Hugging Face (several
GB), the embeddable Python for the release bundle. Three promises:
1. **Verified.** Every file is compared with the SHA-256 checksum its publisher states (GitHub's `digest`, Hugging
   Face's large-file record, python.org's release data). A mismatch — a damaged or altered download — is deleted and
   never used.
2. **Resumable.** A download goes to `<file>.part`. When the connection breaks or you press Ctrl+C, the next run asks
   the server only for the rest (an HTTP *Range* request). Checked live: Hugging Face's CDN answers such a request with
   `206 Partial Content`, so a 5 GB model never starts over.
3. **Unpacked safely.** An archive with a member that would land outside its folder is refused as a whole.

## Module header
```python
  1 | """Downloads you can trust: resumable, checked against the publisher's checksum, unpacked safely.
  2 | 
  3 | Used by `localagent setup` and `localagent models` to fetch the local model (several GB from
  4 | Hugging Face) and llama.cpp (from GitHub). Every file is compared with the SHA-256 checksum its
  5 | publisher states; a file that does not match is deleted, never used. An interrupted download
  6 | keeps its ``.part`` file and continues where it stopped the next time.
  7 | """
  8 | from __future__ import annotations
  9 | 
 10 | import hashlib
 11 | import json
 12 | import os
 13 | import tarfile
 14 | import urllib.error
 15 | import urllib.request
 16 | import zipfile
 17 | from pathlib import Path
 18 | from typing import Callable
 19 | 
 20 | from . import __version__
 21 | 
 22 | USER_AGENT = f"localagent/{__version__} (setup)"
 23 | CHUNK = 1 << 20                                  # read and hash 1 MB at a time
 24 | Progress = Callable[[int, "int | None"], None]   # (bytes so far, total bytes or None)
 25 | 
 26 | 
```
- `hashlib` computes checksums; `json` reads API answers; `os.replace` moves the finished file into place in one step;
  `tarfile`/`zipfile` unpack; `urllib` downloads (standard library, as everywhere in the agent).
- `USER_AGENT` names the program politely (sites may refuse requests without one). `CHUNK`: read, write and hash 1 MB
  at a time, so even a 5 GB file never needs much memory. `Progress` is the type of the progress callback:
  `(bytes so far, total bytes or None)`.

## `DownloadError`
```python
 27 | class DownloadError(Exception):
 28 |     """A download failed or did not match its checksum; the message says what to do."""
 29 | 
 30 | 
```
- Every problem a download can have, with a message that says what to do ("run the same command again to continue").

## Reading and checking
```python
 31 | def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> object:
 32 |     """GET ``url`` and parse the JSON answer; every failure becomes a DownloadError."""
 33 |     request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
 34 |     try:
 35 |         with urllib.request.urlopen(request, timeout=timeout) as response:
 36 |             return json.load(response)
 37 |     except urllib.error.HTTPError as exc:
 38 |         exc.close()
 39 |         raise DownloadError(f"{url} answered HTTP {exc.code}") from None
 40 |     except (urllib.error.URLError, OSError, ValueError) as exc:
 41 |         raise DownloadError(f"cannot read {url}: {exc}") from None
 42 | 
 43 | 
```
- GET a URL (with optional extra headers, e.g. an API key) and parse the JSON answer; an HTTP error, a network
  problem or a non-JSON answer becomes a `DownloadError`.

```python
 44 | def file_sha256(path: Path) -> str:
 45 |     """The SHA-256 checksum of a file, read 1 MB at a time (models are gigabytes)."""
 46 |     digest = hashlib.sha256()
 47 |     with path.open("rb") as handle:
 48 |         while chunk := handle.read(CHUNK):
 49 |             digest.update(chunk)
 50 |     return digest.hexdigest()
 51 | 
 52 | 
```
- The checksum of a file on disk, read in 1 MB pieces.

## `download`
```python
 53 | def download(url: str, target: Path, sha256: str, size: int | None = None,
 54 |              on_progress: Progress = lambda _done, _total: None) -> Path:
 55 |     """Fetch ``url`` into ``target`` and verify it; returns ``target``.
 56 | 
 57 |     A file already in place with the right checksum is kept. Otherwise the download goes to
 58 |     ``target.part``: a part left by an interrupted run is continued with an HTTP Range request
 59 |     (a server that ignores it simply sends the whole file again).
 60 |     """
 61 |     expected = sha256.lower()
 62 |     if target.is_file() and (size is None or target.stat().st_size == size) \
 63 |             and file_sha256(target) == expected:
 64 |         return target
 65 |     target.parent.mkdir(parents=True, exist_ok=True)
 66 |     part = target.with_name(target.name + ".part")
 67 |     done = part.stat().st_size if part.is_file() else 0
 68 |     if size is not None and done > size:                  # not a part of this file: start over
 69 |         part.unlink()
 70 |         done = 0
 71 |     if size is None or done < size:
 72 |         done = _fetch(url, part, done, size, on_progress)
 73 |     if size is not None and done != size:
 74 |         raise DownloadError(f"the download of {target.name} stopped at {done} of {size} bytes; "
 75 |                             "run the same command again to continue it")
 76 |     if file_sha256(part) != expected:
 77 |         part.unlink()
 78 |         raise DownloadError(f"{target.name} does not match its published checksum, so it was "
 79 |                             "deleted (a damaged or altered download); try again")
 80 |     os.replace(part, target)
 81 |     return target
 82 | 
 83 | 
```
- A file already in place with the right size and checksum is simply kept — running `localagent setup` twice never
  downloads twice.
- Otherwise: an existing `.part` larger than the file can be is not a part of it and is deleted; the rest is fetched
  (`_fetch`); a size that still does not match means the connection broke, which is reported with the advice to run
  the command again (the part stays for that). Then the whole part is checked: a wrong checksum deletes it; the right
  one moves it into place with `os.replace`, so the final name only ever holds a complete, verified file.

```python
 84 | def _fetch(url: str, part: Path, done: int, size: int | None, on_progress: Progress) -> int:
 85 |     """Append the rest of ``url`` to ``part`` (from byte ``done``); returns the bytes now there."""
 86 |     headers = {"User-Agent": USER_AGENT}
 87 |     if done:
 88 |         headers["Range"] = f"bytes={done}-"
 89 |     try:
 90 |         response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
 91 |     except urllib.error.HTTPError as exc:
 92 |         exc.close()
 93 |         raise DownloadError(f"{url} answered HTTP {exc.code}") from None
 94 |     except (urllib.error.URLError, OSError) as exc:
 95 |         raise DownloadError(f"cannot download {url} ({exc}); check the internet connection and "
 96 |                             "run the same command again to continue") from None
 97 |     with response:
 98 |         if done and response.status != 206:              # the server ignored the Range request
 99 |             done = 0
100 |         with part.open("ab" if done else "wb") as out:
101 |             try:
102 |                 while chunk := response.read(CHUNK):
103 |                     out.write(chunk)
104 |                     done += len(chunk)
105 |                     on_progress(done, size)
106 |             except (OSError, TimeoutError) as exc:
107 |                 raise DownloadError(f"the connection broke after {done} bytes ({exc}); run the same "
108 |                                     "command again to continue") from None
109 |     return done
110 | 
111 | 
```
- Asks for the rest of the file (`Range: bytes=<done>-`) when part of it is already there. A server that answers with
  the whole file (status 200 instead of 206) is handled by starting the part again. Each 1 MB piece is written and
  reported to `on_progress`; a connection that breaks during reading becomes a `DownloadError` that keeps the part.

## Unpacking
```python
112 | def _check_inside(archive: Path, folder: Path, names: list[str]) -> None:
113 |     """Refuse an archive with a member that would land outside ``folder`` (an attack called
114 |     "zip slip": a name such as ``../../x`` or an absolute path)."""
115 |     for name in names:
116 |         landing = (folder / name).resolve()
117 |         if landing != folder and folder not in landing.parents:
118 |             raise DownloadError(f"{archive.name}: '{name}' would land outside {folder}; "
119 |                                 "the archive was not unpacked")
120 | 
121 | 
```
- Resolves where each member would land; one landing outside the folder ("zip slip": names such as `../../x` or an
  absolute path, which could overwrite any file you own) refuses the whole archive before anything is written.

```python
122 | def extract_archive(archive: Path, folder: Path) -> None:
123 |     """Unpack a ``.zip`` (Windows builds) or ``.tar.gz`` (Linux and macOS builds) into ``folder``,
124 |     after checking that every member stays inside it."""
125 |     folder = folder.resolve()
126 |     if archive.name.endswith(".zip"):
127 |         with zipfile.ZipFile(archive) as bundle:
128 |             _check_inside(archive, folder, bundle.namelist())
129 |             bundle.extractall(folder)
130 |         return
131 |     with tarfile.open(archive, "r:*") as bundle:
132 |         _check_inside(archive, folder, bundle.getnames())
133 |         if hasattr(tarfile, "data_filter"):           # also refuses links out and device files
134 |             bundle.extractall(folder, filter="data")
135 |         else:                                         # very old Pythons: our own check only
136 |             bundle.extractall(folder)
```
- `.zip` (llama.cpp for Windows, the embeddable Python) or `.tar.gz` (llama.cpp for Linux and macOS): checked with
  `_check_inside`, then unpacked. For tar archives Python's `data` filter (3.12+, and recent security releases of
  older versions) is used as well: it also refuses links pointing out of the folder and device files.
