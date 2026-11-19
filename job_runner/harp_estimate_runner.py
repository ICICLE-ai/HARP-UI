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
import socket
import sys

SKOPS_TRUSTED_TYPES = {"LR": set(), "DTR": {"sklearn.tree._tree.Tree"}}


def _lock_down_process():
    """Best-effort process limits; the server also enforces a wall-clock timeout."""
    try:
        import resource
    except ImportError:  # Windows does not provide POSIX rlimits.
        return
    limits = (
        (resource.RLIMIT_CORE, 0, 0),
        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024, 64 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 128, 128),
        (resource.RLIMIT_CPU, 170, 170),
    )
    memory_mb = int(os.environ.get("HARP_ESTIMATE_MEMORY_MB", "4096"))
    if memory_mb > 0:
        limits += ((resource.RLIMIT_AS, memory_mb * 1024 * 1024, memory_mb * 1024 * 1024),)
    for kind, soft, hard in limits:
        try:
            resource.setrlimit(kind, (soft, hard))
        except (OSError, ValueError):
            pass


def _deny_network_and_child_processes():
    """Inference never needs network access or child processes."""
    def denied(*_args, **_kwargs):
        raise PermissionError("network access is disabled during model inference")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied

    def audit(event, _args):
        if event.startswith("socket.connect") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
            raise PermissionError(f"{event} is disabled during model inference")

    sys.addaudithook(audit)


def _load_model(artifact_dir, model_name, model_type):
    if model_type in {"LR", "DTR"}:
        try:
            from sklearn.linear_model import LinearRegression
            from sklearn.tree import DecisionTreeRegressor
            import skops.io as sio
        except ImportError as e:
            raise RuntimeError(f"prediction dependencies are not installed: {e.name or e}") from e
        model_path = os.path.join(artifact_dir, "models", model_name + ".skops")
        untrusted = set(sio.get_untrusted_types(file=model_path))
        approved = SKOPS_TRUSTED_TYPES[model_type]
        unexpected = untrusted - approved
        if unexpected:
            raise RuntimeError("model contains unapproved serialized types: " + ", ".join(sorted(unexpected)))
        model = sio.load(model_path, trusted=sorted(untrusted))
        expected = LinearRegression if model_type == "LR" else DecisionTreeRegressor
        if type(model) is not expected:
            raise RuntimeError(f"expected {expected.__name__}, got {type(model).__name__}")
        return model
    if model_type == "NN":
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        try:
            import tensorflow as tf
        except ImportError as e:
            raise RuntimeError("TensorFlow is required to use this neural-network model") from e
        return tf.keras.models.load_model(
            os.path.join(artifact_dir, "models", model_name + ".keras"), compile=False, safe_mode=True)
    raise RuntimeError(f"unsupported model type {model_type!r}")


def run(artifact_dir, request):
    try:
        import numpy as np
        import pandas as pd
        from sklearn.linear_model import LinearRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as e:
        raise RuntimeError(f"prediction dependencies are not installed: {e.name or e}") from e

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
    model = _load_model(artifact_dir, model_name, model_type)
    if model_type in {"LR", "DTR"}:
        predicted = model.predict(model_input)
    elif model_type == "NN":
        predicted = model.predict(model_input, verbose=0).reshape(-1)

    factor = float(request.get("adjust_factor") or 1)
    return {"seconds": [max(0.0, float(v) * factor) for v in np.asarray(predicted).reshape(-1)],
            "preprocess_max_error": error}


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    _lock_down_process()
    _deny_network_and_child_processes()
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
