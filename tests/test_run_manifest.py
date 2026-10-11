"""A saved run records the version of CADL Explorer that produced it."""

import json

from backend.services.pipeline import run_pipeline
from backend.services.run_manager import RunDir, replay_run
from backend.version import __version__


def test_manifest_records_the_pipeline_version(tmp_path):
    result = run_pipeline(template="D-SoS", profile="uniform", rho=0.0, num_seeds=2)
    RunDir(tmp_path).save_pipeline_result(result)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["pipeline_version"] == __version__ == result.pipeline_version


def test_replay_still_works_with_the_extra_key(tmp_path):
    result = run_pipeline(template="D-SoS", profile="uniform", rho=0.0, num_seeds=2)
    RunDir(tmp_path).save_pipeline_result(result)

    replayed = replay_run(str(tmp_path))
    assert replayed.template == result.template
    assert len(replayed.results) == len(result.results)
