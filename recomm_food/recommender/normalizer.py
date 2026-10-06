import json
from pathlib import Path


class IngredientNormalizer:
    """동의어 사전으로 재료명을 표준 재료명으로 바꾼다."""

    def __init__(self, synonyms: dict[str, str] | None = None):
        self._synonyms = {self._key(k): v for k, v in (synonyms or {}).items()}

    @classmethod
    def from_json(cls, path: Path) -> "IngredientNormalizer":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @staticmethod
    def _key(name: str) -> str:
        return "".join(name.split()).lower()

    def normalize(self, name: str) -> str:
        key = self._key(name)
        return self._synonyms.get(key, key)

    def normalize_all(self, names) -> list[str]:
        result: list[str] = []
        for name in names:
            if not name or not name.strip():
                continue
            canonical = self.normalize(name)
            if canonical not in result:
                result.append(canonical)
        return result

    def aliases(self) -> list[str]:
        return list(self._synonyms)
