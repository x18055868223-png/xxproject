"""Read-only product evidence composition over the existing bounded archive."""
from __future__ import annotations
from copy import deepcopy
import gzip
import hashlib
import os
from pathlib import Path
from typing import Mapping

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from . import astra_wpem_native_v1 as native
    from . import astra_wpem_review_evidence_v1 as evidence
    from . import astra_wpem_product_review_v1 as product
else:
    import astra_candidate_asof_v1 as packets
    import astra_wpem_native_v1 as native
    import astra_wpem_review_evidence_v1 as evidence
    import astra_wpem_product_review_v1 as product


def _safe_replay_reason(reason):
    text = str(reason or "")
    if text == "future_quote_clock":
        return {"code": "QUOTE_CLOCK_NOT_REPLAYABLE", "message_cn": "报价时钟晚于冻结估值时点，证据不可回放。"}
    if text.startswith("future_source_clock") or text in {"future_manifest_cutoff", "future_input_clock"}:
        return {"code": "SOURCE_CLOCK_NOT_REPLAYABLE", "message_cn": "来源时钟晚于冻结估值时点，证据不可回放。"}
    if text in {"source_manifest_hash_mismatch", "source_manifest_candidate_mismatch", "candidate_identity_mismatch"}:
        return {"code": "SOURCE_IDENTITY_NOT_REPLAYABLE", "message_cn": "来源或候选身份不能按冻结规则回放。"}
    return {"code": "EVIDENCE_REPLAY_UNAVAILABLE", "message_cn": "证据未通过回放核验，当前适配和补偿不能裁定。"}


def replay_block(reason):
    return _safe_replay_reason(reason)


def non_comparable_peer(snapshot, reason):
    contract = snapshot.get("contract") if isinstance(snapshot, Mapping) else {}
    market = snapshot.get("market") if isinstance(snapshot, Mapping) else {}
    safe = _safe_replay_reason(reason)
    return {
        "error_code": "NON_COMPARABLE",
        "blocked_reason": safe["code"],
        "candidate_id": snapshot.get("candidate_id") if isinstance(snapshot, Mapping) else None,
        "snapshot_id": snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None,
        "side": contract.get("side") if isinstance(contract, Mapping) else None,
        "decision_asof_ms": market.get("valuation_ts_ms") if isinstance(market, Mapping) else None,
    }


def _gap_limitations(snapshot, safe):
    gaps = []
    if isinstance(safe.get("message_cn"), str):
        gaps.append(safe["message_cn"])
    raw_reasons = list(snapshot.get("gap_reasons") or [])
    fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
    raw_reasons.extend(fee.get("gap_reasons") or [])
    for reason in raw_reasons:
        if reason in {"fee_unknown", "fee_missing_or_incomplete"}:
            gaps.append("费用资料缺失，不能形成净补偿判断。")
        elif reason in {"long_book_stale_or_future", "short_book_stale_or_future", "cached_quote_not_current", "quote_invalid_or_stale"}:
            if safe["code"] != "QUOTE_CLOCK_NOT_REPLAYABLE":
                gaps.append("报价时钟过期或晚于冻结时点，不能作为当前证据。")
    result = []
    for item in gaps:
        if item and item not in result:
            result.append(item)
    return result or ["当前证据不可回放。"]


def blocked_summary(snapshot, reason):
    safe = _safe_replay_reason(reason)
    return {
        "path_cn": None,
        "compensation_cn": None,
        "breaks_cn": _gap_limitations(snapshot, safe),
        "path_classification": "blocked",
        "window_status": "BLOCKED",
        "delta_status": "NO_CURRENT_EVIDENCE",
        "source_dependencies": {},
        "win_probability": None,
        "financial_values_changed": False,
    }


