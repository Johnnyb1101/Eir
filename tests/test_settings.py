import pytest
from src.settings import load_config


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("provider: anthropic\nmodel: shared-model\n", encoding="utf-8")
    return path


def test_shared_defaults_disable_visual_processing(base):
    settings = load_config(base)
    assert settings.provider == "anthropic"
    assert settings.model == "shared-model"
    assert settings.visual is None
    assert settings.source_folder == "corpus/"


def test_local_source_folder_override(base):
    base.with_name("config.local.yaml").write_text(
        'source_folder: "D:/Synced/Clinical Guidelines"\n', encoding="utf-8"
    )
    assert load_config(base).source_folder == "D:/Synced/Clinical Guidelines"


def test_personal_override_and_independent_visual_choice(base):
    original = base.read_bytes()
    base.with_name("config.local.yaml").write_text(
        "provider: ollama\nmodel: local-model\nvisual:\n"
        "  provider: anthropic\n  model: visual-model\n", encoding="utf-8")
    settings = load_config(base)
    assert (settings.provider, settings.model) == ("ollama", "local-model")
    assert (settings.visual.provider, settings.visual.model) == ("anthropic", "visual-model")
    assert base.read_bytes() == original


def test_local_openai_provider_and_visual_model(base):
    base.with_name("config.local.yaml").write_text(
        "provider: openai\nmodel: gpt-6.2-sol\nvisual:\n"
        "  provider: openai\n  model: gpt-6.2-sol\n", encoding="utf-8",
    )

    settings = load_config(base)
    assert (settings.provider, settings.model) == ("openai", "gpt-6.2-sol")
    assert (settings.visual.provider, settings.visual.model) == ("openai", "gpt-6.2-sol")


@pytest.mark.parametrize("local", [
    "provider: other\nmodel: example\n",
    "provider: ollama\n",
    "model: '   '\n",
    "model: 123\n",
    "source_folder: '   '\n",
    "source_folder: 123\n",
    "visual:\n  provider: ollama\n",
    "visual: true\n",
    "api_key: do-not-store-keys-here\n",
    "- not-a-mapping\n",
    "",
    "model: [broken\n",
])
def test_invalid_local_configuration_is_rejected(base, local):
    base.with_name("config.local.yaml").write_text(local, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(base)


def test_local_can_disable_shared_visual_settings(base):
    base.write_text(base.read_text() + "visual:\n  provider: ollama\n  model: vision\n")
    base.with_name("config.local.yaml").write_text("visual: null\n")
    assert load_config(base).visual is None


def test_partial_visual_override_does_not_inherit_model(base):
    base.write_text(base.read_text() + "visual:\n  provider: anthropic\n  model: vision\n")
    base.with_name("config.local.yaml").write_text("visual:\n  provider: ollama\n")
    with pytest.raises(ValueError):
        load_config(base)
