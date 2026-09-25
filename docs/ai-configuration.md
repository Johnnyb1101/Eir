# Personal AI configuration

Eir supports Anthropic and a local Ollama server. Shared defaults live in
`config.yaml`. To make a personal choice, copy `config.local.example.yaml` to
`config.local.yaml` in the project root and replace its placeholder model name.
Git ignores the local file. Do not put API keys in either configuration file.

Local top-level values override shared defaults. Changing the provider requires
an explicit model as well, so a cloud model name is not accidentally reused for
Ollama. Unknown settings, unsupported providers, and blank models are rejected.
Restart Eir after changing configuration; it loads settings at startup.

## Anthropic

Set `provider: anthropic` and `model` to a model available to your account.
Set `ANTHROPIC_API_KEY` in your environment. Eir does not load `.env` files itself.
Requests send prompts and retrieved source text to Anthropic and incur API usage.
An Anthropic client is created only when an Anthropic request is made.

## Ollama

Set `provider: ollama` and `model` to a model you have installed locally.
Start the Ollama server at `http://localhost:11434`. Eir does not install or
download models. No Anthropic key is required for Ollama generation.
Use a locally running model if documents must stay on your computer; this setting
does not control how your Ollama server itself is configured.

Provider failures are reported, not redirected to another provider. Choosing
Ollama never causes Eir to fall back to Anthropic.

## Independent visual-model settings

`visual: null` is the default. An optional `visual` mapping must contain both
`provider` and `model`, independently of ordinary generation. Local visual
settings replace the whole shared visual mapping, rather than merging its fields.

This release only validates and stores that choice. It does not yet send images,
interpret PDF visuals, verify model vision capabilities, or add interpretations
to the corpus. A later visual-extraction implementation will use these settings.

## Validation limits

Configuration validation checks structure and supported provider names. It does
not contact providers, verify model availability, or check credentials. Those
are checked by the provider when a request is made.
