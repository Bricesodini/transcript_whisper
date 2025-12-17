from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Dict, Optional, Sequence

REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "httpx")
DEFAULT_PATHS: Sequence[str] = ("/api/v1/health", "/api/v1/profiles", "/api/v1/docs")


def _check_modules() -> None:
    missing: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise RuntimeError(f"Missing dependencies: {', '.join(missing)}")


async def _run_server(
    *,
    port: int,
    host: str,
    probe_host: str,
    api_key: Optional[str],
    request_timeout: float,
    ready_timeout: float,
) -> None:
    import uvicorn

    from .app import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    base_url = f"http://{probe_host}:{port}"
    headers = {"X-API-KEY": api_key} if api_key else None
    try:
        if not await _wait_for_ready(
            base_url, headers=headers, timeout=ready_timeout, request_timeout=request_timeout
        ):
            raise RuntimeError("Server did not start within the allotted timeout.")
        await _exercise_endpoints(base_url, headers=headers, request_timeout=request_timeout)
    finally:
        server.should_exit = True
        await server_task


async def _wait_for_ready(
    base_url: str,
    *,
    headers: Optional[Dict[str, str]],
    timeout: float,
    request_timeout: float,
) -> bool:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=request_timeout) as client:
        end_time = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end_time:
            try:
                response = await client.get("/api/v1/health")
                if response.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


async def _exercise_endpoints(
    base_url: str,
    *,
    headers: Optional[Dict[str, str]],
    request_timeout: float,
) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=request_timeout) as session:
        for path in DEFAULT_PATHS:
            response = await session.get(path)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(f"{path} returned error: {payload['error']}")


async def _main(args) -> None:
    _check_modules()
    await _run_server(
        port=args.port,
        host=args.host,
        probe_host=args.probe_host,
        api_key=args.api_key,
        request_timeout=args.request_timeout,
        ready_timeout=args.ready_timeout,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Control Room smoke test helper.")
    parser.add_argument("--port", type=int, default=8787, help="Port used for the temporary uvicorn server.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface for uvicorn binding.")
    parser.add_argument("--probe-host", default="127.0.0.1", help="Host used for HTTP calls (loopback by défaut).")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Convenience flag: bind 0.0.0.0 (LAN) while conservatively probing via 127.0.0.1.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Durée totale max (s) pour le smoke test (serveur + requêtes).",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=10.0,
        help="Timeout pour attendre /health lors du démarrage.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=5.0,
        help="Timeout HTTP individuel (s) pour chaque requête.",
    )
    parser.add_argument(
        "--api-key",
        help="API key explicite (sinon utilise CONTROL_ROOM_API_KEY de l'environnement).",
    )
    args = parser.parse_args()
    if args.lan:
        args.host = "0.0.0.0"
    args.api_key = args.api_key or os.getenv("CONTROL_ROOM_API_KEY")
    try:
        asyncio.run(asyncio.wait_for(_main(args), timeout=args.timeout))
        print("[SMOKE] All checks passed.")
    except asyncio.TimeoutError:
        print(f"[SMOKE] ERROR: smoke test timed out after {args.timeout:.1f}s")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[SMOKE] ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
