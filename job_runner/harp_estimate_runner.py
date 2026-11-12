#!/usr/bin/env python3
"""Apply one HARP build artifact to new, fully specified profiling rows.

The historical HARP pipeline saved the original training CSV, its PCA output,
and the trained model, but not the fitted scaler/PCA objects.  The PCA transform
is affine, so this runner recovers that transform from the aligned saved rows,
verifies that it reproduces the saved PCA data, and only then predicts.
"""

import argparse
import json
import os
import pickle
import sys


def run(artifact_dir, request):
    try:
        import numpy as np
        import pandas as pd
        from sklearn.linear_model import LinearRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as e:
        raise RuntimeError(f"prediction dependencies are not installed: {e.name}") from e

    training = pd.read_csv(os.path.join(artifact_dir, request["dataset_file"]))
    transformed = pd.read_csv(os.path.join(artifact_dir, "full_dataset_pca.csv"))
    feature_names = [c for c in training.columns
                     if c not in {"run_config", "run_type", "sys_name", "sys_processor", "walltime", "uniq_id"}]
    if not feature_names or transformed.empty:
        raise RuntimeError("the build has no reusable feature data")

    # uniq_id is the original input row number (one based).  The preprocessor
    # may reorder SD/FS/test rows, so align with it rather than row position.
    ids = transformed["uniq_id"].astype(int).to_numpy() - 1
    if ids.min(initial=0) < 0 or ids.max(initial=-1) >= len(training):
        raise RuntimeError("the build's PCA rows cannot be aligned with its training data")
    aligned = training.iloc[ids][feature_names].reset_index(drop=True)
    categorical = [c for c in feature_names if aligned[c].dtype == "object"]

    incoming = pd.DataFrame(request["rows"])
    missing = [c for c in feature_names if c not in incoming.columns]
    if missing:
        raise RuntimeError("estimate is missing model features: " + ", ".join(missing))
    incoming = incoming[feature_names]
    for c in categorical:
        known = set(aligned[c].astype(str))
        unknown = sorted(set(incoming[c].astype(str)) - known)
        if unknown:
            raise RuntimeError(f"feature {c} has a value the model never saw: {unknown[0]}")

    encoded = pd.get_dummies(aligned, columns=categorical, dtype=float)
    new_encoded = pd.get_dummies(incoming, columns=categorical, dtype=float).reindex(
        columns=encoded.columns, fill_value=0)
    scaler = StandardScaler().fit(encoded)
    x = scaler.transform(encoded)
    pca_columns = [c for c in transformed.columns if c not in {"walltime", "run_type", "uniq_id"}]
    pca_values = transformed[pca_columns].to_numpy(dtype=float)
    recovery = LinearRegression().fit(x, pca_values)
    recovered = recovery.predict(x)
    error = float(np.max(np.abs(recovered - pca_values)))
    if not np.isfinite(error) or error > 1e-6:
        raise RuntimeError(f"could not verify the build's preprocessing transform (error {error:.3g})")
    model_input = recovery.predict(scaler.transform(new_encoded))

    model_name, model_type = request["model_name"], request["model_type"]
    if model_type in {"LR", "DTR"}:
        with open(os.path.join(artifact_dir, "models", model_name + ".pkl"), "rb") as f:
            model = pickle.load(f)
        predicted = model.predict(model_input)
    elif model_type == "NN":
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        try:
            import tensorflow as tf
        except ImportError as e:
            raise RuntimeError("TensorFlow is required to use this neural-network model") from e
        model = tf.keras.models.load_model(
            os.path.join(artifact_dir, "models", model_name + ".h5"), compile=False)
        predicted = model.predict(model_input, verbose=0).reshape(-1)
    else:
        raise RuntimeError(f"unsupported model type {model_type!r}")

    factor = float(request.get("adjust_factor") or 1)
    return {"seconds": [max(0.0, float(v) * factor) for v in np.asarray(predicted).reshape(-1)],
            "preprocess_max_error": error}


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.request, encoding="utf-8") as f:
            result = run(args.artifacts, json.load(f))
        result["ok"] = True
    except Exception as e:
        result = {"ok": False, "error": str(e)}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
