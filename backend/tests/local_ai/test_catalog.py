from pathlib import Path

from app.local_ai.catalog import LOCAL_AI_PRESETS, get_local_ai_preset_catalog


def test_catalog_exposes_only_supported_local_presets() -> None:
    catalog = get_local_ai_preset_catalog()

    assert catalog.catalog_version == 1
    assert {preset.key for preset in catalog.presets} == {
        "multilingual-e5-small",
        "bge-m3",
        "sensevoice-small",
    }
    assert all(preset.model_source == "modelscope" for preset in catalog.presets)
    assert {
        preset.model_id
        for preset in catalog.presets
    } == {
        "intfloat/multilingual-e5-small",
        "BAAI/bge-m3",
        "iic/SenseVoiceSmall",
    }
    assert next(
        preset for preset in catalog.presets if preset.key == "bge-m3"
    ).vector_dimensions == 1024


def test_catalog_keys_match_checked_loader_presets() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    preset_directory = repository_root / "docker" / "model-presets"
    manifest_ids = {
        manifest_path.stem
        for manifest_path in preset_directory.glob("*.json")
        if manifest_path.name != "schema.json"
    }

    assert {preset.key for preset in LOCAL_AI_PRESETS} == manifest_ids
