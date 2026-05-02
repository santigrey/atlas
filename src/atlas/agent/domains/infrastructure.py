"""Atlas Operations Agent -- Domain 1: Infrastructure monitoring.

Responsibilities (per build spec lines 272-313 + Sloan directive Day 78 morning):
- system_vitals_check(): every 5min; per-node CPU/RAM/disk via Prometheus first, SSH fallback.
- service_uptime_check(): every 5min; per-node service health (containers + systemd).
- substrate_anchor_check(): hourly; verify control-postgres-beast + control-garage-beast StartedAt unchanged.

Findings persist to atlas.tasks via canonical atlas.mcp_server.tasks.create_task pattern.
P6 #32 mitigation: pattern copied from Cycle 1I commit d383fe0; not authored fresh from memory.
Sloan directive override (Day 78): table=atlas.tasks not atlas.events; payload.kind in
{monitoring_cpu, monitoring_ram, monitoring_disk, service_uptime, substrate_check}.

Mac mini DEFERRED per v0.2 P5 #35 (DNS intermittency).

All probes are READ-ONLY: Prometheus GET requests + SSH commands that observe-only
(systemctl is-active, docker inspect, /proc reads, df/free/top output). Never modify.

Parallelization: per-node + per-service probes run via asyncio.gather to fit 90s
acceptance window. Each probe has own timeout; gather collects all results.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from atlas.db import Database

log = logging.getLogger(__name__)

# Nodes monitored (Mac mini DEFERRED per v0.2 P5 #35)
NODES: list[dict[str, str]] = [
    {"name": "ck",       "ip": "192.168.1.10",  "user": "jes"},
    {"name": "beast",    "ip": "192.168.1.152", "user": "jes"},
    {"name": "goliath",  "ip": "192.168.1.20",  "user": "jes"},
    {"name": "slimjim",  "ip": "192.168.1.40",  "user": "jes"},
    {"name": "kalipi",   "ip": "192.168.1.254", "user": "sloan"},
]

PROMETHEUS_URL = "http://192.168.1.40:9090"

# Substrate anchors (canonical values per Phase 0 verified-live)
ANCHOR_POSTGRES = "2026-04-27T00:13:57.800746541Z"
ANCHOR_GARAGE = "2026-04-27T05:39:58.168067641Z"

# Services per Sloan directive Day 78 morning + spec service registry
SERVICES: list[dict[str, str]] = [
    {"node": "beast",  "target": "control-postgres-beast",  "kind": "container"},
    {"node": "beast",  "target": "control-garage-beast",    "kind": "container"},
    {"node": "beast",  "target": "atlas-mcp.service",       "kind": "systemd"},
    {"node": "ck",     "target": "orchestrator.service",    "kind": "systemd"},
    {"node": "ck",     "target": "mercury-scanner.service", "kind": "systemd"},
    {"node": "ck",     "target": "nginx.service",           "kind": "systemd"},
]


def _node_by_name(name: str) -> Optional[dict[str, str]]:
    for n in NODES:
        if n["name"] == name:
            return n
    return None


async def _ssh_run(host_ip: str, user: str, cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Async SSH via subprocess. Beast->fleet auth via deployed id_ed25519 (Phase 0).

    Returns (returncode, stdout, stderr). On timeout: returncode=-1, stderr='ssh timeout'.
    No new dependencies; uses openssh-client which is system-level on Beast.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{user}@{host_ip}",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return -1, "", f"ssh spawn error: {e}"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout_b.decode(errors="replace").strip(), stderr_b.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -1, "", "ssh timeout"


async def _local_run(cmd: str, timeout: float = 10.0) -> tuple[int, str, str]:
    """Async local shell command. Used for Beast-local docker inspect calls."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return -1, "", f"local spawn error: {e}"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout_b.decode(errors="replace").strip(), stderr_b.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -1, "", "local timeout"


async def _prometheus_query(client: httpx.AsyncClient, query: str) -> Optional[dict]:
    """GET /api/v1/query?query=... -- returns parsed JSON dict on 200, None on error/timeout."""
    try:
        resp = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning(f"prometheus_query_failed query={query[:80]!r} error={str(e)[:200]}")
        return None


