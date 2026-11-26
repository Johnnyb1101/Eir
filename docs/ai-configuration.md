# Personal AI configuration

Eir supports Anthropic, OpenAI, and a local Ollama server. Shared defaults live in
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

## OpenAI

Set `provider: openai` and `model: gpt-6.2-sol` in `config.local.yaml`. Set
`OPENAI_API_KEY` in the environment before starting Eir. Eir sends prompts and
retrieved source text to the OpenAI Responses API. Requests use your project’s
API usage and credits. To use the same model for an optional page check, set
`visual.provider: openai` and `visual.model: gpt-6.2-sol` in the visual mapping.

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

The selected-page extraction command uses this choice; ordinary ingestion never
triggers visual AI automatically. See [visual extraction](visual-extraction.md).
Ollama must report vision capability for the chosen model. Anthropic rejects
unsupported model/input combinations through its API. Eir never switches models
or providers automatically. Interpretations are not added to the corpus.

## Validation limits

Configuration validation checks structure and supported provider names. It does
not contact providers, verify model availability, or check credentials. Those
are checked by the provider when a request is made.
