#!/usr/bin/env python3
"""Offline authored-fixture validation; never imports or invokes the agent harness.

Default execution never writes into the frozen suite. --write-report is permitted
only before FREEZE.json exists. All execution is authored stdlib fixture code in
fresh temporary copies; child processes have finite timeouts and minimal env.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SUITE = Path(__file__).resolve().parent
ENV = {"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}
BOOTSTRAP = r'''
import copy, importlib, json, os, sys
sys.path.insert(0, os.getcwd())
request = json.loads(sys.stdin.read())
try:
    fn = getattr(importlib.import_module(request["module"]), request["function"])
    values = [fn(*copy.deepcopy(args)) for args in request["inputs"]]
    print(json.dumps({"complete": True, "observations": values}, allow_nan=False))
except Exception as error:
    print(json.dumps({"complete": False, "error_type": type(error).__name__}))
'''


def load_oracle(name):
    path = SUITE / "oracles" / (name + ".py")
    spec = importlib.util.spec_from_file_location("oracle_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe(root, oracle):
    request = {key: oracle[key] for key in ("module", "function", "inputs")}
    child = subprocess.run([sys.executable, "-I", "-B", "-c", BOOTSTRAP],
                           cwd=root, input=json.dumps(request), text=True,
                           capture_output=True, timeout=5, env=ENV)
    if child.returncode or child.stderr:
        return {"complete": False}
    try:
        return json.loads(child.stdout)
    except ValueError:
        return {"complete": False}


def public_tests(root):
    code = ('import unittest,sys;sys.path.insert(0,".");'
            'suite=unittest.defaultTestLoader.discover("tests");'
            'result=unittest.TextTestRunner(verbosity=0).run(suite);'
            'sys.exit(not result.wasSuccessful())')
    child = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=root,
                           text=True, capture_output=True, timeout=5, env=ENV)
    return child.returncode == 0


def same(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same(actual[k], value)
                                                        for k, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def evaluate(root, case):
    got = observe(root, case["oracle"])
    return {"public_tests_pass": public_tests(root),
            "oracle_matches": got.get("complete") is True
                              and same(got.get("observations"), case["oracle"]["expected"])}


def verify_freeze():
    freeze_path = SUITE / "FREEZE.json"
    if not freeze_path.exists():
        return None
    freeze = json.loads(freeze_path.read_text())
    actual = {str(p.relative_to(SUITE)) for p in SUITE.rglob("*") if p.is_file()
              and p != freeze_path}
    assert actual == set(freeze["files"]), "inventory changed"
    for relative, digest in freeze["files"].items():
        path = SUITE / relative
        assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, relative
    return hashlib.sha256(freeze_path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    if args.write_report and (SUITE / "FREEZE.json").exists():
        raise SystemExit("cannot rewrite frozen validation")
    freeze_sha = verify_freeze()
    manifest = json.loads((SUITE / "manifest.json").read_text())
    mutations = json.loads((SUITE / "mutation_cases.json").read_text())
    report = {"kind": "offline_authored_fixture_selfvalidation",
              "model_requests": 0, "harness_executed": False, "cases": []}
    for case in manifest["cases"]:
        oracle_module = load_oracle(case["id"])
        inputs = oracle_module.cases()
        want = [{"result": oracle_module.expected(*values), "inputs_unchanged": True}
                for values in inputs]
        assert same(inputs, case["oracle"]["inputs"]), "input freeze differs from oracle generator"
        assert same(want, case["oracle"]["expected"]), "expected freeze differs from external oracle"
        before = {relative: hashlib.sha256((SUITE / case["workspace_dir"] / relative).read_bytes()).hexdigest()
                  for relative in case["immutable_files"]}
        assert before == case["immutable_sha256"]
        with tempfile.TemporaryDirectory(prefix="arl-holdout-v3-") as temporary:
            temporary = Path(temporary)
            baseline = temporary / "baseline"
            shutil.copytree(SUITE / case["workspace_dir"], baseline)
            baseline_result = evaluate(baseline, case)
            assert not baseline_result["public_tests_pass"] and not baseline_result["oracle_matches"]
            reference = temporary / "reference"
            shutil.copytree(baseline, reference)
            ref_dir = SUITE / case["reference_dir"]
            for source in ref_dir.rglob("*.py"):
                target = reference / source.relative_to(ref_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            reference_result = evaluate(reference, case)
            assert all(reference_result.values()), "reference failed"
            mutation_results = []
            for mutation in mutations[case["id"]]:
                mutant = temporary / mutation["id"]
                shutil.copytree(reference, mutant)
                path = mutant / mutation["path"]
                text = path.read_text()
                assert text.count(mutation["old"]) == 1
                path.write_text(text.replace(mutation["old"], mutation["new"]))
                result = evaluate(mutant, case)
                assert not all(result.values()), "mutation unexpectedly accepted"
                mutation_results.append({"id": mutation["id"], "expected": "reject",
                                         "rejected": True, **result})
            assert before == {relative: hashlib.sha256((reference / relative).read_bytes()).hexdigest()
                              for relative in case["immutable_files"]}
            report["cases"].append({"id": case["id"], "oracle_inputs": len(inputs),
                                    "baseline": baseline_result, "reference": reference_result,
                                    "mutations": mutation_results})
    if args.write_report:
        (SUITE / "validation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(report["cases"]),
                      "reference_passes": sum(all(case["reference"].values()) for case in report["cases"]),
                      "baseline_rejections": sum(not any(case["baseline"].values()) for case in report["cases"]),
                      "mutation_rejections": sum(len(case["mutations"]) for case in report["cases"]),
                      "oracle_inputs": sum(case["oracle_inputs"] for case in report["cases"]),
                      "freeze_sha256": freeze_sha, "model_requests": 0,
                      "harness_executed": False}, sort_keys=True))


if __name__ == "__main__":
    main()

