"""Prototype API app — NOT mounted by ``zolai.api.server.create_app``.

Kept because ``prediction_router`` is exercised through it
(``tests/test_prediction_api.py``). Its ``/verify-grammar`` rule list is
pre-canonical: the served grammar routes live in ``server.py`` and the lexicon
lives in ``dictionary`` / ``dictionary_en_zo``, read through ``DatabaseManager``.

``/search-dict`` was removed: it queried ``entries`` / ``translations`` in
``data/master_unified_dictionary.db``, a retired store that does not exist at
that path, so the endpoint answered 500 to every request. Nothing is left here
that opens a second database.
"""

from typing import List

from fastapi import FastAPI
from pydantic import BaseModel

from zolai.api.prediction_api import router as prediction_router

app = FastAPI(title="Zolai Tool API")


class GrammarQuery(BaseModel):
    sentence: str


class GrammarResponse(BaseModel):
    is_valid: bool
    feedback: str
    suggestions: List[str]


@app.post("/verify-grammar", response_model=GrammarResponse)
def verify_grammar(payload: GrammarQuery):
    sentence = payload.sentence
    feedback = []
    suggestions = []
    is_valid = True

    # Simple Zolai Standard Rule Checks (as per project mandates)
    if "uhhi" in sentence and ("i " in sentence or "i-" in sentence):
        is_valid = False
        feedback.append("Invalid plurality: 'uhhi' should not be used with first-person inclusive 'i' (we).")
        suggestions.append("Use 'i [verb] hi' instead.")

    if "lo leh" in sentence:
        is_valid = False
        feedback.append("Use 'kei' for conditionals.")
        suggestions.append("Replace 'lo leh' with 'kei leh'.")

    if is_valid:
        return GrammarResponse(
            is_valid=True,
            feedback="Sentence appears to follow Tedim Zolai Standard.",
            suggestions=[],
        )
    else:
        return GrammarResponse(is_valid=False, feedback="; ".join(feedback), suggestions=suggestions)


app.include_router(prediction_router)
