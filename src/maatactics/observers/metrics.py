"""Field-level exact-match metrics, including abstentions and negative frames."""

from statistics import median


def summarize(samples: list[dict], *, binary: bool = False) -> dict:
    tp = fp = fn = tn = unknown = 0
    latency = []
    for sample in samples:
        expected, actual = sample["expected"], sample["actual"]
        unknown += actual is None
        if expected is None:
            if actual is None:
                tn += 1
            else:
                fp += 1
        elif actual == expected:
            tp += 1
        else:
            fn += 1
            fp += actual is not None
        if sample.get("latency_ms") is not None:
            latency.append(sample["latency_ms"])
    result = {
        "samples": len(samples),
        "correct": tp,
        "false_positive": fp,
        "missed": fn,
        "correct_unknown": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "unknown_ratio": unknown / len(samples) if samples else None,
        "median_latency_ms": median(latency) if latency else None,
        "max_latency_ms": max(latency) if latency else None,
    }
    if binary:
        positives = [s for s in samples if s["expected"] is True]
        binary_tp = sum(s["actual"] is True for s in positives)
        binary_fp = sum(s["actual"] is True and s["expected"] is not True for s in samples)
        result["positive_class"] = {
            "true_positive": binary_tp,
            "false_positive": binary_fp,
            "false_negative": len(positives) - binary_tp,
            "true_negative": sum(s["expected"] is False and s["actual"] is False for s in samples),
            "precision": binary_tp / (binary_tp + binary_fp) if binary_tp + binary_fp else None,
            "recall": binary_tp / len(positives) if positives else None,
        }
    return result
