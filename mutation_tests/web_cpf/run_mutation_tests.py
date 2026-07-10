#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import json

# Setup paths (derived from this script's location and the active interpreter,
# so the runner works regardless of the machine/checkout location).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # mutation_tests/web_cpf
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))        # repo root
VENV_BIN = os.path.dirname(sys.executable)                        # bin/ of active venv
EXEC_BASE = os.path.join(PROJECT_ROOT, "experimental_executions/web_cpf")
WORKSPACES = {
    "Workspace 1": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_cpf_1_tdd-6b937ffc0f349cbc"),
        "config_template": "config_w1.cfg",
    },
    "Workspace 2": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_cpf_2_tdd-10b251d42f79b5ff"),
        "config_template": "config_w2.cfg",
    },
    "Workspace 3": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_cpf_3_tdd-d8441751f9876ab1"),
        "config_template": "config_w3.cfg",
    }
}

# Tests that are not behavioral (structure/lint/static-analysis/filesystem checks).
# They break or add noise inside mutmut's sandbox, so they are temporarily renamed
# out of the way for the mutation run and restored afterwards.
NON_BEHAVIORAL_TESTS = {
    "Workspace 1": [],
    "Workspace 2": [
        "tests/test_structure.py",
        "tests/test_readme_and_lint.py",
    ],
    "Workspace 3": [
        "tests/test_directories.py",
        "tests/test_file_existence.py",
        "tests/test_static_analysis.py",
    ],
}

# Workspaces 1 and 3 import their source through a top-level ``src`` package
# ("from src.domain.cpf import ...").  mutmut roots its sandbox at ``mutants/src``
# and names mutants without the ``src.`` prefix, and its import trampoline rejects
# any module whose name starts with ``src.``.  So, like the cep runner did for its
# test imports, we strip the ``src.`` prefix from every .py file (source + tests)
# of those workspaces before the run and restore them afterwards.  Workspace 2 has
# its packages at the project root (no ``src`` prefix) and needs no rewriting.
STRIP_SRC_PREFIX = {
    "Workspace 1": True,
    "Workspace 2": False,
    "Workspace 3": True,
}

# PYTHONPATH used for each workspace (must match the ``pythonpath`` of its
# setup.cfg template): ``src`` for the src-layout workspaces, ``.`` for W2.
PYTHONPATH_BY_WS = {
    "Workspace 1": "src",
    "Workspace 2": ".",
    "Workspace 3": "src",
}

# Add venv to environment; PYTHONPATH is set per-workspace inside the loop.
env = os.environ.copy()
env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
env["PYTHONPATH"] = "."

