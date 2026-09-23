from astra_joint_benchmarks import compare, summarize


def row(identity, kind, dte, payout, day='2023-01-02', active=False, width=2000):
    return dict(row_id=identity, observation_id=identity, observation_kind=kind, dte_hours=dte,
                payout_btc=payout, loss_normalized=payout / .1, delivery_date=day, in_episode=active,
                side='put', actual_width=width, target_width=2000, entry_price=20000,
                short_strike=19000, as_of_ms=dte)


def test_controls_use_eligibility_and_time_not_result():
    rows = [row('e', 'shock', 12, .004), row('near', 'clock', 11, .007),
            row('far', 'clock', 19, 0), row('active', 'clock', 12, .08, active=True),
            row('wrong_width', 'clock', 12, .08, width=1500)]
    result = compare(rows)
    assert result['matched_rows'][0]['control_row_id'] == 'near'
    rows[1]['payout_btc'] = 0
    assert compare(rows)['matched_rows'][0]['control_row_id'] == 'near'


def test_date_equal_and_break_even_not_win():
    rows = [row('1', 'shock', 12, .01), row('2', 'shock', 12, .01),
            row('3', 'shock', 12, 0, day='2023-01-03')]
    result = summarize(rows)
    assert result['credit_scenarios']['0.05']['net_win_rate'] == .5
    assert result['credit_scenarios']['0.1']['net_win_rate'] == .5
    assert result['mean_payout_btc'] == .005


def test_sensitivity_widths_stay_separate_from_primary_control():
    rows = []
    for width in (1500, 2000, 2500):
        event = row(f'e{width}', 'shock', 12, .004, width=width)
        clock = row(f'c{width}', 'clock', 11, .007, width=width)
        event['target_width'] = clock['target_width'] = width
        rows.extend((event, clock))
    result = compare(rows)
    assert {item['target_width'] for item in result['width_and_phase']} == {1500, 2000, 2500}
    assert len(result['matched_rows']) == 1
    assert result['matched_rows'][0]['event_row_id'] == 'e2000'
