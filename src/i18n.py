import json
from pathlib import Path


DEFAULT_LANGUAGE = "en"
LOCALES_DIR = Path(__file__).parent / "locales"
LANGUAGE_LABELS = {
    "en": "English",
    "es": "Español",
}

_translations = None


def _flatten_keys(data, prefix=""):
    keys = set()
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(_flatten_keys(value, full_key))
        else:
            keys.add(full_key)
    return keys


def _lookup(data, key):
    current = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def load_translations():
    translations = {}
    for language in LANGUAGE_LABELS:
        path = LOCALES_DIR / f"{language}.json"
        with path.open("r", encoding="utf-8") as file:
            translations[language] = json.load(file)
    return translations


def translations():
    global _translations
    if _translations is None:
        _translations = load_translations()
    return _translations


def available_languages():
    return LANGUAGE_LABELS.copy()


def supported_language(language):
    return language in LANGUAGE_LABELS


def language_label(language):
    return LANGUAGE_LABELS.get(language, LANGUAGE_LABELS[DEFAULT_LANGUAGE])


def current_language(json_data=None):
    language = (json_data or {}).get("botLanguage", DEFAULT_LANGUAGE)
    return language if supported_language(language) else DEFAULT_LANGUAGE


def t(json_data, key, **kwargs):
    language = current_language(json_data)
    loaded = translations()
    text = _lookup(loaded.get(language, {}), key)
    if text is None:
        text = _lookup(loaded.get(DEFAULT_LANGUAGE, {}), key)
    if text is None:
        text = key
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError):
        return text


def validate_locale_keys():
    loaded = translations()
    base_keys = _flatten_keys(loaded[DEFAULT_LANGUAGE])
    differences = {}
    for language, values in loaded.items():
        keys = _flatten_keys(values)
        missing = sorted(base_keys - keys)
        extra = sorted(keys - base_keys)
        if missing or extra:
            differences[language] = {"missing": missing, "extra": extra}
    return differences
