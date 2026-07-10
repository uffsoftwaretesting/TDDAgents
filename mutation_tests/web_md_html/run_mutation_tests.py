#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import json

# Setup paths (derived from this script's location and the active interpreter,
# so the runner works regardless of the machine/checkout location).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # mutation_tests/web_md_html
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))        # repo root
VENV_BIN = os.path.dirname(sys.executable)                        # bin/ of active venv
EXEC_BASE = os.path.join(PROJECT_ROOT, "experimental_executions/web_md_to_html")
WORKSPACES = {
    "Workspace 1": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_md_html_1_tdd-543084c7b4f8d606"),
        "config_template": "config_w1.cfg",
    },
    "Workspace 2": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_md_html_2_tdd-c7659e1557c3f96f"),
        "config_template": "config_w2.cfg",
    },
    "Workspace 3": {
        "path": os.path.join(EXEC_BASE, "workspace_output_web_md_html_3_tdd-92cbf2125bca3611"),
        "config_template": "config_w3.cfg",
    }
}

# Tests that are not behavioral (structure/lint/static-analysis/filesystem checks).
# They break or add noise inside mutmut's sandbox, so they are temporarily renamed
# out of the way for the mutation run and restored afterwards.
NON_BEHAVIORAL_TESTS = {
    "Workspace 1": [
        "tests/test_setup.py",
        "tests/test_docs_and_openapi.py",
    ],
    "Workspace 2": [],
    "Workspace 3": [
        "tests/unit/test_entrypoint_and_linting.py",
        "tests/unit/test_imports.py",
        "tests/integration/test_openapi_and_docs.py",
        "tests/integration/test_app_startup.py",
    ],
}

# Workspaces do not use a top-level src package with layout issues in web_md_html.
STRIP_SRC_PREFIX = {
    "Workspace 1": False,
    "Workspace 2": False,
    "Workspace 3": False,
}

