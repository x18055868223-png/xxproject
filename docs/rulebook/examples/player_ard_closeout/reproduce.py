"""Reproduce the closeout hand from the unchanged frozen sources."""
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'player_input_liability/source'
GENERATOR = HERE.parents[3] / 'tools/astra_player_input_v1.py'
sys.path.insert(0, str(GENERATOR.parent))
import astra_player_input_v1 as player


def generate(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    history = HERE.parent / 'player_ard/generated/pin_history.jsonl'
    command = [sys.executable, str(GENERATOR), '--archive', str(SOURCE), '--expiry-day', '2026-11-15',
               '--round', 'r2', '--ard-history', str(history)]
    first_order = out / 'first_order_replay.md'
    subprocess.run([*command, '--without-ard', '--out', str(first_order)], check=True)
    original = HERE.parent / 'player_first_order_guard/ai_input.md'
    assert first_order.read_bytes() == original.read_bytes()
    subprocess.run([*command, '--out', str(out / 'ai_input.md')], check=True)
    cards = [json.loads(line) for line in (SOURCE / 'levels/2026-11-15.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    cards = [card for card in cards if card['round'] == 'r2']
    facts = player.ard.analyze([json.loads(line) for line in history.read_text(encoding='utf-8').splitlines() if line.strip()],
                               player.read_json(SOURCE / 'paths/2026-11-15.json.gz'), expiry_ms=cards[0]['expiry_ms'],
                               cutoff_ms=min(card['read_at_ms'] for card in cards))
    previous = HERE.parent / 'player_ard_responsibility/generated/ard_facts.json'
    # Numerical computation and all observation identities stay unchanged.
    assert json.loads(previous.read_text(encoding='utf-8')) == json.loads(json.dumps(facts))
    return facts


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    generate(parser.parse_args().out)
