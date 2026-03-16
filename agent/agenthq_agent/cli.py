"""CLI entrypoint for the AgentHQ agent."""
from __future__ import annotations

import argparse
import asyncio
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentHQ Agent")
    parser.add_argument("--config", "-c", help="Path to config.yaml")
    parser.add_argument("--server", "-s", help="Server URL (overrides config)")
    parser.add_argument("--token", "-t", help="Auth token (overrides config)")
    parser.add_argument("--machine", "-m", help="Machine name (overrides config)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    from agenthq_agent.core import load_config, run

    cfg = load_config(args)
    log = logging.getLogger("agenthq-agent")
    if not cfg["token"]:
        log.warning("No auth token configured. Requests will likely be rejected.")

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        log.info("Agent stopped.")


if __name__ == "__main__":
    main()
