import hashlib
import os

import pytest

from harp_server.build import BuildManager
from job_runner.harp_estimate_runner import _load_model

skops = pytest.importorskip("skops.io")
sklearn = pytest.importorskip("sklearn")
from sklearn.linear_model import LinearRegression
from sklearn.tree import DecisionTreeRegressor


class UnexpectedModel:
    def predict(self, values):
        return values


def _write_model(tmp_path, model, name="model"):
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    skops.dump(model, models / f"{name}.skops")
    return name


def test_skops_loader_accepts_only_the_expected_estimator(tmp_path):
    name = _write_model(tmp_path, LinearRegression().fit([[0], [1]], [1, 2]))
    assert type(_load_model(str(tmp_path), name, "LR")) is LinearRegression

    _write_model(tmp_path, DecisionTreeRegressor().fit([[0], [1]], [1, 2]), "tree")
    assert type(_load_model(str(tmp_path), "tree", "DTR")) is DecisionTreeRegressor
    with pytest.raises(RuntimeError, match="unapproved serialized types"):
        _load_model(str(tmp_path), "tree", "LR")


def test_skops_loader_rejects_unapproved_serialized_types(tmp_path):
    name = _write_model(tmp_path, UnexpectedModel())
    with pytest.raises(RuntimeError, match="unapproved serialized types"):
        _load_model(str(tmp_path), name, "LR")


def _build_with_local_artifacts(tmp_path, manifest):
    manager = BuildManager(str(tmp_path))
    build_id = "secure01"
    out = tmp_path / "builds" / build_id / "out"
    (out / "models").mkdir(parents=True)
    files = {"training.csv": b"feature,walltime\n1,2\n", "full_dataset_pca.csv": b"uniq_id,walltime\n1,2\n",
             "models/LR_SD_no.skops": b"not loaded when integrity validation fails"}
    for rel, data in files.items():
        path = out.joinpath(*rel.split("/"))
        path.write_bytes(data)
    manager.builds[build_id] = {
        "id": build_id, "owner": "alice", "status": "DONE", "dataset_file": "training.csv",
        "metrics": [{"MODEL_NAME": "LR_SD_no", "RegModel": "LR", "ADJ_FACTOR": 1}],
        "artifact_manifest": manifest, "storage": {"system_id": "storage"}, "dest": "/models",
    }
    return manager, files


def _manifest(files):
    return {rel: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)} for rel, data in files.items()}


def test_estimate_rejects_legacy_build_without_fingerprints(tmp_path):
    manager, _ = _build_with_local_artifacts(tmp_path, None)
    with pytest.raises(RuntimeError, match="rebuild it"):
        manager.estimate("alice", "secure01", "LR_SD_no", [{"feature": 1}], None)


def test_estimate_rejects_modified_artifact(tmp_path):
    manager, files = _build_with_local_artifacts(tmp_path, {})
    manifest = _manifest(files)
    manager.builds["secure01"]["artifact_manifest"] = manifest
    model_path = tmp_path / "builds" / "secure01" / "out" / "models" / "LR_SD_no.skops"
    model_path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="integrity check"):
        manager.estimate("alice", "secure01", "LR_SD_no", [{"feature": 1}], None)


def test_estimate_rejects_traversal_in_model_name(tmp_path):
    manager, _ = _build_with_local_artifacts(tmp_path, None)
    manager.builds["secure01"]["metrics"][0]["MODEL_NAME"] = "../outside"
    with pytest.raises(ValueError, match="model name is invalid"):
        manager.estimate("alice", "secure01", "../outside", [{"feature": 1}], None)


def test_verified_skops_model_runs_in_inference_process(tmp_path):
    manager = BuildManager(str(tmp_path))
    build_id = "secure02"
    out = tmp_path / "builds" / build_id / "out"
    (out / "models").mkdir(parents=True)
    (out / "training.csv").write_text(
        "run_config,run_type,run_x,walltime\na,SD,1,10\nb,SD,2,20\nc,test_data,3,30\n"
    )
    (out / "full_dataset_pca.csv").write_text(
        "pc1,walltime,run_type,uniq_id\n-1.224744871391589,10,SD,1\n0,20,SD,2\n1.224744871391589,30,test_data,3\n"
    )
    model = LinearRegression().fit([[-1.224744871391589], [0], [1.224744871391589]], [10, 20, 30])
    skops.dump(model, out / "models" / "LR_SD_no.skops")
    rels = ["training.csv", "full_dataset_pca.csv", "models/LR_SD_no.skops"]
    manifest = {}
    for rel in rels:
        data = out.joinpath(*rel.split("/")).read_bytes()
        manifest[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manager.builds[build_id] = {
        "id": build_id, "owner": "alice", "status": "DONE", "dataset_file": "training.csv",
        "metrics": [{"MODEL_NAME": "LR_SD_no", "RegModel": "LR", "ADJ_FACTOR": 1}],
        "artifact_manifest": manifest, "storage": {"system_id": "storage"}, "dest": "/models",
    }
    result = manager.estimate("alice", build_id, "LR_SD_no", [{"run_x": 4}], None)
    assert result["seconds"][0] == pytest.approx(40)
