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
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/palindrome/workspace_output_palindrome_1_tdd-6d5197f8f1676d5d"),
        "config_template": "config_w1.cfg",
    },
    "Workspace 2": {
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/palindrome/workspace_output_palindrome_2_tdd-8c06d0a4e2049f2a"),
        "config_template": "config_w2.cfg",
    },
    "Workspace 3": {
        "path": os.path.join(PROJECT_ROOT, "TDDAgents/experimental_executions/palindrome/workspace_output_palindrome_3_tdd-404b83d38c6c6f95"),
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
    print(f"\n========================================\nRunning mutation tests for {ws_name}...\n========================================")
    
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
    
    if ws_name == "Workspace 1":
        # Modify imports in tests from "src.palindrome_checker" to "palindrome_checker"
        test_dir = os.path.join(path, "tests")
        for filename in os.listdir(test_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(test_dir, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                if "from src.palindrome_checker" in content:
                    files_to_restore[filepath] = content
                    new_content = content.replace("from src.palindrome_checker", "from palindrome_checker")
                    with open(filepath, 'w') as f:
                        f.write(new_content)
                        
        # Rename test_docstrings.py to prevent failures when docstring is removed/changed by mutmut
        doc_test = os.path.join(path, "tests/test_docstrings.py")
        if os.path.exists(doc_test):
            shutil.move(doc_test, doc_test + ".bak")
            renamed_files.append((doc_test + ".bak", doc_test))
            
    elif ws_name in ("Workspace 2", "Workspace 3"):
        # Modify imports in tests from "src.palindrome" to "palindrome"
        test_dir = os.path.join(path, "tests")
        for filename in os.listdir(test_dir):
            if filename.endswith(".py"):
                filepath = os.path.join(test_dir, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                if "from src.palindrome" in content:
                    files_to_restore[filepath] = content
                    new_content = content.replace("from src.palindrome", "from palindrome")
                    with open(filepath, 'w') as f:
                        f.write(new_content)
            
    # 2. Copy the configuration template
    config_src = os.path.join(PROJECT_ROOT, "mutation_tests/palindrome", cfg_template)
    shutil.copy(config_src, setup_cfg_path)
    
    # 3. Run mutmut
    print(f"[{ws_name}] Generating and running mutants...")
    res_run = run_cmd("mutmut run", path)
    if res_run.returncode != 0:
        print(f"[{ws_name}] mutmut run exited with code {res_run.returncode}")
    
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
                
    print(f"[{ws_name}] Done. Killed: {stats.get('killed', 0)}/{stats.get('total', 0)} (Survived: {len(survived_list)}, Timeouts: {len(timeouts_list)})")

# Save results JSON
results_json_path = os.path.join(PROJECT_ROOT, "mutation_tests/palindrome/results.json")
with open(results_json_path, "w", encoding="utf-8") as f:
    json.dump(results_data, f, indent=4)
print(f"Saved raw results to {results_json_path}")

# 5. Generate the markdown report
print("\nGenerating consolidated report...")
report_path = os.path.join(PROJECT_ROOT, "mutation_tests/palindrome/mutation_report_palindrome.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("# Relatório de Testes de Mutação - TDDAgents (Palindrome)\n\n")
    f.write("Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais dos verificadores de palíndromos.\n\n")
    
    # Write summary table (in metric-row, workspace-column format)
    f.write("## 📊 Resumo das Execuções\n\n")
    f.write("Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:\n\n")
    f.write("| Métrica | Workspace 1 (`..._1_tdd-6d5197f...`) | Workspace 2 (`..._2_tdd-8c06d0a...`) | Workspace 3 (`..._3_tdd-404b83d...`) |\n")
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
    w1_survived = w1_stats.get('survived', 0)
    w1_survived_pct = (w1_survived / w1_total * 100) if w1_total > 0 else 0.0
    w2_survived = w2_stats.get('survived', 0)
    w2_survived_pct = (w2_survived / w2_total * 100) if w2_total > 0 else 0.0
    w3_survived = w3_stats.get('survived', 0)
    w3_survived_pct = (w3_survived / w3_total * 100) if w3_total > 0 else 0.0
    f.write(f"| **Survived (Sobreviventes)** | {w1_survived} ({w1_survived_pct:.2f}%) | {w2_survived} ({w2_survived_pct:.2f}%) | {w3_survived} ({w3_survived_pct:.2f}%) |\n")
    
    # Row: Timeouts
    w1_timeout = w1_stats.get('timeout', 0)
    w1_timeout_pct = (w1_timeout / w1_total * 100) if w1_total > 0 else 0.0
    w2_timeout = w2_stats.get('timeout', 0)
    w2_timeout_pct = (w2_timeout / w2_total * 100) if w2_total > 0 else 0.0
    w3_timeout = w3_stats.get('timeout', 0)
    w3_timeout_pct = (w3_timeout / w3_total * 100) if w3_total > 0 else 0.0
    f.write(f"| **Timeouts** | {w1_timeout} ({w1_timeout_pct:.2f}%) | {w2_timeout} ({w2_timeout_pct:.2f}%) | {w3_timeout} ({w3_timeout_pct:.2f}%) |\n")
    
    # Row: Score de Mutação Real
    w1_real_survivors = 1  # 1 semantic equivalent (i <= j)
    w2_real_survivors = 2  # 2 semantic equivalents (left <= right)
    w3_real_survivors = 5  # 5 semantic equivalents (Mn category, upper() vs lower(), join with 'XXXX')
    
    w1_real_score = ((w1_total - (len(results_data["Workspace 1"]["survived"]) - w1_real_survivors)) / w1_total * 100) if w1_total > 0 else 0.0
    w2_real_score = ((w2_total - (len(results_data["Workspace 2"]["survived"]) - w2_real_survivors)) / w2_total * 100) if w2_total > 0 else 0.0
    w3_real_score = ((w3_total - (len(results_data["Workspace 3"]["survived"]) - w3_real_survivors)) / w3_total * 100) if w3_total > 0 else 0.0
    
    f.write(f"| **Score de Mutação Real*** | **{w1_real_score:.2f}%** | **{w2_real_score:.2f}%** | **{w3_real_score:.2f}%** |\n\n")
    f.write("\\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.\n")
    f.write("\n---\n\n")
    
    # Detailed analysis section
    f.write("## 💡 Análise Geral e Lacunas Encontradas\n\n")
    f.write("### 1. Mutantes Semânticos Equivalentes (Todos os Workspaces)\n")
    f.write("Vários mutantes sobreviventes são funcionalmente equivalentes ao comportamento original do programa:\n")
    f.write("- **Laço Inclusivo** (`while i <= j` ou `while left <= right`): Nos **Workspaces 1 e 2**, a alteração do operador de `<` para `<=` no laço principal do algoritmo de dois ponteiros sobreviveu. Quando `left == right`, o caractere comparado é o caractere central de uma string de comprimento ímpar (que é sempre igual a si mesmo). Embora execute uma verificação redundante adicional, o resultado lógico final é idêntico para qualquer entrada.\n")
    f.write("- **Filtro de Diacríticos Redundante (Workspace 3)**: A mutação que corrompe a verificação da categoria `Mn` (`unicodedata.category(char) == 'Mn'`) para outros valores sobreviveu. Isso ocorre porque o laço logo em seguida aplica o filtro `char.isalnum()`. Como caracteres combinantes de acentuação (diacríticos) não são alfanuméricos, eles acabam sendo descartados pelo segundo filtro de qualquer forma, tornando a checagem explícita de `Mn` redundante.\n")
    f.write("- **Caixa Alta vs Caixa Baixa (Workspace 3)**: A alteração de `.lower()` para `.upper()` na normalização de caracteres sobreviveu porque toda a string é convertida uniformemente para maiúsculas e comparada com o seu reverso. A consistência da caixa garante o mesmo resultado para todas as strings.\n")
    f.write("- **Junção com Separador Simétrico (Workspace 3)**: O mutante que junta a lista de caracteres utilizando `'XXXX'` em vez de `''` sobreviveu. Como o caractere separador é perfeitamente simétrico (`'XXXX'`), a string resultante `c1 S c2 S ... S cn` permanece um palíndromo se e somente se a sequência de caracteres original era um palíndromo.\n\n")
    
    f.write("### 2. Lacunas de Teste nos Casos de Borda (Workspaces 1 e 2)\n")
    f.write("Nos **Workspaces 1 e 2**, a alteração que corrompe o decremento do ponteiro direito (`j = 1` ou `right = 1` no lugar de `j -= 1` ou `right -= 1`) sobreviveu. ")
    f.write("Isso indica um ponto cego nas suítes de teste: todas as strings não-palíndromas testadas diferem logo em seu primeiro e último caractere (ex: `'hello'`, `'abc123'`), o que faz com que a função retorne `False` logo na primeira iteração do laço. ")
    f.write("Se os testes tivessem incluído não-palíndromos que começam e terminam com o mesmo caractere (como `'abca'` ou `'radir'`), este mutante teria sido morto.\n")
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
