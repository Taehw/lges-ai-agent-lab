import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Ingredient:
    name: str
    amount: str


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    category: str
    cook_time_min: int
    difficulty: str
    servings: int
    main_ingredients: tuple[Ingredient, ...]
    seasonings: tuple[Ingredient, ...]
    steps: tuple[str, ...]
    tags: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict) -> "Recipe":
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            cook_time_min=int(data["cook_time_min"]),
            difficulty=data["difficulty"],
            servings=int(data["servings"]),
            main_ingredients=tuple(Ingredient(**i) for i in data["main_ingredients"]),
            seasonings=tuple(Ingredient(**i) for i in data["seasonings"]),
            steps=tuple(data["steps"]),
            tags=tuple(data.get("tags", [])),
        )

    @property
    def main_names(self) -> list[str]:
        return [i.name for i in self.main_ingredients]

    @property
    def seasoning_names(self) -> list[str]:
        return [i.name for i in self.seasonings]

    def to_search_text(self) -> str:
        return (
            f"요리명: {self.name}\n"
            f"카테고리: {self.category}\n"
            f"주재료: {', '.join(self.main_names)}\n"
            f"태그: {', '.join(self.tags)}"
        )


class RecipeRepository:
    def __init__(self, recipes: list[Recipe]):
        self._recipes = list(recipes)
        self._by_id = {r.id: r for r in self._recipes}

    @classmethod
    def from_json(cls, path: Path) -> "RecipeRepository":
        with open(path, encoding="utf-8") as f:
            return cls([Recipe.from_dict(d) for d in json.load(f)])

    def all(self) -> list[Recipe]:
        return list(self._recipes)

    def get(self, recipe_id: str) -> Recipe | None:
        return self._by_id.get(recipe_id)

    def main_ingredient_names(self) -> list[str]:
        return sorted({n for r in self._recipes for n in r.main_names})

    def seasoning_names(self) -> list[str]:
        return sorted({n for r in self._recipes for n in r.seasoning_names})
