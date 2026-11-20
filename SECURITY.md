# Security

## Reporting a problem
Please report security problems privately to the maintainer through GitHub (the repository owner's profile), not in a
public issue. Describe what you found and how to reproduce it; you will get an answer within a week.

## How localagent protects you
- The model never touches your computer directly: it asks for tools, and the program checks every request.
- File access is confined to the project folder; every write is verified and rolled back if broken.
- Nothing runs without your approval unless one of *your* permission rules allows it; a rule never stretches to a
  second command chained with `;`, `&&` or `|`.
- With Docker, code runs in a container without network, with memory, CPU and process limits, as a normal user.
- Downloads (llama.cpp, models, the bundled Python) are checked against their publishers' SHA-256 checksums.
- The local model server listens on `127.0.0.1` only.
- Your Gemini key is kept in your user settings or environment, never in a project.

## What it cannot do
- Without Docker, code you approve runs with your own rights on your computer.
- Checks prove that code parses and passes its tests, not that it does what you meant: review the diffs.
- On Gemini's free tier Google may use what you send to improve its services; use offline mode for private code.
