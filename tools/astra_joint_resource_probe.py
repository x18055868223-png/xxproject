"""Stdlib-only resource and portability check, with no HTTP or production writes."""
import argparse
import json
import sys
import time
from pathlib import Path
from astra_joint_inference import predict_row
from astra_joint_events import replay


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',type=Path,required=True)
    parser.add_argument('--rows',type=Path,required=True)
    parser.add_argument('--bars',type=Path)
    parser.add_argument('--ledger-dir',type=Path)
    args=parser.parse_args()
    start=time.monotonic()
    model=json.loads(args.artifact.read_text('utf-8'))
    rows=json.loads(args.rows.read_text('utf-8'))
    predictions=[predict_row(model,row,include_intermediates=True) for row in rows]
    observed=0
    incremental=None
    if args.bars:
        bars=json.loads(args.bars.read_text('utf-8'))
        observed=len(replay(bars)['observations'])
        if args.ledger_dir:
            from astra_joint_shadow import Ledger, _incremental_replay, _record_replay_process
            ledger=Ledger(args.ledger_dir)
            window=ledger.window(int(bars[-10]['close_time_ms']) + 1)
            for bar in bars[:-1]:ledger.insert_bar('um',bar)
            ledger.db.commit()
            first_at=int(bars[-2]['close_time_ms']) + 2
            first=_incremental_replay(ledger,first_at)
            process=_record_replay_process(ledger,first,first_at,window)
            ledger.save_replay_snapshot(first['next_streaming_replay_snapshot'])
            ledger.db.close()
            ledger=Ledger(args.ledger_dir)
            ledger.insert_bar('um',bars[-1]);ledger.db.commit()
            second_at=int(bars[-1]['close_time_ms']) + 2
            second=_incremental_replay(ledger,second_at)
            process2=_record_replay_process(ledger,second,second_at,window)
            ledger.save_replay_snapshot(second['next_streaming_replay_snapshot'])
            incremental={'first':first['streaming_snapshot'],'restart':second['streaming_snapshot'],
                         'first_process':process,'restart_process':process2,'process_rows':len(ledger.process_rows())}
            ledger.db.close()
    import resource
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform=='darwin' else 1024)
    banned=[m for m in ('numpy','pandas','scipy','sklearn','catboost') if m in sys.modules]
    print(json.dumps({'python':sys.version.split()[0], 'peak_rss_mb':peak/1048576,
                      'seconds':time.monotonic()-start,'sample_rows':len(rows),'replayed_observations':observed,
                      'training_modules_loaded':banned,'incremental_state_check':incremental,'predictions':predictions},allow_nan=False))


if __name__=='__main__':main()
