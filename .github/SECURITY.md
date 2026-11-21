# Security

## Reporting a problem
Please report security problems privately, not in a public issue: on the repository's **Security** tab, choose
**Report a vulnerability** (GitHub's private vulnerability reporting — only the maintainer sees the report). Describe
what you found and how to reproduce it; you will get an answer within a week.

## How localagent protects you
- The model never touches your computer directly: it asks for tools, and the program checks every request.
- File access is confined to the project folder; every write is verified and rolled back if broken.
- Nothing runs without your approval unless one of *your* permission rules allows it; a rule never stretches to a
  second command chained with `;`, `&&` or `|`.
- With Docker, code runs in a container without network, with memory, CPU and process limits, as a normal user.
- Downloads (llama.cpp, models, the bundled Python) are checked against their publishers' SHA-256 checksums.
- The local model server listens on `127.0.0.1` only.
- Your keys (Gemini, OpenAI, Anthropic...) are kept in your user settings or environment, never in a project; on
  Linux and macOS the settings file is readable by you alone. A key is checked before it is saved.

## What it cannot do
- Without Docker, code you approve runs with your own rights on your computer.
- Checks prove that code parses and passes its tests, not that it does what you meant: review the diffs.
- On Gemini's free tier Google may use what you send to improve its services; use offline mode for private code.
