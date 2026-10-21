"""Hook Stop: antes de o Claude encerrar a tarefa, roda os testes rápidos.

Se falharem, bloqueia o encerramento e devolve o motivo, para o Claude corrigir. Para não entrar em
loop, só bloqueia uma vez seguida (o campo `stop_hook_active` indica que já estamos nessa situação).
"""

import json
import subprocess
import sys

event = json.load(sys.stdin)
if event.get("stop_hook_active"):
    sys.exit(0)

result = subprocess.run(["pytest", "-q", "-x", "--no-header"], capture_output=True, text=True, check=False)
if result.returncode != 0:
    tail = "\n".join(result.stdout.splitlines()[-25:])
    print(json.dumps({"decision": "block", "reason": f"Os testes estão falhando. Corrija antes de terminar:\n{tail}"}))
sys.exit(0)