def blocked_program_questions(snapshot, reason):
    safe = _safe_replay_reason(reason)
    contract = snapshot["contract"]
    raw_side = str(contract.get("side") or "").lower()
    side = "Put" if raw_side == "put" else "Call" if raw_side == "call" else "候选"
    adverse = "下跌" if raw_side == "put" else "上涨" if raw_side == "call" else "不利变动"
    return {
        "source_kind": "deterministic_program_no_llm",
        "q1": {
            "title_cn": "Q1 · 当前合同承担什么责任",
            "summary_cn": f"这张{side}保单承担价格{adverse}进入卖出腿后的赔付责任；当前证据不可回放，不能给出窗口适配结论。",
            "window_attribution_cn": None,
        },
        "q2": {
            "title_cn": "Q2 · 当前补偿能否参与判断",
            "summary_cn": "报价、费用或来源证据未通过回放校验，不能形成补偿取舍。",
            "window_tradeoff_cn": None,
            "observable_changes_cn": [],
        },
        "audit": {
            "risk": {"status": "NOT_ASSESSED", "mu_M_null_reason": "EVIDENCE_REPLAY_BLOCKED"},
            "source_risk": deepcopy(snapshot.get("risk") or {}),
            "window_status": "BLOCKED",
            "financial_gate_changed": False,
            "llm_calls": 0,
            "blocked_reason": safe["code"],
        },
    }


def _sources(path):
    sources={}
    if not Path(path).is_file(): return sources
    with Path(path).open('rb') as handle:
        handle.seek(max(0,Path(path).stat().st_size-2_000_000))
        if handle.tell(): handle.readline()
        for raw in handle:
            raw=raw.rstrip(b'\r\n')
            if raw:
                digest=hashlib.sha256(raw).hexdigest()
                sources[digest]=raw
    return sources


LEGACY_WINDOW_ROWS=24


def window_row_limit(interval_ms=packets.DEFAULT_INTERVAL_MS):
    """History rows replayed per compose.

    Default stays 24 so composed evidence (and its evidence_hash) is unchanged.
    ASTRA_WPEM_MAX_WINDOW_ROWS lowers it, but never below what the 15/60-minute
    windows need (boundary anchor + rows inside; 13 at the 5-minute interval).
    The row count is a composition input: lowering it changes evidence_hash and
    the dependency diagnostics, not window coverage, delta or path.
    """
    try:
        configured=int(os.environ.get('ASTRA_WPEM_MAX_WINDOW_ROWS') or 0)
    except ValueError:
        configured=0
    if configured<=0:
        return LEGACY_WINDOW_ROWS
    return max(configured,packets.window_rows_needed(interval_ms))


def _same_object_rows(rows, snapshot, cutoff, limit=None):
    """WPEM remembers one risk object: select its own rows, never the side's."""
    limit=window_row_limit() if limit is None else limit
    return [row for row in rows if row['decision_asof_ms']<=cutoff
            and row.get('candidate_id')==snapshot['candidate_id']][-limit:]


def _reusable_current_packet(packet, snapshot, raw_payload, receipts):
    """True only for exactly build_packet(snapshot, raw_payload=..., source_receipts=receipts)."""
    try:
        return (isinstance(packet,Mapping) and packet.get('schema')==packets.SCHEMA
                and packet['snapshot']==snapshot and packet.get('raw_payload')==raw_payload
                and packet['sources']['acquisition_receipts']==dict(receipts or {})
                and packet['versions']['sampling_interval_ms']==packets.DEFAULT_INTERVAL_MS
                and packet['versions']['feature_catalog_hash'] is None
                and packet['model_input'].get('status')=='risk_asset_not_configured'
                and 'candidate_history_method_boundary' not in packet['gaps']
                and all(window['sample_count']==1 for window in packet['window']['coverage']))
    except (KeyError, TypeError):
        return False


