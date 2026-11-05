from screening.domain.result import StageSentiment, observed_sentiment


def test_sentiment_requires_literal_candidate_evidence_and_unique_stage() -> None:
    result = observed_sentiment(
        [
            StageSentiment(stage="opening", label="positive", evidence="Me interesa mucho"),
            StageSentiment(stage="requirements", label="frustrated", evidence="Inventado"),
            StageSentiment(stage="opening", label="neutral", evidence=None),
        ],
        ["Me interesa mucho este trabajo."],
    )
    assert result == [
        {"stage": "opening", "label": "positive", "evidence": "Me interesa mucho"},
        {"stage": "requirements", "label": "unclear", "evidence": None},
    ]
