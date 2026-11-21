# Troubleshooting

Start here: `localagent --doctor` checks the whole setup — the verification tools, the sandbox, the online model and
its key, the local model — and every problem it finds comes with the fix.

## "localagent" is not recognized

The installer added the folder to your PATH, but only terminals opened **afterwards** see it. Close the terminal and
open a new one. Still not found? Double-click `install.cmd` again (it is safe to repeat), or start `localagent.cmd`
from the unzipped folder directly. With pipx, run `python3 -m pipx ensurepath` and open a new terminal.

## "No model is available yet"

Nothing is set up: run `localagent setup`. The quickest start is a free Gemini key; for working without internet,
choose an offline model in step 2.

## The key is refused

Setup saves nothing when a service refuses a key — make a new key on the service's key page and run
`localagent setup` again. A key that was set by hand can be checked with `localagent --doctor`. When the doctor says
that a variable "is also set in your environment", that one wins over your saved choice: remove it from your
environment variables.

## "Daily quota used up" or "rate limit"

Gemini's free models have daily quotas; they reset at midnight, US Pacific time. Until then the agent answers with
your local model, if you set one up — `localagent setup` adds one. With your own billing, quotas are much higher
(`LOCALAGENT_GEMINI_TIER=paid`).

## No internet

In the default `auto` mode the local model takes over by itself and the online model returns when the connection
does. Without a local model, nothing can answer offline — run `localagent setup` and choose one.

## The local model is slow

On a laptop processor, 20-60 seconds per step is normal for the 8B model. To go faster: close memory-hungry programs,
use a smaller model (`localagent models download qwen3-4b`, then `localagent models use qwen3-4b`), or a computer with
a graphics card — the agent uses it automatically.

## The local model does not start

Usually there is not enough free memory: close other programs or use a smaller model. The model server's own log is
`.localagent\llama-server.log` in your home folder. If you chose Ollama or LM Studio in setup, that program must be
running — start it, or choose another model with `localagent setup`.

## "Docker is not running"

Docker is optional. Without it, the agent's code runs on your computer after your approval. To use the sealed box,
install Docker Desktop, start it, and build the sandbox image once — `localagent --doctor` prints the exact command.

## An edit was rejected

That is the safety net working: a file that fails its checks is put back exactly as it was, and the errors go to the
model, which tries again. If a task goes the wrong way, `/undo` takes the last change back, and `/clear` starts a
fresh conversation.

## Start over

Run `localagent setup` to change your choices. To forget everything, delete the `.localagent` folder in your home
folder (settings, keys, downloaded models) and the `.localagent` folder in a project (its conversations).

Still stuck? Every message, setting and behaviour is explained in the [User manual](05-user-manual.md) and in
[Run and verify](06-run-and-verify.md).
