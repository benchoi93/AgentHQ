#!/usr/bin/env python3
"""AgentHQ Agent — backwards-compatible entrypoint.

Usage:
    python agenthq_agent.py --config config.yaml
    pip install -e .  &&  agenthq-agent --config config.yaml
"""
from agenthq_agent.cli import main

if __name__ == "__main__":
    main()