# PYTHONPATH used for each workspace (must match the ``pythonpath`` of its setup.cfg template): ``.``
PYTHONPATH_BY_WS = {
    "Workspace 1": ".",
    "Workspace 2": ".",
    "Workspace 3": ".",
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
    print(f"\n========================================\nRunning mutation tests for {ws_name}...\n========================================")

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

    # Strip the import prefix if necessary (not used for web_md_html)
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
    print(f"[{ws_name}] Generating and running mutants...")
    res_run = run_cmd("mutmut run", path)
    if res_run.returncode != 0:
        print(f"[{ws_name}] mutmut run exited with code {res_run.returncode}")
        print(f"STDOUT:\n{res_run.stdout}\nSTDERR:\n{res_run.stderr}")

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

    print(f"[{ws_name}] Done. Killed: {stats.get('killed', 0)}/{stats.get('total', 0)} (Survived: {len(survived_list)}, Timeouts: {len(timeouts_list)})")

# Save results JSON
results_json_path = os.path.join(SCRIPT_DIR, "results.json")
with open(results_json_path, "w", encoding="utf-8") as f:
    json.dump(results_data, f, indent=4)
print(f"Saved raw results to {results_json_path}")

# 5. Generate the markdown report
print("\nGenerating consolidated report...")
report_path = os.path.join(SCRIPT_DIR, "mutation_report_web_md_html.md")

def mutation_score(stats):
    total = stats.get('total', 0)
    killed = stats.get('killed', 0)
    return (killed / total * 100) if total > 0 else 0.0

with open(report_path, "w", encoding="utf-8") as f:
    f.write("# Relatório de Testes de Mutação - TDDAgents (Web Markdown to HTML Converter)\n\n")
    f.write("Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais do conversor de Markdown para HTML via API web (FastAPI).\n\n")
    f.write("Os mutantes foram gerados apenas sobre a **lógica de conversão** de cada execução (serviços de conversão), excluindo o *glue* de framework (FastAPI `main`/rotas, *schemas* e configuração). Testes não comportamentais (configuração, inicialização, importações, OpenAPI/documentação e linting/estrutura) foram desativados durante a execução por não contribuírem para matar mutantes.\n\n")

    # Write summary table (metric-row, workspace-column format)
    f.write("## 📊 Resumo das Execuções\n\n")
    f.write("| Métrica | Workspace 1 (`..._1_tdd-543084c...`) | Workspace 2 (`..._2_tdd-c7659e1...`) | Workspace 3 (`..._3_tdd-92cbf21...`) |\n")
    f.write("| :--- | :---: | :---: | :---: |\n")

    w1_stats = results_data["Workspace 1"]["stats"]
    w2_stats = results_data["Workspace 2"]["stats"]
    w3_stats = results_data["Workspace 3"]["stats"]

    w1_total = w1_stats.get('total', 0)
    w2_total = w2_stats.get('total', 0)
    w3_total = w3_stats.get('total', 0)

    # Row: Total de Mutantes
    f.write(f"| **Total de Mutantes** | {w1_total} | {w2_total} | {w3_total} |\n")

    # Row: Killed (Mortos)
    w1_killed = w1_stats.get('killed', 0)
    w1_killed_pct = (w1_killed / w1_total * 100) if w1_total > 0 else 0.0
    w2_killed = w2_stats.get('killed', 0)
    w2_killed_pct = (w2_killed / w2_total * 100) if w2_total > 0 else 0.0
    w3_killed = w3_stats.get('killed', 0)
    w3_killed_pct = (w3_killed / w3_total * 100) if w3_total > 0 else 0.0
    f.write(f"| **Killed (Mortos)** | {w1_killed} ({w1_killed_pct:.2f}%) | {w2_killed} ({w2_killed_pct:.2f}%) | {w3_killed} ({w3_killed_pct:.2f}%) |\n")

    # Row: Survived (Sobreviventes)
    w1_survived = len(results_data["Workspace 1"]["survived"])
    w1_survived_pct = (w1_survived / w1_total * 100) if w1_total > 0 else 0.0
    w2_survived = len(results_data["Workspace 2"]["survived"])
    w2_survived_pct = (w2_survived / w2_total * 100) if w2_total > 0 else 0.0
    w3_survived = len(results_data["Workspace 3"]["survived"])
    w3_survived_pct = (w3_survived / w3_total * 100) if w3_total > 0 else 0.0
    f.write(f"| **Survived (Sobreviventes)** | {w1_survived} ({w1_survived_pct:.2f}%) | {w2_survived} ({w2_survived_pct:.2f}%) | {w3_survived} ({w3_survived_pct:.2f}%) |\n")

    # Row: Timeouts
    w1_timeout = len(results_data["Workspace 1"]["timeouts"])
    w1_timeout_pct = (w1_timeout / w1_total * 100) if w1_total > 0 else 0.0
    w2_timeout = len(results_data["Workspace 2"]["timeouts"])
    w2_timeout_pct = (w2_timeout / w2_total * 100) if w2_total > 0 else 0.0
    w3_timeout = len(results_data["Workspace 3"]["timeouts"])
    w3_timeout_pct = (w3_timeout / w3_total * 100) if w3_total > 0 else 0.0
    f.write(f"| **Timeouts** | {w1_timeout} ({w1_timeout_pct:.2f}%) | {w2_timeout} ({w2_timeout_pct:.2f}%) | {w3_timeout} ({w3_timeout_pct:.2f}%) |\n")

    # Row: Score de Mutação
    f.write(f"| **Score de Mutação** | **{mutation_score(w1_stats):.2f}%** | **{mutation_score(w2_stats):.2f}%** | **{mutation_score(w3_stats):.2f}%** |\n\n")
    f.write("O Score de Mutação é calculado como `Killed / Total`. Mutantes sobreviventes funcionalmente equivalentes devem ser inspecionados manualmente nas seções de detalhes abaixo.\n")
    f.write("\n---\n\n")

    # Write details for each workspace
    for ws_name in WORKSPACES.keys():
        data = results_data[ws_name]
        f.write(f"## 🔍 Detalhes - {ws_name}\n\n")
        f.write(f"- **Total de Mutantes:** {data['stats'].get('total', 0)}\n")
        f.write(f"- **Killed:** {data['stats'].get('killed', 0)}\n")
        f.write(f"- **Survived:** {len(data['survived'])}\n")
        f.write(f"- **Timeout:** {len(data['timeouts'])}\n\n")

        if data['survived']:
            f.write("### Mutantes Sobreviventes\n\n")
            for mut in data['survived']:
                f.write(f"<details>\n<summary><code>{mut}</code> (survived)</summary>\n\n")
                f.write("```diff\n")
                f.write(data['diffs'].get(mut, "Diff não disponível.") + "\n")
                f.write("```\n\n</details>\n\n")
        else:
            f.write("Não houve mutantes sobreviventes neste workspace.\n\n")

        if data['timeouts']:
            f.write("### Mutantes com Timeout\n\n")
            for mut in data['timeouts']:
                f.write(f"<details>\n<summary><code>{mut}</code> (timeout)</summary>\n\n")
                f.write("```diff\n")
                f.write(data['diffs'].get(mut, "Diff não disponível.") + "\n")
                f.write("```\n\n</details>\n\n")
        f.write("\n---\n\n")

print(f"Report generated successfully at {report_path}!")
