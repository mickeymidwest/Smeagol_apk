"""
Search Hugging Face Hub for GGUF models and download a chosen
quantization straight into this project, no browser needed.

Uses Hugging Face's public, unauthenticated JSON API (confirmed current
as of writing, not assumed from memory):
  - search:      GET https://huggingface.co/api/models?search=...&filter=gguf
  - list files:  GET https://huggingface.co/api/models/{repo_id}/tree/main
  - download:    GET https://huggingface.co/{repo_id}/resolve/main/{filename}
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import requests

SEARCH_URL = "https://huggingface.co/api/models"
TREE_URL_TEMPLATE = "https://huggingface.co/api/models/{repo_id}/tree/main"
RESOLVE_URL_TEMPLATE = "https://huggingface.co/{repo_id}/resolve/main/{filename}"


def search_models(query: str, limit: int = 8, session: Optional[requests.Session] = None) -> list[dict]:
    """Returns a list of {id, downloads, likes} for GGUF repos matching query."""
    s = session or requests
    resp = s.get(SEARCH_URL, params={"search": query, "filter": "gguf", "limit": limit}, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    return [
        {"id": m.get("id", ""), "downloads": m.get("downloads", 0), "likes": m.get("likes", 0)}
        for m in results
    ]


def list_gguf_files(repo_id: str, session: Optional[requests.Session] = None) -> list[dict]:
    """Returns [{filename, size}] for every .gguf file in the repo."""
    s = session or requests
    url = TREE_URL_TEMPLATE.format(repo_id=repo_id)
    resp = s.get(url, timeout=15)
    resp.raise_for_status()
    entries = resp.json()
    return [
        {"filename": e["path"], "size": e.get("size", 0)}
        for e in entries
        if e.get("path", "").endswith(".gguf")
    ]


def download_url(repo_id: str, filename: str) -> str:
    return RESOLVE_URL_TEMPLATE.format(repo_id=repo_id, filename=filename)


def download_file(
    repo_id: str,
    filename: str,
    dest_path: str,
    session: Optional[requests.Session] = None,
    chunk_size: int = 1 << 20,
    progress_callback=None,
) -> None:
    """Streams the file to dest_path. progress_callback(bytes_downloaded, total_bytes)
    is called after each chunk if provided -- optional, purely for CLI progress display."""
    s = session or requests
    url = download_url(repo_id, filename)
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)

    with s.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
