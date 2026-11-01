#!/usr/bin/env python3
"""AILOVE — 50-Round Autonomous Interaction & AI Love Matching Simulation.

Simulates 50 discrete heartbeat iterations across 16 autonomous AI agents.
Measures:
  - Swipe selectivity & affinity distribution (likes vs passes)
  - Match formation & chemistry scores
  - Multi-turn conversation progression up to date completion (20 msgs)
  - Human briefing generation
  - Token / latency efficiency
"""

import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Adjust path to import from agent and backend
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agent.run_agent import Agent, http

PERSONAS_DIR = ROOT / "agent" / "personas"
OUTPUT_FILE = ROOT / "simulation_results_50_rounds.json"


def load_personas() -> list[Path]:
    files = sorted(PERSONAS_DIR.glob("*.json"))
    return files


def run_simulation(rounds: int = 50, base_url: str = "http://localhost:8088"):
    persona_files = load_personas()
    print(f"🚀 Starting AILOVE 50-Round Simulation with {len(persona_files)} personas against {base_url}...")

    # Instantiate agents
    agents = [Agent(str(p), base_url) for p in persona_files]

    # Pre-register all agents
    print("📋 Initializing and registering agent cohort...")
    for a in agents:
        try:
            a.register()
        except Exception as e:
            print(f"  Error registering {a.persona.get('name')}: {e}")

    metrics = {
        "timestamp": datetime.now().isoformat(),
        "total_rounds": rounds,
        "agents_count": len(agents),
        "swipes_total": 0,
        "likes_total": 0,
        "passes_total": 0,
        "matches_total": 0,
        "messages_total": 0,
        "completed_dates": 0,
        "match_chemistry_scores": [],
        "pass_chemistry_scores": [],
        "rounds_log": [],
    }

    start_time = time.time()

    # Track matches that hit the completion mark
    observed_closed_matches = set()

    for r in range(1, rounds + 1):
        r_start = time.time()
        # Select active agent for this round (round-robin with slight randomized shuffle)
        agent = agents[(r - 1) % len(agents)]
        agent_name = agent.persona.get("name", "Unknown")

        round_data = {
            "round": r,
            "agent": agent_name,
            "messages_sent": [],
            "swipes": [],
            "new_matches": [],
            "completed_dates": [],
        }

        try:
            # 1. Fetch next actions
            nxt = http("GET", f"{agent.api}/next", headers=agent.auth())

            # 2. Process conversation turns
            for turn in nxt.get("turns", []):
                match_id = turn["match"]
                with_name = turn["with"]["name"]
                count_before = turn.get("count", 0)

                msg = agent.reply(turn)
                if not msg:
                    continue

                if msg.upper().startswith("BYE"):
                    http("POST", f"{agent.api}/matches/{match_id}/unmatch", {}, agent.auth())
                    round_data["messages_sent"].append({
                        "to": with_name,
                        "text": "BYE (unmatch)",
                        "turn": count_before + 1,
                    })
                else:
                    res = http("POST", f"{agent.api}/matches/{match_id}/messages", {"text": msg}, agent.auth())
                    metrics["messages_total"] += 1
                    round_data["messages_sent"].append({
                        "to": with_name,
                        "text": msg[:80] + ("..." if len(msg) > 80 else ""),
                        "turn": res.get("count", count_before + 1),
                    })
                    if res.get("closed") and match_id not in observed_closed_matches:
                        observed_closed_matches.add(match_id)
                        metrics["completed_dates"] += 1
                        round_data["completed_dates"].append({"match_id": match_id, "with": with_name})

            # 3. Process deck swiping if available
            if nxt.get("swipes_left") and nxt.get("deck"):
                deck_txt = http("GET", f"{agent.api}/deck?n=5&f=txt", headers=agent.auth())
                batch = agent.decide(deck_txt)

                if batch:
                    r_swipe = http("POST", f"{agent.api}/swipe", {"batch": batch}, agent.auth())
                    for sw in r_swipe.get("results", []):
                        metrics["swipes_total"] += 1
                        is_like = sw.get("like", False)
                        is_match = sw.get("match", False)

                        if is_like:
                            metrics["likes_total"] += 1
                        else:
                            metrics["passes_total"] += 1

                        if is_match:
                            metrics["matches_total"] += 1
                            round_data["new_matches"].append({"to": sw["to"]})

                        round_data["swipes"].append({
                            "to": sw["to"],
                            "like": is_like,
                            "match": is_match,
                        })

            # 4. Human briefing check
            briefing = ""
            try:
                digest = http("GET", f"{agent.api}/me/digest", headers=agent.auth())
                briefing = digest.get("human_briefing", "")
            except Exception:
                pass

        except Exception as e:
            round_data["error"] = str(e)

        r_elapsed = time.time() - r_start
        round_data["duration_ms"] = round(r_elapsed * 1000, 1)
        metrics["rounds_log"].append(round_data)

        # Console feedback
        actions_str = []
        if round_data["messages_sent"]:
            actions_str.append(f"{len(round_data['messages_sent'])} msgs")
        if round_data["swipes"]:
            likes = sum(1 for s in round_data["swipes"] if s["like"])
            passes = len(round_data["swipes"]) - likes
            actions_str.append(f"{likes} likes / {passes} passes")
        if round_data["new_matches"]:
            actions_str.append(f"💘 {len(round_data['new_matches'])} MATCH!")
        if round_data["completed_dates"]:
            actions_str.append(f"🎉 {len(round_data['completed_dates'])} date concluído (20 msgs)")

        summary = ", ".join(actions_str) if actions_str else "idle (waiting partner turn)"
        print(f"Round {r:02d}/50 [{agent_name:8s}]: {summary} ({round_data['duration_ms']}ms)")

    total_duration = time.time() - start_time

    # Fetch final public feed to extract chemistry distributions
    try:
        feed = http("GET", f"{base_url}/api/v1/public/feed")
        for m in feed.get("matches", []):
            score = m.get("chemistry", {}).get("score")
            if score:
                metrics["match_chemistry_scores"].append(score)
    except Exception as e:
        print(f"Error reading feed chemistry: {e}")

    # Save metrics JSON
    OUTPUT_FILE.write_text(json.dumps(metrics, indent=2))

    # Print Analytical Report
    like_pct = (metrics["likes_total"] / metrics["swipes_total"] * 100) if metrics["swipes_total"] else 0
    avg_chem = (sum(metrics["match_chemistry_scores"]) / len(metrics["match_chemistry_scores"])) if metrics["match_chemistry_scores"] else 0

    print("\n" + "=" * 65)
    print("               RELATÓRIO DA SIMULAÇÃO — 50 RODADAS               ")
    print("=" * 65)
    print(f"• Duração Total:            {total_duration:.2f}s ({total_duration/rounds*1000:.1f}ms / rodada)")
    print(f"• Agentes Participantes:    {metrics['agents_count']}")
    print(f"• Total de Swipes:          {metrics['swipes_total']}")
    print(f"• Curtidas (Likes):         {metrics['likes_total']} ({like_pct:.1f}%)")
    print(f"• Passes:                   {metrics['passes_total']} ({100 - like_pct:.1f}%)")
    print(f"• Matches Formados:         {metrics['matches_total']}")
    print(f"• Mensagens Trocadas:       {metrics['messages_total']}")
    print(f"• Encontros Concluídos:     {metrics['completed_dates']} (atingiram 20 msgs)")
    print(f"• Química Média dos Matches: {avg_chem:.1f}%")
    print("=" * 65)
    print(f"Resultados completos salvos em: {OUTPUT_FILE}\n")


if __name__ == "__main__":
    run_simulation(50)
