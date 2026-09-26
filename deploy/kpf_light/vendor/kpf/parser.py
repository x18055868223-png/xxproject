from __future__ import annotations

import csv
import io
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from .models import ParseAudit, TradeRecord


HEADER_TOKENS = {"agg", "aggregate", "price", "quantity", "timestamp", "time"}


def detect_timestamp_unit(raw_ts: int) -> tuple[int, str]:
    if raw_ts > 10**15:
        return raw_ts // 1000, "microseconds"
    return raw_ts, "milliseconds"


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "t", "yes"}


class AggTradesParser:
    def iter_trades(self, zip_path: Path, audit: ParseAudit | None = None) -> Iterator[TradeRecord]:
        if audit is None:
            audit = ParseAudit(source_file=str(zip_path))
        with zipfile.ZipFile(zip_path) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            if not members:
                audit.schema_failed = True
                return
            member = next((name for name in members if name.lower().endswith(".csv")), members[0])
            with archive.open(member, "r") as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
                reader = csv.reader(text)
                for line_number, row in enumerate(reader, start=1):
                    audit.rows_total += 1
                    if not row:
                        continue
                    if line_number == 1 and self._looks_like_header(row):
                        continue
                    try:
                        trade = self._parse_row(row, zip_path.name)
                    except Exception as exc:
                        if len(audit.bad_rows) < 20:
                            audit.bad_rows.append(
                                {
                                    "line_number": line_number,
                                    "sample": ",".join(row)[:500],
                                    "error": str(exc),
                                }
                            )
                        continue
                    audit.rows_ok += 1
                    audit.timestamp_units.add("microseconds" if trade.ts_ms > 10**12 and len(row[5]) > 13 else "milliseconds")
                    yield trade
        if audit.rows_ok == 0:
            audit.schema_failed = True

    def parse_file(self, zip_path: Path) -> tuple[list[TradeRecord], ParseAudit]:
        audit = ParseAudit(source_file=str(zip_path))
        trades = list(self.iter_trades(zip_path, audit))
        return trades, audit

    def _looks_like_header(self, row: list[str]) -> bool:
        joined = " ".join(cell.strip().lower() for cell in row)
        return any(token in joined for token in HEADER_TOKENS) and not row[0].strip().isdigit()

    def _parse_row(self, row: list[str], source_file: str) -> TradeRecord:
        if len(row) < 7:
            raise ValueError(f"expected at least 7 columns, got {len(row)}")
        try:
            raw_ts = int(row[5])
            ts_ms, _unit = detect_timestamp_unit(raw_ts)
            return TradeRecord(
                agg_id=int(row[0]),
                price=Decimal(row[1]),
                qty=Decimal(row[2]),
                first_trade_id=int(row[3]),
                last_trade_id=int(row[4]),
                ts_ms=ts_ms,
                is_buyer_maker=_parse_bool(row[6]),
                source_file=source_file,
            )
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"invalid aggTrades row: {exc}") from exc
