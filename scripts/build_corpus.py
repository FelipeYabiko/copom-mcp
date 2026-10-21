"""Gera src/copom_mcp/data/ a partir da API do Banco Central (rodado por quem mantém o pacote)."""

import logging
import sys
from pathlib import Path

from copom_mcp.corpus import BCBClient, build_corpus, save_corpus

logging.getLogger("pypdf").setLevel(logging.ERROR)
DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "copom_mcp" / "data"

if __name__ == "__main__":
    meetings, chunks = build_corpus(BCBClient(), progress=lambda msg: print(msg, file=sys.stderr))
    save_corpus(DATA_DIR, meetings, chunks)
    print(f"{len(meetings)} reuniões, {len(chunks)} parágrafos em {DATA_DIR}")
