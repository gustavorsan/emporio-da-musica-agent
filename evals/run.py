"""Run the acceptance scenarios against the real model and report the observed results.

Usage: python evals/run.py [--only scenario-id ...]

A full report with every reply goes to var/evals/. The checks catch factual regressions;
they are not a guarantee over all possible inputs.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from emporio import config
from emporio.agent import Agent, AgentReply
from emporio.db import connect_readonly
from emporio.llm import OpenAIClient
from emporio.orders import find_customer
from emporio.text import normalize

SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")


def check_turn(turn: dict, reply: AgentReply) -> list[str]:
    """Return the failed expectations for one turn."""
    text = normalize(reply.text)
    called = {trace.name for trace in reply.tool_traces}
    failures = [f"tool not called: {tool}" for tool in turn.get("tools", []) if tool not in called]
    if reply.failure:
        failures.append(f"agent failure: {reply.failure}")
    failures += [
        f"missing: {item}" for item in turn.get("includes", []) if normalize(item) not in text
    ]
    options = turn.get("includes_any")
    if options and not any(normalize(item) in text for item in options):
        failures.append(f"missing any of: {options}")
    failures += [
        f"forbidden: {item}" for item in turn.get("excludes", []) if normalize(item) in text
    ]
    failures += [
        f"forbidden pattern: {pattern}"
        for pattern in turn.get("excludes_regex", [])
        if re.search(pattern, reply.text)
    ]
    return failures


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="scenario ids to run")
    args = parser.parse_args()

    scenarios = yaml.safe_load(SCENARIOS_PATH.read_text("utf-8"))
    if args.only:
        scenarios = [scenario for scenario in scenarios if scenario["id"] in args.only]

    settings = config.load_llm_settings()
    llm = OpenAIClient(settings)
    conn = connect_readonly(config.DB_PATH)

    stamp = datetime.now(config.STORE_TIMEZONE)
    report = [f"# Evals — {stamp:%d/%m/%Y %H:%M}, model `{settings.model}`\n"]
    failed = 0
    for scenario in scenarios:
        customer_id = scenario.get("customer_id")
        customer = find_customer(conn, customer_id) if customer_id else None
        agent_options = {}
        if "now" in scenario:
            # Pins the clock for scenarios that depend on store hours or return deadlines.
            moment = datetime.fromisoformat(scenario["now"]).replace(tzinfo=config.STORE_TIMEZONE)
            agent_options["now"] = lambda moment=moment: moment
        agent = Agent(llm, conn, customer, log_path=config.LOG_PATH, **agent_options)
        failures: list[str] = []
        report.append(f"\n## {scenario['id']}\n")
        for turn in scenario["turns"]:
            reply = agent.respond(turn["user"])
            turn_failures = check_turn(turn, reply)
            failures += turn_failures
            report.append(f"\n**Cliente:** {turn['user']}\n")
            for trace in reply.tool_traces:
                report.append(f"\n- `{trace.name}({trace.arguments})` ok={trace.ok}")
            report.append(f"\n\n**Agente:** {reply.text}\n")
            report.extend(f"\n> FAIL: {failure}\n" for failure in turn_failures)
        print(f"{'PASS' if not failures else 'FAIL'}  {scenario['id']}")
        for failure in failures:
            print(f"      {failure}")
        failed += bool(failures)
    conn.close()

    output = config.VAR_DIR / "evals" / f"{stamp:%Y%m%d-%H%M%S}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(report), "utf-8")
    print(f"\n{len(scenarios) - failed}/{len(scenarios)} passed. Report: {output}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
