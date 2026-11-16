"""Reproduce one historical import using the real frozen input and exact original reply."""
import argparse
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[3] / 'tools'
sys.path.insert(0, str(TOOLS))
import astra_player_dossier_v1 as dossier

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--official', action='store_true', help='use the public Deribit delivery endpoint')
args = parser.parse_args()
inputs = HERE.parent / 'player_input_liability/source'
expiry = 1794729600000
cases = [dossier.player.read_json(next((inputs / 'cases/2026-11-14').glob(f'14_{side}_*.gz'))) for side in ('put', 'call')]
cards = dossier.rows(inputs / 'levels/2026-11-15.jsonl')
cards = [next(r for r in cards if r['round'] == 'r2' and r['side'] == side) for side in ('put', 'call')]
path = dossier.player.read_json(inputs / 'paths/2026-11-15.json.gz')
source = dossier.seal_source(cases, cards, path, dossier.rows(HERE.parent / 'player_ard/generated/pin_history.jsonl'),
                             source='historical ARD closeout replay; availability at original model request unverified')
inbox = args.out / 'replay-import'
annotations = dict(put='入区前零站，24 小时采样未进入', call='读取时仍在外侧',
                   put_counter='近 4 小时 Binance 现货价跌 1.04%、净主动卖 390.7 BTC',
                   call_counter='已越过保护腿约 469.7 美元', comparison='仍择 Call，因为它已有实测回收',
                   depends_on_ard=False)
dossier.write(inbox / 'source.json', dossier.encode(source))
dossier.write(inbox / 'annotation.json', dossier.encode(annotations))
cli = [sys.executable, str(TOOLS / 'astra_player_dossier_v1.py'), '--root', str(args.out)]
scope = ['--expiry-ms', str(expiry)]
subprocess.run([*cli, 'historical-replay', *scope, '--reference', 'ard-closeout-real-frozen-card',
                '--source', str(inbox / 'source.json')], check=True)
folder = dossier.Dossiers(args.out).path(expiry)
hand = dossier.identity('hand', 'Deribit', 'BTC', expiry, 'HISTORICAL_REPLAY', 'ard-closeout-real-frozen-card')
reproduced = folder / 'hands' / hand / 'evidence/input.md'
assert reproduced.read_text(encoding='utf-8') == (HERE.parent / 'player_ard_closeout/generated/ai_input.md').read_text(encoding='utf-8')
subprocess.run([*cli, 'response', *scope, '--hand', hand, '--attempt', 'historical-original',
                '--raw', str(HERE.parent / 'player_ard_closeout/player_reply.md'),
                '--annotation', str(inbox / 'annotation.json'), '--channel', 'HISTORICAL_MANUAL_IMPORT'], check=True)
subprocess.run([*cli, 'freeze', *scope, '--hand', hand, '--condition',
                str(HERE.parent / 'player_ard_responsibility/shadow_condition.json')], check=True)
if args.official:
    subprocess.run([*cli, 'settle', *scope], check=True)