def _prom_first_value(data: Optional[dict]) -> Optional[float]:
    """Extract first numeric value from Prometheus query response."""
    if data is None:
        return None
    try:
        result = data.get("data", {}).get("result", [])
        if not result:
            return None
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


async def _create_monitoring_task(db: Database, kind: str, payload: dict[str, Any]) -> Optional[str]:
    """Insert atlas.tasks row with status='pending' + payload.kind + observation data.

    P6 #32 mitigation: pattern copied from atlas.mcp_server.tasks.create_task
    (Cycle 1I commit d383fe0). Adapted: payload.kind explicit, status='pending'.
    Returns task UUID as str on success, None on failure.
    """
    # Defensive: explicit kind argument ALWAYS wins (prevents payload-supplied 'kind' from shadowing).
    payload_with_kind = dict(payload)
    payload_with_kind["kind"] = kind
    payload_json = json.dumps(payload_with_kind, default=str)
    try:
        async with db.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO atlas.tasks (status, payload) "
                    "VALUES ('pending', %s::jsonb) RETURNING id",
                    (payload_json,),
                )
                row = await cur.fetchone()
                await conn.commit()
        return str(row[0]) if row else None
    except Exception as e:
        log.exception(f"_create_monitoring_task failed kind={kind}: {e}")
        return None


# ---------------- system_vitals_check (every 5min) ----------------

async def system_vitals_check(db: Database) -> None:
    """Per-node CPU/RAM/disk: 5 nodes * 3 metrics = 15 atlas.tasks. Parallel via asyncio.gather."""
    log.info(f"system_vitals_check_start nodes={len(NODES)}")
    async with httpx.AsyncClient() as client:
        tasks = []
        for node in NODES:
            instance = f"{node['ip']}:9100"
            tasks.append(_check_cpu(db, client, node, instance))
            tasks.append(_check_ram(db, client, node, instance))
            tasks.append(_check_disk(db, client, node, instance))
        # Run all 15 probes concurrently with return_exceptions so one failure doesn't kill others
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            log.warning(f"system_vitals_check sub-probe errors: {len(errors)} of {len(tasks)}")
    log.info("system_vitals_check_done")


async def _check_cpu(db: Database, client: httpx.AsyncClient, node: dict, instance: str) -> None:
    q = f'100 - (avg by (instance) (rate(node_cpu_seconds_total{{mode="idle",instance="{instance}"}}[5m])) * 100)'
    cpu_pct = _prom_first_value(await _prometheus_query(client, q))
    source = "prometheus"
    if cpu_pct is None:
        rc, stdout, _ = await _ssh_run(node["ip"], node["user"], "top -bn1 | head -3 | tail -1")
        if rc == 0 and stdout:
            try:
                for part in stdout.split(","):
                    part = part.strip()
                    if part.endswith("id"):
                        cpu_pct = round(100.0 - float(part.split()[0]), 2)
                        source = "ssh"
                        break
            except Exception:
                pass
    await _create_monitoring_task(db, "monitoring_cpu", {
        "node": node["name"],
        "cpu_pct": cpu_pct,
        "source": source if cpu_pct is not None else "unavailable",
        "threshold_breach": (cpu_pct is not None and cpu_pct > 85.0),
    })


async def _check_ram(db: Database, client: httpx.AsyncClient, node: dict, instance: str) -> None:
    q = f'(1 - (node_memory_MemAvailable_bytes{{instance="{instance}"}} / node_memory_MemTotal_bytes{{instance="{instance}"}})) * 100'
    ram_pct = _prom_first_value(await _prometheus_query(client, q))
    source = "prometheus"
    if ram_pct is None:
        rc, stdout, _ = await _ssh_run(
            node["ip"], node["user"],
            "awk '/MemTotal:/ {tot=$2} /MemAvailable:/ {avail=$2} END {printf \"%.2f\", (tot-avail)*100/tot}' /proc/meminfo",
        )
        if rc == 0 and stdout:
            try:
                ram_pct = round(float(stdout), 2)
                source = "ssh"
            except ValueError:
                pass
    await _create_monitoring_task(db, "monitoring_ram", {
        "node": node["name"],
        "ram_pct": ram_pct,
        "source": source if ram_pct is not None else "unavailable",
        "threshold_breach": (ram_pct is not None and ram_pct > 90.0),
    })


