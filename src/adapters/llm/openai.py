from langchain_openai import ChatOpenAI

MODEL_ID = "gpt-6.1-terra"
CONVERSATION_MODEL_ID = MODEL_ID
ROLE_EFFORT = {
    "conversador": "low",
    "guardian": "medium",
    "evaluador": "medium",
    "juez": "medium",
    "simulador": "medium",
    "copiloto": "medium",
    "senales": "medium",
}
ROLE_MAX_TOKENS = {
    "conversador": 4096,
    "guardian": 2048,
    "evaluador": 8192,
    "juez": 4096,
    "simulador": 2048,
    "copiloto": 4096,
    "senales": 1024,
}


def build_openai_model(role: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=CONVERSATION_MODEL_ID if role == "conversador" else MODEL_ID,
        use_responses_api=True,
        reasoning={"effort": ROLE_EFFORT[role]},
        max_completion_tokens=ROLE_MAX_TOKENS[role],
        store=False,
        include=["reasoning.encrypted_content"],
        # Signals must never hold a candidate's turn for long; the caller also bounds them.
        timeout={"evaluador": 120, "juez": 120, "senales": 10}.get(role, 60),
        max_retries=1 if role == "senales" else 2,
    )
