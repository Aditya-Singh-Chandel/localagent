## This website

- **No cookies, no analytics, no tracking.** The site does not count, log or follow you.
- **Nothing from other sites.** Fonts, scripts and pictures are part of the site itself, so opening a page contacts
  only the server it comes from. The one thing remembered in your browser is the colour theme you choose with the
  switch — stored only on your device.
- **Downloads** come from this site too. Their checksums are listed on the [Download page](download.html).

## The localagent program

- **No telemetry.** The program never reports anything about you or your use.
- **On your computer** it keeps your settings and keys (`.localagent/settings.json` in your home folder, readable only
  by your user account), each project's conversations and rules (the `.localagent` folder of that project), and daily
  usage counts for the online quotas.
- **Online mode** sends your requests and the parts of your project the agent reads to the online service you chose
  (Google Gemini, OpenAI, Anthropic...), under that service's own terms. On Gemini's free tier, Google may use them to
  improve its products.
- **Offline mode** sends nothing anywhere: the model runs on your computer and listens only to your computer.
- **Downloads during setup** come from their publishers — llama.cpp from GitHub, models from Hugging Face — and those
  services see a normal download request. Every file is checked against its publisher's checksum.