async def _check_disk(db: Database, client: httpx.AsyncClient, node: dict, instance: str) -> None:
    q = f'(1 - (node_filesystem_avail_bytes{{instance="{instance}",mountpoint="/"}} / node_filesystem_size_bytes{{instance="{instance}",mountpoint="/"}})) * 100'
    disk_pct = _prom_first_value(await _prometheus_query(client, q))
    source = "prometheus"
    if disk_pct is None:
        rc, stdout, _ = await _ssh_run(
            node["ip"], node["user"],
            "df / --output=pcent | tail -1 | tr -d ' %'",
        )
        if rc == 0 and stdout:
            try:
                disk_pct = round(float(stdout), 2)
                source = "ssh"
            except ValueError:
                pass
    await _create_monitoring_task(db, "monitoring_disk", {
        "node": node["name"],
        "disk_pct": disk_pct,
        "source": source if disk_pct is not None else "unavailable",
        "threshold_breach": (disk_pct is not None and disk_pct > 85.0),
    })


# ---------------- service_uptime_check (every 5min) ----------------

async def service_uptime_check(db: Database) -> None:
    """6 services parallel via asyncio.gather. Writes one atlas.tasks per service."""
    log.info(f"service_uptime_check_start services={len(SERVICES)}")
    tasks = [_probe_service(db, svc) for svc in SERVICES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        log.warning(f"service_uptime_check sub-probe errors: {len(errors)} of {len(tasks)}")
    log.info("service_uptime_check_done")


async def _probe_service(db: Database, svc: dict) -> None:
    node = _node_by_name(svc["node"])
    if node is None:
        log.warning(f"service_uptime_check unknown_node={svc['node']}")
        return
    target = svc["target"]
    kind = svc["kind"]
    if kind == "container":
        cmd = f"docker inspect {target} --format '{{{{.State.Status}}}}' 2>&1"
    else:  # systemd
        cmd = f"systemctl is-active {target} 2>&1"
    rc, stdout, stderr = await _ssh_run(node["ip"], node["user"], cmd, timeout=8.0)
    status = stdout.strip() if rc == 0 else f"probe_error rc={rc} stderr={stderr[:120]}"
    is_healthy = (kind == "systemd" and status == "active") or (kind == "container" and status == "running")
    await _create_monitoring_task(db, "service_uptime", {
        "node": svc["node"],
        "target": target,
        "target_kind": kind,
        "status": status,
        "is_healthy": is_healthy,
    })


# ---------------- substrate_anchor_check (hourly) ----------------

async def substrate_anchor_check(db: Database) -> None:
    """Verify B2b + Garage anchors bit-identical to canonical. Writes one atlas.tasks row.

    READ-ONLY: docker inspect retrieval; never modifies container state.
    Drift = Tier 3 alert (substrate disturbance).
    """
    log.info("substrate_anchor_check_start")
    rc_pg, pg_anchor, _ = await _local_run(
        "docker inspect control-postgres-beast --format '{{.State.StartedAt}}'"
    )
    rc_gg, gg_anchor, _ = await _local_run(
        "docker inspect control-garage-beast --format '{{.State.StartedAt}}'"
    )
    pg_match = (rc_pg == 0 and pg_anchor == ANCHOR_POSTGRES)
    gg_match = (rc_gg == 0 and gg_anchor == ANCHOR_GARAGE)
    drift = (not pg_match) or (not gg_match)
    await _create_monitoring_task(db, "substrate_check", {
        "postgres_anchor_observed": pg_anchor if rc_pg == 0 else f"probe_error rc={rc_pg}",
        "postgres_anchor_canonical": ANCHOR_POSTGRES,
        "postgres_match": pg_match,
        "garage_anchor_observed": gg_anchor if rc_gg == 0 else f"probe_error rc={rc_gg}",
        "garage_anchor_canonical": ANCHOR_GARAGE,
        "garage_match": gg_match,
        "drift_detected": drift,
    })
    log.info(f"substrate_anchor_check_done drift={drift}")