def run_cmd(cmd, cwd, env=env):
    res = subprocess.run(cmd, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res

results_data = {}

for ws_name, ws_info in WORKSPACES.items():
    path = ws_info["path"]
    cfg_template = ws_info["config_template"]

    # 1. Clean up mutants and cache
    run_cmd("rm -rf mutants .mutmut-cache setup.cfg", path)

    # Backup original setup.cfg if any
    setup_cfg_path = os.path.join(path, "setup.cfg")
    setup_cfg_backup = None
    if os.path.exists(setup_cfg_path):
        with open(setup_cfg_path, 'r') as f:
            setup_cfg_backup = f.read()

    # Backup and remove original pytest.ini if any (W1/W3 ship one; W3 sets
    # --cov-fail-under=90 which would fail every mutant). Our setup.cfg drives pytest.
    pytest_ini_path = os.path.join(path, "pytest.ini")
    pytest_ini_backup = None
    if os.path.exists(pytest_ini_path):
        with open(pytest_ini_path, 'r') as f:
            pytest_ini_backup = f.read()
        os.remove(pytest_ini_path)

    # Backup and remove original pyproject.toml if any
    pyproject_toml_path = os.path.join(path, "pyproject.toml")
    pyproject_toml_backup = None
    if os.path.exists(pyproject_toml_path):
        with open(pyproject_toml_path, 'r') as f:
            pyproject_toml_backup = f.read()
        os.remove(pyproject_toml_path)

    # Set the PYTHONPATH expected by this workspace's layout.
    env["PYTHONPATH"] = PYTHONPATH_BY_WS.get(ws_name, ".")

    # Workspace-specific preprocessing: rename non-behavioral tests so they do
    # not run during the mutmut run (they assert on files/lint/structure and
    # fail or add noise inside the sandbox).
    renamed_files = []
    for rel in NON_BEHAVIORAL_TESTS.get(ws_name, []):
        test_path = os.path.join(path, rel)
        if os.path.exists(test_path):
            shutil.move(test_path, test_path + ".bak")
            renamed_files.append((test_path + ".bak", test_path))

    # Strip the ``src.`` import prefix from every .py file (source + tests) so the
    # mutated modules can be imported as top-level packages inside mutmut's sandbox.
    files_to_restore = {}
    created_init_files = []
    if STRIP_SRC_PREFIX.get(ws_name):
        for sub in ("src", "tests"):
            base = os.path.join(path, sub)
            for root, _dirs, files in os.walk(base):
                for filename in files:
                    if not filename.endswith(".py"):
                        continue
                    filepath = os.path.join(root, filename)
                    with open(filepath, 'r') as f:
                        content = f.read()
                    if "src." in content:
                        files_to_restore[filepath] = content
                        with open(filepath, 'w') as f:
                            f.write(content.replace("src.", ""))

        # Once the ``src.`` prefix is gone, the test sub-packages (e.g. ``interfaces``,
        # ``application``) share names with the source packages. Make ``tests`` a real
        # package (tests/__init__.py + an __init__.py in every test sub-directory) so
        # pytest roots at the workspace and imports tests under the ``tests.`` namespace
        # instead of prepending the tests dirs and shadowing the source packages.
        tests_base = os.path.join(path, "tests")
        for root, _dirs, _files in os.walk(tests_base):
            init_path = os.path.join(root, "__init__.py")
            if not os.path.exists(init_path):
                open(init_path, "w").close()
                created_init_files.append(init_path)

    # 2. Copy the configuration template
    config_src = os.path.join(SCRIPT_DIR, cfg_template)
    shutil.copy(config_src, setup_cfg_path)

    # 3. Run mutmut
    res_run = run_cmd("mutmut run", path)

    # Run export-cicd-stats
    run_cmd("mutmut export-cicd-stats", path)

    # Load stats
    stats = {}
    stats_json_path = os.path.join(path, "mutants/mutmut-cicd-stats.json")
    if os.path.exists(stats_json_path):
        with open(stats_json_path, 'r') as f:
            stats = json.load(f)

    # Run mutmut results to get lists
    res_list = run_cmd("mutmut results", path)
    survived_list = []
    timeouts_list = []
    for line in res_list.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if ": survived" in line:
            mut_name = line.split(":")[0].strip()
            survived_list.append(mut_name)
        elif ": timeout" in line:
            mut_name = line.split(":")[0].strip()
            timeouts_list.append(mut_name)

    # Get diffs for survived and timeouts
    diffs = {}
    for mut in survived_list + timeouts_list:
        diff_res = run_cmd(f"mutmut show {mut}", path)
        diffs[mut] = diff_res.stdout

    results_data[ws_name] = {
        "stats": stats,
        "survived": survived_list,
        "timeouts": timeouts_list,
        "diffs": diffs
    }

    # 4. Clean up and restore workspace
    run_cmd("rm -rf mutants .mutmut-cache setup.cfg", path)
    if setup_cfg_backup:
        with open(setup_cfg_path, 'w') as f:
            f.write(setup_cfg_backup)

    if pytest_ini_backup:
        with open(pytest_ini_path, 'w') as f:
            f.write(pytest_ini_backup)

    if pyproject_toml_backup:
        with open(pyproject_toml_path, 'w') as f:
            f.write(pyproject_toml_backup)

    for src, dst in renamed_files:
        if os.path.exists(src):
            shutil.move(src, dst)

    # Restore the original (un-stripped) source/test files
    for filepath, original in files_to_restore.items():
        with open(filepath, 'w') as f:
            f.write(original)

    # Remove the __init__.py files we created for the run
    for init_path in created_init_files:
        if os.path.exists(init_path):
            os.remove(init_path)

# Save results JSON
results_json_path = os.path.join(SCRIPT_DIR, "results.json")
with open(results_json_path, "w", encoding="utf-8") as f:
    json.dump(results_data, f, indent=4)

# Report writing omitted