def compose(snapshot, archive, native_path, *, raw_payload=None, engine=None, release=None, now_ms=None,
            cross_section=(), current_packet=None):
    cutoff=snapshot['market']['valuation_ts_ms']
    receipts=packets.collect_source_receipts(snapshot,engine) if engine is not None else {}
    archive.sample(snapshot,recorded_at_ms=max(cutoff,now_ms or cutoff),raw_payload=raw_payload,source_receipts=receipts)
    rows=archive.index_rows() if hasattr(archive,'index_rows') else archive.rows()
    selected=_same_object_rows(rows,snapshot,cutoff,window_row_limit(getattr(archive,'interval_ms',packets.DEFAULT_INTERVAL_MS)))
    sequence=[archive.replay(row['snapshot_id']) for row in selected]
    if not any(p['snapshot']['snapshot_id']==snapshot['snapshot_id'] for p in sequence):
        # A packet the caller already built for this exact input is reused, not rebuilt.
        sequence.append(current_packet if _reusable_current_packet(current_packet,snapshot,raw_payload,receipts)
                        else packets.build_packet(snapshot,raw_payload=raw_payload,source_receipts=receipts))
    cache=archive.path/'native_sources'
    sources=_sources(native_path)
    needed={p['snapshot'].get('fact_context',{}).get('source_hash') for p in sequence}
    for digest in needed:
        if not isinstance(digest,str) or len(digest)!=64: continue
        # New copies are gzip of the exact source bytes; legacy plain copies stay readable.
        saved=cache/(digest+'.json'); packed=cache/(digest+'.json.gz')
        if digest not in sources and (packed.is_file() or saved.is_file()):
            raw=gzip.decompress(packed.read_bytes()) if packed.is_file() else saved.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('native_cache_hash_mismatch')
            sources[digest]=raw
        if digest in sources and not saved.exists() and not packed.exists():
            # Running byte count, not a directory rescan per source.
            if not archive.write_within_budget(packed,gzip.compress(sources[digest],compresslevel=6,mtime=0)):
                # No durable source proof within the same archive budget.
                sources.pop(digest,None)
    reports=[native.qualify_native(p,sources.get(p['snapshot'].get('fact_context',{}).get('source_hash')),
             evidence_kind='native_producer' if p['provenance']['source_kind']=='live_public_capture' else 'synthetic_fixture') for p in sequence]
    return evidence.build_review_evidence(snapshot,sequence,reports,release_identity=release,cross_section=cross_section,
                                         native_sources={key:value for key,value in sources.items() if key in needed})


_SOURCES_CACHE={}


def _sources_cached(path):
    """_sources keyed by the file's (size, mtime_ns): the 2 MB tail is hashed once per change."""
    try:
        stat=Path(path).stat()
    except OSError:
        return {}
    key=(str(path),stat.st_size,stat.st_mtime_ns)
    hit=_SOURCES_CACHE.get(str(path))
    if hit is not None and hit[0]==key:
        return dict(hit[1])
    sources=_sources(path)
    _SOURCES_CACHE[str(path)]=(key,sources)
    return dict(sources)


def compose_current(snapshot, native_path, *, raw_payload=None, engine=None, release=None,
                    cross_section=(), current_packet=None, native_cache=None):
    """compose() for the current point only: no archive sample, no history replay.

    The package has the same shape compose() gives a candidate with no archived history:
    one current packet plus its native-producer qualification (source bytes from the
    native file, else the read-only archive cache). Nothing is written.
    """
    receipts=packets.collect_source_receipts(snapshot,engine) if engine is not None else {}
    packet=(current_packet if _reusable_current_packet(current_packet,snapshot,raw_payload,receipts)
            else packets.build_packet(snapshot,raw_payload=raw_payload,source_receipts=receipts))
    digest=packet['snapshot'].get('fact_context',{}).get('source_hash')
    sources={}
    if isinstance(digest,str) and len(digest)==64:
        raw=_sources_cached(native_path).get(digest)
        if raw is None and native_cache is not None:
            saved=Path(native_cache)/(digest+'.json'); packed=Path(native_cache)/(digest+'.json.gz')
            if packed.is_file() or saved.is_file():
                raw=gzip.decompress(packed.read_bytes()) if packed.is_file() else saved.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('native_cache_hash_mismatch')
        if raw is not None:
            sources[digest]=raw
    report=native.qualify_native(packet,sources.get(digest),
        evidence_kind='native_producer' if packet['provenance']['source_kind']=='live_public_capture' else 'synthetic_fixture')
    return evidence.build_review_evidence(snapshot,[packet],[report],release_identity=release,
                                         cross_section=cross_section,native_sources=sources)


