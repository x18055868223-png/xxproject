import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from astra_joint_dataset import expiry_ms, select_legs, payout, build_outcomes


def stamp(hour):
    return int(datetime(2022, 5, 3, hour, tzinfo=timezone.utc).timestamp() * 1000)


def test_nearest_expiry_and_exact_eight_hour_exclusion():
    assert (expiry_ms(stamp(0)) - stamp(0)) / 3600000 == 8
    assert not 8 < (expiry_ms(stamp(0)) - stamp(0)) / 3600000 <= 24
    assert (expiry_ms(stamp(8)) - stamp(8)) / 3600000 == 24
    assert (expiry_ms(stamp(21)) - stamp(21)) / 3600000 == 11


def test_actual_creation_otm_and_narrow_tie():
    def contract(strike, created=10):
        return dict(option_type='put', strike=strike, creation_timestamp=created, instrument_name=str(strike))
    pair = select_legs([contract(100), contract(99, 200), contract(98), contract(96.5), contract(95.5)], 'put', 100, 2, 100)
    assert pair[0]['strike'] == 98
    assert pair[1]['strike'] == 96.5


def test_inverse_payout_tail_is_not_capped():
    assert payout('put', 100, 80, 50) == .4
    assert payout('put', 100, 80, 50) / (20 / 105) > 1
    assert payout('call', 100, 120, 150) == pytest.approx(20/150)
    assert payout('put', 100, 80, 100) == 0
    with pytest.raises(ValueError):
        payout('put', 100, 80, 0)


def test_test_outcomes_locked_until_selection(tmp_path):
    (tmp_path / 'decisions').mkdir()
    (tmp_path / 'decisions/candidate_seal.json').write_text('{}')
    with pytest.raises(ValueError, match='sealed'):
        build_outcomes(tmp_path, [2023])
