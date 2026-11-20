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
@@ localagent/fetch.py header @@
- `hashlib` computes checksums; `json` reads API answers; `os.replace` moves the finished file into place in one step;
  `tarfile`/`zipfile` unpack; `urllib` downloads (standard library, as everywhere in the agent).
- `USER_AGENT` names the program politely (sites may refuse requests without one). `CHUNK`: read, write and hash 1 MB
  at a time, so even a 5 GB file never needs much memory. `Progress` is the type of the progress callback:
  `(bytes so far, total bytes or None)`.

## `DownloadError`
@@ localagent/fetch.py DownloadError @@
- Every problem a download can have, with a message that says what to do ("run the same command again to continue").

## Reading and checking
@@ localagent/fetch.py get_json @@
- GET a URL (with optional extra headers, e.g. an API key) and parse the JSON answer; an HTTP error, a network
  problem or a non-JSON answer becomes a `DownloadError`.

@@ localagent/fetch.py file_sha256 @@
- The checksum of a file on disk, read in 1 MB pieces.

## `download`
@@ localagent/fetch.py download @@
- A file already in place with the right size and checksum is simply kept — running `localagent setup` twice never
  downloads twice.
- Otherwise: an existing `.part` larger than the file can be is not a part of it and is deleted; the rest is fetched
  (`_fetch`); a size that still does not match means the connection broke, which is reported with the advice to run
  the command again (the part stays for that). Then the whole part is checked: a wrong checksum deletes it; the right
  one moves it into place with `os.replace`, so the final name only ever holds a complete, verified file.

@@ localagent/fetch.py _fetch @@
- Asks for the rest of the file (`Range: bytes=<done>-`) when part of it is already there. A server that answers with
  the whole file (status 200 instead of 206) is handled by starting the part again. Each 1 MB piece is written and
  reported to `on_progress`; a connection that breaks during reading becomes a `DownloadError` that keeps the part.

## Unpacking
@@ localagent/fetch.py _check_inside @@
- Resolves where each member would land; one landing outside the folder ("zip slip": names such as `../../x` or an
  absolute path, which could overwrite any file you own) refuses the whole archive before anything is written.

@@ localagent/fetch.py extract_archive @@
- `.zip` (llama.cpp for Windows, the embeddable Python) or `.tar.gz` (llama.cpp for Linux and macOS): checked with
  `_check_inside`, then unpacked. For tar archives Python's `data` filter (3.12+, and recent security releases of
  older versions) is used as well: it also refuses links pointing out of the folder and device files.
