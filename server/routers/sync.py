from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from server.auth import require_token
from server.models import SyncFileEntry, SyncManifest
from server import store

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/upload")
async def upload_sync_files(
    manifest: SyncManifest,
    agent_id: str = Query(...),
    _token: str = Depends(require_token),
):
    updated = []
    for entry in manifest.files:
        changed = await store.upsert_sync_file(
            path=entry.path,
            content=entry.content,
            hash=entry.hash,
            agent_id=agent_id,
        )
        if changed:
            updated.append(entry.path)
    return {"ok": True, "updated": updated}


@router.get("/manifest")
async def get_manifest(_token: str = Depends(require_token)):
    files = await store.get_sync_manifest()
    return {"files": files}


@router.get("/file")
async def get_file(
    path: str = Query(...),
    _token: str = Depends(require_token),
):
    entry = await store.get_sync_file(path)
    if entry is None:
        raise HTTPException(status_code=404, detail="File not found")
    return entry
