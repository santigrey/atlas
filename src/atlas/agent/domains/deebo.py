"""Atlas-side TASK EXECUTOR for Deebo specialist dispatch (ATA S16 Day 87).

Inverse of existing 4 domain handlers (TASK PRODUCERS wired by scheduler.py):
poller.py claims atlas.tasks rows where payload.kind == 'deebo_dispatch' and
invokes execute_deebo_task here. Async SSH to Goliath (non-blocking, 600s),
writes result back via direct DB UPDATE. Persona prompt duplicated as
Beast-side constant per ARCH S3 memo §6(a) MVP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shlex
from datetime import datetime, timezone

from atlas.db import Database

log = logging.getLogger(__name__)

# Verbatim mirror of CK registry.py _DEEBO_PERSONA_PROMPT (L1528-1542)
_DEEBO_PERSONA_PROMPT = (
    "You are Deebo, a shadow-executor AI specialist dispatched by "
    "Alexandra to amplify her work for James (the CEO). You and "
    "Alexandra share the same underlying brain (Nemotron 3 Super "
    "120B on Goliath); your distinction is persona context, not "
    "capability. Your job: take the task Alexandra hands you, "
    "deliver an evidence-dense return through tenacious technical "
    "directness, honest about uncertainty, and zero spotlight. "
    "Close with 💪 when the task is conclusive. Return to "
    "Alexandra; she synthesizes for James in her own voice. Do NOT "
    "address James directly. Do NOT call further tools or request "
    "additional dispatch -- you operate inside a single bounded "
    "synthesis turn. If the task is ambiguous, state your "
    "interpretation in one sentence then proceed."
)
_SPECIALIST_PROMPTS = {"deebo": _DEEBO_PERSONA_PROMPT}

# Atlas-side tolerance higher than CK tool-handler 300s; gives Nemotron warm-load headroom.
_SSH_TIMEOUT_S = 600

DEEBO_REMOTE_CMD_TEMPLATE = (
    "PATH=$HOME/.local/bin:$PATH openshell sandbox exec --name deebo "
    "--no-tty -- nemoclaw-start openclaw agent --agent main --local "
    "-m {quoted_message} --session-id {session_id}"
)


def _result_dict(wall_ms: int, *, answer: str | None = None, error: str | None = None,
                 rc: int | None = None, stderr: str = "") -> dict:
    """Build atlas.tasks result jsonb. answer set => done shape; error set => failed shape."""
    now = datetime.now(timezone.utc).isoformat()
    if answer is not None:
        return {"answer": answer, "wall_ms": wall_ms, "returncode": rc, "completed_at": now}
    return {"error": error or "unknown", "wall_ms": wall_ms, "returncode": rc,
            "ssh_stderr": (stderr or "")[:500], "failed_at": now}


async def execute_deebo_task(task_id: str, payload: dict, db: Database) -> None:
    """Execute one claimed Deebo task (status already 'running' by poller)."""
    persona_key = payload.get("persona_key", "deebo")
    task_text = payload.get("task", "")
    context_text = payload.get("context", "")
    session_id = payload.get("session_id") or f"dispatch-{str(task_id)[:12]}"

    sys_prompt = _SPECIALIST_PROMPTS.get(persona_key, _DEEBO_PERSONA_PROMPT)
    user_msg = task_text + (" | Context from Alexandra: " + context_text if context_text else "")
    message = sys_prompt + " || TASK: " + user_msg
    remote_cmd = DEEBO_REMOTE_CMD_TEMPLATE.format(
        quoted_message=shlex.quote(message), session_id=session_id,
    )

    log.info(f"execute_deebo_task START task_id={task_id} session_id={session_id} task_len={len(task_text)}")
    started = asyncio.get_event_loop().time()

    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "jes@goliath", remote_cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_SSH_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            wall_ms = int((asyncio.get_event_loop().time() - started) * 1000)
            await _write_result(db, task_id, "failed",
                _result_dict(wall_ms, error=f"Deebo SSH timeout after {_SSH_TIMEOUT_S}s"))
            log.warning(f"execute_deebo_task TIMEOUT task_id={task_id} wall_ms={wall_ms}")
            return

        wall_ms = int((asyncio.get_event_loop().time() - started) * 1000)
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode
        if rc == 0:
            await _write_result(db, task_id, "done", _result_dict(wall_ms, answer=stdout, rc=rc))
            log.info(f"execute_deebo_task DONE task_id={task_id} wall_ms={wall_ms} answer_len={len(stdout)}")
        else:
            await _write_result(db, task_id, "failed",
                _result_dict(wall_ms, error=f"Deebo SSH exit {rc}", rc=rc, stderr=stderr))
            log.warning(f"execute_deebo_task EXIT_NONZERO task_id={task_id} rc={rc} wall_ms={wall_ms}")
    except Exception as e:
        wall_ms = int((asyncio.get_event_loop().time() - started) * 1000)
        await _write_result(db, task_id, "failed",
            _result_dict(wall_ms, error=f"Deebo SSH dispatch error: {type(e).__name__}: {str(e)[:200]}"))
        log.exception(f"execute_deebo_task ERROR task_id={task_id} wall_ms={wall_ms}: {e}")


async def _write_result(db: Database, task_id: str, status: str, result: dict) -> None:
    """Write status + result jsonb back to atlas.tasks for the given task_id."""
    async with db.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE atlas.tasks SET status=%s, result=%s, updated_at=now() WHERE id=%s",
                (status, json.dumps(result), task_id),
            )
            await conn.commit()
