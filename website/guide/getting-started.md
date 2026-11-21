# Get started

localagent is a coding agent for your terminal. You ask in plain words; it reads your project, edits files, runs your
tests and checks every change before it keeps it. This guide takes you from the download to your first finished task
in about five minutes.

## 1. Install it

**Windows.** Download the zip from the [Download page](../download.html) — Python is inside, nothing else is needed.
1. Unzip it somewhere permanent, for example `C:\Tools\localagent` (not the Downloads folder, which gets cleaned up).
2. Double-click `install.cmd`. It adds the folder to your PATH — only for your account — and starts the guided setup.
3. Open a **new** terminal (PowerShell or Windows Terminal), so it knows about the new PATH.

**macOS and Linux.** You need Python 3.10 or newer. Download the package from the [Download page](../download.html),
then in a terminal:
```
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install ~/Downloads/localagent-0.6.0-py3-none-any.whl
localagent setup
```

## 2. Choose your models

The guided setup asks three short questions; Enter always accepts the suggestion shown in brackets.

1. **An online model** — fast and strong. Google Gemini's key is free (no card needed); you can also paste your own key
   for OpenAI, Anthropic, OpenRouter, Groq, Mistral or DeepSeek. The key is checked before anything is saved.
2. **An offline model** — runs on your computer: private, no quota, slower. A model server you already run (Ollama,
   LM Studio, llama.cpp...) is found and offered first; otherwise setup can download a free model sized for your
   memory.
3. **Docker** — optional. With Docker Desktop running, the agent's code runs in a sealed box.

You can pick one model or both. With both, the online model answers and the offline one takes over by itself the
moment the internet goes away. [Choose a model](choosing-a-model.md) explains every option, and the
[setup builder](../setup.html) shows the exact steps for your choice.

## 3. Your first task

Open a terminal **in a project folder** and start the chat:
```
cd my-project
localagent
```
Then just ask:
```
you> What does this project do?
you> The tests fail - find the bug and fix it.
you> Add a --verbose flag to the command line, with a test.
```
For a question, it reads what it needs and answers. For a task, it plans, edits, checks each file it writes, runs
your tests, and finishes with a short summary. You see a diff of every edit as it happens, and before anything runs
on your computer you are asked — unless one of your own rules allows it.

## Everyday commands

| Type | What happens |
|---|---|
| `/plan <task>` | it looks first, shows a numbered plan, and only starts when you say yes |
| `/undo` | takes the last change back — your own later edits stay |
| `/model` | shows who answers; `/model offline`, `/model online`, `/model auto` switch |
| `/resume` | continues an earlier conversation in this project |
| `/clear` | starts a fresh conversation |
| `@file.py` | attaches a file to your request |
| `!command` | runs a command yourself, without asking the model |
| `# note` | adds a note to the project's `AGENT.md`, which the agent reads every time |
| `/help` | everything else |

Outside the chat: `localagent -p "question"` answers once (handy in scripts), `localagent --doctor` checks the whole
setup, and `localagent setup` lets you change your choices.

## Where your things are kept

- **Your settings and keys:** `%USERPROFILE%\.localagent\settings.json` on Windows, `~/.localagent/settings.json`
  elsewhere — only your user account can read it.
- **A project's conversations and rules:** the `.localagent` folder inside that project.
- **Downloaded models and llama.cpp:** `%USERPROFILE%\.localagent\models` and `...\llama`.

## Update or remove it

To update, download the new version and replace the folder (Windows) or run `pipx install --force` with the new
package — your settings stay. To remove it, double-click `uninstall.cmd` and delete the folder (Windows) or run
`pipx uninstall localagent`; delete the `.localagent` folder in your home folder to remove settings and models too.

Something not working? See [Troubleshooting](troubleshooting.md). Every command, flag and setting is in the
[User manual](05-user-manual.md).
