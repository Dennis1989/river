"""River API module."""

from . import (
    anomaly,
    base,
    cluster,
    compat,
    compose,
    datasets,
    drift,
    dummy,
    ensemble,
    evaluate,
    facto,
    feature_extraction,
    feature_selection,
    imblearn,
    linear_model,
    metrics,
    misc,
    model_selection,
    multiclass,
    multioutput,
    naive_bayes,
    neighbors,
    neural_net,
    optim,
    preprocessing,
    proba,
    reco,
    rules,
    stats,
    stream,
    time_series,
    tree,
    utils,
)
from .datasets import synth

__all__ = [
    "anomaly",
    "base",
    "cluster",
    "compat",
    "compose",
    "datasets",
    "dummy",
    "drift",
    "ensemble",
    "evaluate",
    "facto",
    "feature_extraction",
    "feature_selection",
    "imblearn",
    "linear_model",
    "metrics",
    "misc",
    "model_selection",
    "multiclass",
    "multioutput",
    "naive_bayes",
    "neighbors",
    "neural_net",
    "optim",
    "preprocessing",
    "proba",
    "reco",
    "rules",
    "stats",
    "stream",
    "synth",
    "time_series",
    "tree",
    "utils",
]