"""Replay archived research without network or production changes; writes only .replay-edge."""
from pathlib import Path
import contextlib,io,json,shutil,sys
import astra_control_study as c
import astra_edge_features as f
import astra_edge_analysis as a
import astra_edge_prior as p
import astra_edge_finalize as z
import astra_edge_math as m

def main(root):
    root=root.resolve();source=root/'.artifacts/astra-edge-research-20260914';target=root/'.replay-edge'
    target.mkdir(exist_ok=True)
    for name in ('protocol.json','features_seal.json'):shutil.copy2(source/name,target/name)
    with contextlib.redirect_stdout(io.StringIO()):
        f.run(root,target)
        a.run(root,target)
        p.run(root,target/'prior')
        z.run(root,target)
        m.scenarios(target/'math')
    names=['features/signal_facts.csv','features/clock_facts.csv','analysis/main_ledger.csv',
      'analysis/factor_summary.csv','analysis/prior_comparisons.csv','prior/prior_predictions.csv',
      'prior/prior_projection_paths.csv','prior/prior_side_choices.csv','prior/prior_choice_evaluation.csv',
      'math/illustrative_distribution_scenarios.csv']
    checks={name:c.sha(source/name)==c.sha(target/name) for name in names}
    result={'all_equal':all(checks.values()),'csv_hash_comparison':checks,'no_network':True}
    c.save(target/'replay_acceptance.json',result);print(json.dumps(result,indent=2))
    if not result['all_equal']:raise RuntimeError('replay differs from sealed outputs')

if __name__=='__main__':main(Path(sys.argv[1]) if len(sys.argv)>1 else Path.cwd())
