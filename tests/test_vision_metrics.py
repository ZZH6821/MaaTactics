from maatactics.observers.metrics import summarize


def test_positive_ready_precision_counts_only_true_predictions_as_positives():
    result = summarize(
        [
            {"expected": True, "actual": True},
            {"expected": True, "actual": None},
            {"expected": False, "actual": True},
            {"expected": False, "actual": False},
        ],
        binary=True,
    )
    assert result["positive_class"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "true_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
    }


def test_abstention_reduces_recall_and_negative_frame_false_positive_reduces_precision():
    result = summarize(
        [
            {"expected": 1, "actual": 1},
            {"expected": 2, "actual": None},
            {"expected": None, "actual": 3},
            {"expected": None, "actual": None},
        ]
    )
    assert result["precision"] == 0.5 and result["recall"] == 0.5
    assert result["unknown_ratio"] == 0.5
    assert (
        result["false_positive"] == 1 and result["missed"] == 1 and result["correct_unknown"] == 1
    )
