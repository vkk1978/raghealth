"""Query embedders for canary queries.

Canary queries must be embedded with the SAME model your ingestion pipeline
uses, or the search results are meaningless. raghealth doesn't bundle a
model — it plugs into whatever you already use:

    embedder:
      type: openai
      model: text-embedding-3-small
      api_key: env:OPENAI_API_KEY

    embedder:
      type: sentence_transformers          # local, no API cost
      model: all-MiniLM-L6-v2

    embedder:
      type: command                        # escape hatch: any model, any stack
      cmd: "python my_embed.py"            # text on stdin -> JSON array on stdout

An embedder is just `callable(text: str) -> list[float]`.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

Embedder = Callable[[str], list[float]]


def _resolve(v: str) -> str:
    if v and v.startswith("env:"):
        val = os.environ.get(v[4:])
        if not val:
            raise ValueError(f"environment variable {v[4:]} is not set")
        return val
    return v


def _openai(model: str, api_key: str) -> Embedder:
    import requests

    def embed(text: str) -> list[float]:
        r = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {_resolve(api_key)}"},
            json={"model": model, "input": text}, timeout=30)
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    return embed


def _sentence_transformers(model: str) -> Embedder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise ImportError("pip install sentence-transformers") from e
    m = SentenceTransformer(model)

    def embed(text: str) -> list[float]:
        return [float(x) for x in m.encode(text)]
    return embed


def _command(cmd: str) -> Embedder:
    def embed(text: str) -> list[float]:
        out = subprocess.run(cmd, shell=True, input=text,
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"embedder command failed: {out.stderr[:500]}")
        vec = json.loads(out.stdout)
        if not isinstance(vec, list) or not vec:
            raise ValueError("embedder command must print a JSON array of floats")
        return [float(x) for x in vec]
    return embed


def build_embedder(cfg: dict) -> Embedder:
    kind = cfg.get("type")
    if kind == "openai":
        return _openai(cfg.get("model", "text-embedding-3-small"),
                       cfg.get("api_key", "env:OPENAI_API_KEY"))
    if kind == "sentence_transformers":
        return _sentence_transformers(cfg.get("model", "all-MiniLM-L6-v2"))
    if kind == "command":
        if not cfg.get("cmd"):
            raise ValueError("command embedder needs 'cmd'")
        return _command(cfg["cmd"])
    raise ValueError(f"unknown embedder type: {kind!r} "
                     "(supported: openai, sentence_transformers, command)")