def current_only_evidence(snapshot, *, current_packet=None, release=None, cross_section=()):
    """Build a valid package from the frozen current snapshot when history cannot replay."""
    # Preserve already verified current quote bytes; only historical rows are isolated.
    sequence = (current_packet,) if current_packet is not None else ()
    return evidence.build_review_evidence(snapshot, packet_sequence=sequence, qualification_reports=(),
                                          release_identity=release, cross_section=cross_section)


def require_current_package_binding(snapshot, package):
    """Reject a composed package that does not bind the current frozen candidate."""
    if not isinstance(package, Mapping):
        raise ValueError("review_evidence_not_mapping")
    evidence._require_review_evidence_hash(package)
    if package.get("candidate_id") != snapshot.get("candidate_id"):
        raise ValueError("review_evidence_candidate_id_mismatch")
    if package.get("snapshot_id") != snapshot.get("snapshot_id"):
        raise ValueError("review_evidence_snapshot_id_mismatch")
    bindings = package.get("source_bindings") if isinstance(package.get("source_bindings"), Mapping) else {}
    packet = bindings.get("current_packet") if isinstance(bindings.get("current_packet"), Mapping) else {}
    packets.replay_packet(packet)
    packet_snapshot = packet.get("snapshot") if isinstance(packet.get("snapshot"), Mapping) else {}
    if packet_snapshot.get("candidate_id") != snapshot.get("candidate_id"):
        raise ValueError("review_evidence_packet_candidate_mismatch")
    if packet_snapshot.get("snapshot_id") != snapshot.get("snapshot_id"):
        raise ValueError("review_evidence_packet_snapshot_mismatch")
    if packet_snapshot != snapshot:
        raise ValueError("review_evidence_packet_snapshot_content_mismatch")
    if package.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("review_evidence_packet_hash_mismatch")
    current = package.get("current_cross_section") if isinstance(package.get("current_cross_section"), Mapping) else {}
    if current.get("candidate_id") != snapshot.get("candidate_id"):
        raise ValueError("current_cross_section_candidate_mismatch")
    if current.get("snapshot_id") != snapshot.get("snapshot_id"):
        raise ValueError("current_cross_section_snapshot_mismatch")
    if current.get("packet_hash") != packet.get("packet_hash"):
        raise ValueError("current_cross_section_packet_hash_mismatch")
    same_window = package.get("same_candidate_window") if isinstance(package.get("same_candidate_window"), Mapping) else {}
    window = bindings.get("window_evidence") if isinstance(bindings.get("window_evidence"), Mapping) else {}
    if same_window.get("source_window_evidence_hash") != window.get("window_evidence_hash"):
        raise ValueError("same_candidate_window_source_hash_mismatch")
    return package


def validate_evidence_package(snapshot, package):
    return evidence.validate_review_evidence(package, snapshot)


