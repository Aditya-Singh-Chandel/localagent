# Choose a model

localagent does not come with a brain of its own — it works with the model you choose. There are three kinds, and you
can combine them: an online model for speed, an offline one as the backup.

| | Online, free | Online, your key | On your computer |
|---|---|---|---|
| Example | Google Gemini | OpenAI, Anthropic, Groq... | Qwen3 with llama.cpp, Ollama |
| Cost | free daily quota | billed per use by the service | free |
| Needs | internet, a Google account | internet, an account there | memory: 6 GB free for the 8B model |
| Speed per step | 1-3 s | a few seconds | 20-60 s on a laptop processor |
| Your code goes to | Google | that service | nowhere |

## Google Gemini — the free start

Make a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey): sign in with Google, click "Create API
key", copy it, and paste it when `localagent setup` asks. No card is needed. The agent uses the cheapest Gemini models
first, which have large daily quotas, and calls in the stronger ones only when a task struggles. Quotas reset at
midnight, US Pacific time.

On Gemini's free tier, Google may use what is sent to improve its products. For private code, use an offline model —
or turn on billing for your Google project.

## A service you already pay for

Choose it in `localagent setup` and paste your key; a key you have already set for other tools (for example
`OPENAI_API_KEY`) is found by itself. Setup checks the key by listing the service's models — which costs nothing —
then suggests a model that works well with the agent. You can type any other model the service offers.

| Service | Key variable | Where to make a key | Notes |
|---|---|---|---|
| OpenAI | `OPENAI_API_KEY` | platform.openai.com/api-keys | pay per use |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com/settings/keys | pay per use |
| OpenRouter | `OPENROUTER_API_KEY` | openrouter.ai/keys | one key for hundreds of models, some free |
| Groq | `GROQ_API_KEY` | console.groq.com/keys | free tier, very fast |
| Mistral | `MISTRAL_API_KEY` | console.mistral.ai/api-keys | free tier |
| DeepSeek | `DEEPSEEK_API_KEY` | platform.deepseek.com/api_keys | very low prices |
| any OpenAI-compatible address | `LOCALAGENT_PROVIDER_KEY` | — | Together, Fireworks, a company gateway, your own GPU server |

To try another service for one run only: `localagent --provider groq`, or with a model:
`localagent --provider openai:gpt-5-mini`.

## A model on your computer

**You already run one?** Ollama (port 11434), LM Studio (1234), llama.cpp's server (8080), vLLM (8000) and Jan (1337)
are found by `localagent setup` when they run at their usual address, and offered first with their models. A server on
another computer works too: choose "a model server at another address" and type it, for example
`http://192.168.1.20:8080/v1`.

**Nothing yet?** Choose "download a free model for me". Setup downloads llama.cpp (25-40 MB) and the Qwen3 model that
suits your memory — every file checked against its publisher's checksum, and a broken download continues where it
stopped. The agent starts the model when it is needed and stops it when you leave.

| Your computer's memory | Suggested model | Download | Good for |
|---|---|---|---|
| 16 GB or more | Qwen3 8B | 5.0 GB | the best that fits; most everyday tasks |
| 8 GB | Qwen3 4B | 2.5 GB | twice as fast, weaker on bigger tasks |
| less than 8 GB | Qwen3 1.7B | 1.8 GB | questions and very small tasks |

Local models are slower and less capable than the online ones — they shine when you are offline, when the code is
private, or when the quota is used up. A graphics card makes them faster; the agent uses it automatically.

## Online, offline, or automatic

| Mode | Who answers |
|---|---|
| `auto` (default) | the online model; your local model the moment the online one cannot be reached |
| `online` | the online model only |
| `offline` | your local model only — nothing leaves your computer |

Switch at any time in the chat with `/model online`, `/model offline` or `/model auto` — the conversation and your
files stay. Start in a mode with `localagent --offline` or `--online`. After every answer, the last line names the
model that answered, so you always know whether the internet was used.

Not sure? The [setup builder](../setup.html) turns your choices into exact steps. Every setting behind these choices
is in [Online and offline](01-setup-and-models.md).
