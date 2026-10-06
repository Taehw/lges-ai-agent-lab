import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from recommender import config
from recommender.engine import Recommendation

SYSTEM_PROMPT = """너는 집밥 요리 도우미다.
사용자가 가진 재료와, 이미 선정된 추천 요리 목록이 주어진다.
요리마다 다음을 한국어 2~3문장으로 설명하라.
- 사용자가 가진 재료 중 무엇을 활용하는지, 왜 지금 만들기 좋은지
- 부족한 재료가 있으면, 사용자가 가진 재료나 흔한 재료로 대체할 수 있는지 (대체가 어려우면 솔직하게 사러 가야 한다고 말하라)
주어진 요리 외의 다른 요리를 추천하지 말고, 레시피에 없는 재료를 지어내지 마라."""


class _Explanation(BaseModel):
    id: str = Field(description="레시피 id")
    explanation: str = Field(description="추천 이유와 대체 재료 제안")


class _Explanations(BaseModel):
    items: list[_Explanation]


class LLMExplainer:
    def __init__(self, model: str = config.CHAT_MODEL):
        llm = ChatOpenAI(model=model, temperature=0.3, timeout=30)
        prompt = ChatPromptTemplate.from_messages(
            [("system", SYSTEM_PROMPT), ("human", "보유 재료: {owned}\n\n추천 요리:\n{recipes}")]
        )
        self._chain = prompt | llm.with_structured_output(_Explanations)

    def explain(self, owned: list[str], recommendations: list[Recommendation]) -> dict[str, str]:
        if not recommendations:
            return {}
        recipes = [
            {
                "id": s.recipe.id,
                "name": s.recipe.name,
                "main_ingredients": s.recipe.main_names,
                "matched": s.matched,
                "missing": s.missing,
            }
            for s in recommendations
        ]
        result: _Explanations = self._chain.invoke(
            {"owned": ", ".join(owned), "recipes": json.dumps(recipes, ensure_ascii=False, indent=1)}
        )
        return {item.id: item.explanation for item in result.items}
