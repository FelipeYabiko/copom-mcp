"""Hook PostToolUse: formata e verifica cada arquivo Python que o Claude edita.

Recebe o evento em JSON pela entrada padrão. Se sobrar erro de lint, sai com código 2: o texto em
stderr volta para o Claude, que corrige na hora em vez de descobrir só no CI.
"""

import json
import subprocess
import sys

event = json.load(sys.stdin)
path = event.get("tool_input", {}).get("file_path", "")
if not path.endswith(".py"):
    sys.exit(0)

subprocess.run(["ruff", "format", "--quiet", path], check=False)
result = subprocess.run(["ruff", "check", "--fix", "--quiet", path], capture_output=True, text=True, check=False)
if result.returncode != 0:
    print(f"ruff encontrou problemas em {path}:\n{result.stdout}{result.stderr}", file=sys.stderr)
    sys.exit(2)
