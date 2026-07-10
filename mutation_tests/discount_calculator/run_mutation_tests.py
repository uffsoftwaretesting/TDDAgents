#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import json

# Setup paths
VENV_BIN = "/home/amaro/tdd-agents/.venv/bin"
PROJECT_ROOT = "/home/amaro/artigo-ic-SBES"
WORKSPACES = {
    "Workspace 1": {
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/discount_calculator/workspace_output_discount_calculator_1_tdd-48bab129fac68276"),
        "config_template": "config_w1.cfg",
    },
    "Workspace 2": {
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/discount_calculator/workspace_output_discount_calculator_2_tdd-94dd47547c811c6d"),
        "config_template": "config_w2.cfg",
    },
    "Workspace 3": {
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/discount_calculator/workspace_output_discount_calculator_3_tdd-95a3c4c757b4aff4"),
        "config_template": "config_w3.cfg",
    }
}

# Add venv and pythonpath to environment
env = os.environ.copy()
env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
env["PYTHONPATH"] = "src"

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
            
    # Backup original pytest.ini if any
    pytest_ini_path = os.path.join(path, "pytest.ini")
    pytest_ini_backup = None
    if os.path.exists(pytest_ini_path):
        with open(pytest_ini_path, 'r') as f:
            pytest_ini_backup = f.read()
        os.remove(pytest_ini_path)
        
    # Backup original pyproject.toml if any
    pyproject_toml_path = os.path.join(path, "pyproject.toml")
    pyproject_toml_backup = None
    if os.path.exists(pyproject_toml_path):
        with open(pyproject_toml_path, 'r') as f:
            pyproject_toml_backup = f.read()
        os.remove(pyproject_toml_path)
            
    # Workspace-specific preprocessing
    files_to_restore = {}
    renamed_files = []
    
    if ws_name == "Workspace 2":
        # Modify imports in tests from "src.discount" to "discount"
        test_dir = os.path.join(path, "tests")
        for filename in os.listdir(test_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(test_dir, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                if "from src.discount" in content:
                    files_to_restore[filepath] = content
                    new_content = content.replace("from src.discount", "from discount")
                    with open(filepath, 'w') as f:
                        f.write(new_content)
                        
    elif ws_name == "Workspace 3":
        # Modify imports in tests from "src.desconto" to "desconto"
        test_dir = os.path.join(path, "tests")
        for filename in os.listdir(test_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(test_dir, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                if "from src.desconto" in content:
                    files_to_restore[filepath] = content
                    new_content = content.replace("from src.desconto", "from desconto")
                    with open(filepath, 'w') as f:
                        f.write(new_content)
                        
        # Rename test_desconto_docstring.py to prevent failures when docstring is removed/changed by mutmut
        doc_test = os.path.join(path, "tests/test_desconto_docstring.py")
        if os.path.exists(doc_test):
            shutil.move(doc_test, doc_test + ".bak")
            renamed_files.append((doc_test + ".bak", doc_test))
            
    # 2. Copy the configuration template
    config_src = os.path.join(PROJECT_ROOT, "mutation_tests/discount_calculator", cfg_template)
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
            
    for filepath, original in files_to_restore.items():
        with open(filepath, 'w') as f:
            f.write(original)
            
    for src, dst in renamed_files:
        if os.path.exists(src):
            shutil.move(src, dst)
                

# Save results JSON
results_json_path = os.path.join(PROJECT_ROOT, "mutation_tests/discount_calculator/results.json")
with open(results_json_path, "w", encoding="utf-8") as f:
    json.dump(results_data, f, indent=4)

# Report writing omitted
