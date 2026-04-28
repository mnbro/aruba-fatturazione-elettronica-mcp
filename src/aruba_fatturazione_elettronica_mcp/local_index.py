"""SQLite index for read-only Aruba data cached locally."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class InvoiceIndex:
    """Small SQLite index used by optional LLM-friendly tools."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                direction TEXT NOT NULL,
                filename TEXT NOT NULL,
                invoice_date TEXT,
                invoice_number TEXT,
                supplier_name TEXT,
                supplier_vat TEXT,
                customer_name TEXT,
                customer_vat TEXT,
                net_total TEXT,
                vat_total TEXT,
                gross_total TEXT,
                status TEXT,
                raw_json TEXT NOT NULL,
                parsed_json TEXT,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(direction, filename)
            )
            """
        )
        return conn

    def upsert_invoice(
        self,
        *,
        direction: str,
        filename: str,
        raw: dict[str, Any],
        parsed: dict[str, Any] | None,
        status: str | None = None,
    ) -> None:
        document = parsed.get("document", {}) if parsed else {}
        supplier = parsed.get("supplier", {}) if parsed else {}
        customer = parsed.get("customer", {}) if parsed else {}
        amounts = parsed.get("amounts", {}) if parsed else {}
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO invoices (
                    direction, filename, invoice_date, invoice_number, supplier_name,
                    supplier_vat, customer_name, customer_vat, net_total, vat_total,
                    gross_total, status, raw_json, parsed_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(direction, filename) DO UPDATE SET
                    invoice_date=excluded.invoice_date,
                    invoice_number=excluded.invoice_number,
                    supplier_name=excluded.supplier_name,
                    supplier_vat=excluded.supplier_vat,
                    customer_name=excluded.customer_name,
                    customer_vat=excluded.customer_vat,
                    net_total=excluded.net_total,
                    vat_total=excluded.vat_total,
                    gross_total=excluded.gross_total,
                    status=excluded.status,
                    raw_json=excluded.raw_json,
                    parsed_json=excluded.parsed_json,
                    synced_at=excluded.synced_at
                """,
                (
                    direction,
                    filename,
                    document.get("date"),
                    document.get("number"),
                    supplier.get("name"),
                    supplier.get("vat", {}).get("code"),
                    customer.get("name"),
                    customer.get("vat", {}).get("code"),
                    amounts.get("net"),
                    amounts.get("vat"),
                    amounts.get("gross"),
                    status,
                    json.dumps(raw, sort_keys=True),
                    json.dumps(parsed, sort_keys=True) if parsed else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def search(self, query: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if direction := query.get("direction"):
            clauses.append("direction = ?")
            values.append(direction)
        if text := query.get("text"):
            clauses.append("(raw_json LIKE ? OR parsed_json LIKE ?)")
            values.extend([f"%{text}%", f"%{text}%"])
        if vat_code := query.get("vat_code"):
            clauses.append("(supplier_vat = ? OR customer_vat = ?)")
            values.extend([vat_code, vat_code])
        if party := query.get("party_name_contains"):
            clauses.append("(supplier_name LIKE ? OR customer_name LIKE ?)")
            values.extend([f"%{party}%", f"%{party}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with closing(self.connect()) as conn:
            sql = f"SELECT * FROM invoices {where} ORDER BY invoice_date DESC LIMIT ?"  # noqa: S608
            rows = conn.execute(sql, values).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            totals = conn.execute(
                "SELECT direction, COUNT(*) count FROM invoices GROUP BY direction"
            ).fetchall()
            summary = conn.execute(
                """
                SELECT COUNT(*) count,
                       MIN(invoice_date) oldest,
                       MAX(invoice_date) newest,
                       MAX(synced_at) last_sync_at
                FROM invoices
                """
            ).fetchone()
        return {
            "invoice_count": summary["count"] if summary else 0,
            "oldest_invoice_date": summary["oldest"] if summary else None,
            "newest_invoice_date": summary["newest"] if summary else None,
            "last_sync_at": summary["last_sync_at"] if summary else None,
            "directions": {row["direction"]: row["count"] for row in totals},
        }