def _routed_question_observations(package, question):
    """Consume frozen roles only; price/path/compensation already have dedicated rows."""
    units = (package.get('evidence_routing') or {}).get('units') or []
    candidates = [u for u in units if u.get('system_role') in {'FEEDBACK', 'DISTURBANCE', 'CONSTRAINT'}
                  and u.get(question + '_role') in {'SUPPORT', 'COUNTER', 'LIMITATION'}
                  and u.get(question + '_reason_cn')]
    candidates.sort(key=lambda u: 0 if u.get(question + '_role') == 'COUNTER' else 1)
    seen, selected = set(), []
    for unit in candidates:
        # Shared source is not another vote, but its opposing facet is useful.
        facet = (unit.get('dependency_group'), unit.get(question + '_role'))
        if facet in seen:
            continue
        seen.add(facet)
        selected.append(unit)
        if len(selected) == 3:
            break
    return {'routing_observations_cn': [u[question + '_reason_cn'] for u in selected],
            'routing_evidence_ids': [u['evidence_id'] for u in selected]}


def _observed_fragment(package):
    window = package.get('same_candidate_window') or {}
    if window.get('status') != 'INSUFFICIENT':
        return None
    rows = (window.get('coverage_summary') or {}).get('windows') or []
    rows = [row for row in rows if isinstance(row.get('point_count'), int)
            and row['point_count'] > 0 and isinstance(row.get('actual_coverage_ms'), (int, float))]
    if not rows:
        return None
    row = max(rows, key=lambda item: item['actual_coverage_ms'])
    return (f"已保留 {row['point_count']} 个同候选观察点，实际跨度 {row['actual_coverage_ms']/60000:.2f} 分钟；"
            "当前值和已观察片段可读，但不足以概括完整同窗路径。")


def program_questions(snapshot, package):
    """Plain attribution from program facts, explicitly distinct from an LLM review."""
    text=product.window_interpretation(package)
    contract=snapshot['contract']; market=snapshot['market']; liability=snapshot['liability']
    side=contract['side']; adverse='下跌' if side=='put' else '上涨'
    q1=f"这张{side.title()}保单承担价格{adverse}进入卖出腿后的赔付责任。"
    distance=packets.core.responsibility_distance_pct(snapshot,'short')
    if isinstance(distance,(int,float)):
        q1+=f"当前距卖出腿责任边界{distance:+.2f}%（正值表示仍在责任区外）；这描述责任位置。"
    account=snapshot['economics']; credit=account.get('visible_credit_btc'); fee=snapshot['fee'].get('amount_btc')
    parts=[]
    btc=lambda v: (f"{v:.8f}".rstrip('0').rstrip('.') or '0')
    if isinstance(credit,(int,float)): parts.append(f"当前可收信用为{btc(credit)} BTC")
    if isinstance(fee,(int,float)): parts.append(f"持有至到期费用假设为{btc(fee)} BTC")
    q2='，'.join(parts)+'。' if parts else '本次盘口或费用资料不足，尚不能形成补偿取舍。'
    return {'source_kind':'deterministic_program_no_llm',
            'research_context':product._contextual_market_reads(snapshot,package),
            'q1':{'title_cn':'Q1 · 这张保单为什么适配或不适配','summary_cn':q1,'window_attribution_cn':text['path_cn'] if text['path_classification'] not in {None,'gap'} else _observed_fragment(package),
                    **_routed_question_observations(package, 'q1')},
            'q2':{'title_cn':'Q2 · 当前补偿对应哪些风险和取舍','summary_cn':q2,'window_tradeoff_cn':text['compensation_cn'] if text['delta_status']=='STRICT_SAME_CANDIDATE_DELTA' else None,
                  'observable_changes_cn':text['breaks_cn'], **_routed_question_observations(package, 'q2')},
            'audit':{'risk':deepcopy(package['risk_reference_status']),'window_status':text['window_status'],'financial_gate_changed':False,'llm_calls':0}}


def public_evidence(package):
    """Public page/audit summary excludes replay blobs; private proof is separate."""
    if isinstance(package, dict):
        return {key:public_evidence(value) for key,value in package.items()
                if key not in {'composition_inputs','source_bindings'}}
    if isinstance(package, list):
        return [public_evidence(value) for value in package]
    return deepcopy(package)
