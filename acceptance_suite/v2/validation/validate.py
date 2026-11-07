"""Self-validation only: no agent runtime, model, API, or credentials are used."""

import contextlib
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SUITE = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def candidate_module(workspace, module_name):
    prior_path = list(sys.path)
    prior_modules = dict(sys.modules)
    sys.path.insert(0, str(workspace))
    try:
        yield importlib.import_module(module_name)
    finally:
        sys.path[:] = prior_path
        for name in list(sys.modules):
            module = sys.modules[name]
            filename = getattr(module, "__file__", None)
            if filename and Path(filename).resolve().is_relative_to(workspace.resolve()):
                sys.modules.pop(name, None)
        for name, module in prior_modules.items():
            if name not in sys.modules:
                sys.modules[name] = module


def run_public(workspace):
    environment = dict(os.environ)
    environment.pop("DEEPSEEK_API_KEY", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"],
                               cwd=str(workspace), env=environment,
                               capture_output=True, text=True)
    return {"returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr}


def check_oracle(workspace, case):
    oracle = case["oracle"]
    failures = []
    with candidate_module(workspace, oracle["module"]) as module:
        function = getattr(module, oracle["function"])
        for index, (args, expected) in enumerate(zip(oracle["inputs"], oracle["expected"])):
            args = copy.deepcopy(args)
            before = copy.deepcopy(args)
            actual = function(*args)
            json.dumps(actual)
            if actual != expected or args != before:
                failures.append(index)
    return {"total": len(oracle["inputs"]), "failed_indices": failures}


def main():
    manifest = json.loads((SUITE / "manifest.json").read_text())
    results = {"protocol": "independent-plain-python-v1", "cases": []}
    for case in manifest["cases"]:
        original = SUITE / case["workspace_dir"]
        immutable = {name: hashlib.sha256((original / name).read_bytes()).hexdigest()
                     for name in case["immutable_files"]}
        reference_name = "reference_" + case["oracle"]["module"]
        with tempfile.TemporaryDirectory(prefix="acceptance-v2-") as temporary:
            temporary = Path(temporary)
            baseline = temporary / "baseline"
            corrected = temporary / "reference"
            shutil.copytree(original, baseline)
            shutil.copytree(original, corrected)
            shutil.copyfile(SUITE / "validation" / (reference_name + ".py"),
                            corrected / (case["oracle"]["module"] + ".py"))
            record = {"id": case["id"], "baseline_public": run_public(baseline),
                      "baseline_oracle": check_oracle(baseline, case),
                      "reference_public": run_public(corrected),
                      "reference_oracle": check_oracle(corrected, case),
                      "immutable_sha256": immutable}
            assert record["baseline_public"]["returncode"] != 0
            assert record["baseline_oracle"]["failed_indices"]
            assert record["reference_public"]["returncode"] == 0
            assert not record["reference_oracle"]["failed_indices"]
            for name, digest in immutable.items():
                assert hashlib.sha256((corrected / name).read_bytes()).hexdigest() == digest
            results["cases"].append(record)
    (SUITE / "validation" / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"validated_cases": len(results["cases"]),
                      "oracle_inputs": sum(r["reference_oracle"]["total"] for r in results["cases"]),
                      "reference_public_all_pass": True, "reference_oracle_all_pass": True,
                      "baseline_public_all_fail": True, "baseline_oracle_all_fail": True}))


if __name__ == "__main__":
    main()
