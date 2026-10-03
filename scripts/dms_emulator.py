#!/usr/bin/env python3
"""
dms_emulator.py — deja en disco lo mismo que deja DMS en raw/acquiring/.

No replica nada: escribe el LAYOUT de DMS, para que el job de Glue se pruebe sin
AWS contra la forma exacta que va a encontrarse. Es lo que no se hizo la
primera vez —cada lab funcionaba por separado y nunca se habian ejecutado
juntos— y DMS dejo los ficheros donde el job no miraba.

El layout, como lo configura infra/lab1/dms.tf:

    raw/acquiring/<tabla>/LOAD00000001.parquet          la foto inicial, Op = I
    raw/acquiring/<tabla>/AAAA/MM/DD/<ts>.parquet       los cambios, Op = I/U/D

Cada fila lleva Op y dms_timestamp delante de las columnas de la tabla. Las
columnas que DMS quita (merchants.settlement_account) tampoco estan.

Dos origenes:
    snapshot_from_db(conninfo)   las tablas de un PostgreSQL con el esquema
    synthesize(...)              un core sintetico, para correr sin base

y cdc_delta(antes, despues) produce los cambios entre dos fotos, como los
veria el CDC: filas nuevas como I, cambiadas como U, desaparecidas como D.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Las de infra/lab1/dms.tf que el job de Glue necesita.
TABLES = ("merchants", "terminals", "payment_intents", "charges", "balance_transactions")
# La regla remove-column de la tarea.
REMOVED_COLUMNS = {"merchants": ["settlement_account"]}


def snapshot_from_db(conninfo: str) -> dict[str, pd.DataFrame]:
    """SELECT * de cada tabla replicada, como la foto inicial del full load."""
    import psycopg

    out = {}
    with psycopg.connect(conninfo) as conn:
        for t in TABLES:
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM acquiring.{t} ORDER BY id")
                cols = [d.name for d in cur.description]
                out[t] = _like_dms(pd.DataFrame(cur.fetchall(), columns=cols), t)
    return out


def _like_dms(df: pd.DataFrame, tabla: str) -> pd.DataFrame:
    """Los tipos como los escribe DMS en Parquet."""
    df = df.drop(columns=REMOVED_COLUMNS.get(tabla, []), errors="ignore").copy()
    for col in df.columns:
        if df[col].dtype == object:
            # jsonb, enums y text: DMS los escribe como cadenas.
            df[col] = df[col].map(lambda v: None if v is None else
                                  (v if isinstance(v, str) else _json(v)))
        elif str(df[col].dtype).startswith("datetime64"):
            # parquet_timestamp_in_millisecond, sin zona: UTC.
            df[col] = pd.to_datetime(df[col], utc=True).dt.tz_localize(None).astype("datetime64[ms]")
    return df


def _json(v) -> str:
    import json
    from decimal import Decimal
    from datetime import date

    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return json.dumps(v, default=str)


def cdc_delta(antes: dict[str, pd.DataFrame], despues: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Lo que cambio entre dos fotos, con su Op."""
    out = {}
    for t in TABLES:
        a, d = antes.get(t, pd.DataFrame()), despues.get(t, pd.DataFrame())
        ids_a = set(a["id"]) if not a.empty else set()
        ids_d = set(d["id"]) if not d.empty else set()
        filas = []
        for _, row in d.iterrows():
            if row["id"] not in ids_a:
                filas.append({"Op": "I", **row.to_dict()})
            else:
                previa = a[a["id"] == row["id"]].iloc[0]
                if not _iguales(previa, row):
                    filas.append({"Op": "U", **row.to_dict()})
        for _, row in a[~a["id"].isin(ids_d)].iterrows() if not a.empty else []:
            filas.append({"Op": "D", **row.to_dict()})
        out[t] = pd.DataFrame(filas, columns=["Op", *d.columns]) if filas else pd.DataFrame()
    return out


def _iguales(a: pd.Series, b: pd.Series) -> bool:
    for k in b.index:
        va, vb = a.get(k), b.get(k)
        if pd.isna(va) and pd.isna(vb):
            continue
        if va != vb:
            return False
    return True


def _dms_ts(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def write_layout(root: Path, load: dict[str, pd.DataFrame],
                 deltas: list[tuple[datetime, dict[str, pd.DataFrame]]] = (),
                 load_ts: datetime | None = None) -> list[Path]:
    """Escribe la foto inicial y los ficheros de cambios bajo root/raw/acquiring/."""
    escritos = []
    load_ts = load_ts or datetime.now(timezone.utc)
    base = root / "raw" / "acquiring"
    for t, df in load.items():
        if df.empty:
            continue
        dms = df.copy()
        # Texto y no timestamp: es como lo escribe DMS de verdad en Parquet
        # ("2026-10-03 01:34:13.774290"), comprobado en el bucket real.
        dms.insert(0, "dms_timestamp", _dms_ts(load_ts))
        dms.insert(0, "Op", "I")  # include_op_for_full_load = true
        p = base / t / "LOAD00000001.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        dms.to_parquet(p, index=False)
        escritos.append(p)
    for ts, tablas in deltas:
        for t, df in tablas.items():
            if df.empty:
                continue
            dms = df.copy()
            dms.insert(1, "dms_timestamp", _dms_ts(ts))
            # date_partition_enabled + YYYYMMDD
            p = base / t / ts.strftime("%Y/%m/%d") / f"{ts.strftime('%Y%m%d-%H%M%S%f')}.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            dms.to_parquet(p, index=False)
            escritos.append(p)
    return escritos


# ── Un core sintetico, para correr sin base ──────────────────────────────────
def synthesize(cobros: int = 120, seed: int = 7) -> tuple[dict, dict]:
    """
    Dos fotos de un core coherente: la segunda con cobros nuevos y con
    intenciones que pasaron de pendientes a cobradas, que es el U que el CDC
    tiene que traer.

    Coherente quiere decir que respeta lo que la base garantiza: net = gross -
    fee, una captura lleva response_code 00 y auth_code, MDR del comercio.
    """
    ahora = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)

    comercios = [
        (1, "MID000000020001", "Picanteria Arequipa", "5812", "LOW", 299, 30, "PEN"),
        (2, "MID000000020005", "Casa de Cambio Sol", "6051", "HIGH", 450, 50, "USD"),
        (3, "MID000000020007", "TecnoMarket Online", "5732", "MEDIUM", 349, 30, "PEN"),
        (4, "MID000000020010", "PixelPlay Store", "5816", "HIGH", 499, 30, "USD"),
    ]
    merchants = pd.DataFrame([
        {"id": i, "merchant_id": mid, "legal_name": f"{n} S.A.C.", "trade_name": n,
         "mcc": mcc, "risk_level": r, "status": "ACTIVE", "mdr_bps": mdr,
         "fixed_fee_minor": fee, "default_currency": cur}
        for i, mid, n, mcc, r, mdr, fee, cur in comercios
    ])
    terminals = pd.DataFrame([
        {"id": 1, "merchant_id": 1, "terminal_id": "TID00002", "device_serial": "sim-tid00002"},
        {"id": 2, "merchant_id": 2, "terminal_id": "TID00008", "device_serial": "sim-tid00008"},
    ])

    def una_foto(n: int, capturar_pendientes: bool):
        rng_f = random.Random(seed)
        intents, charges, ledger = [], [], []
        for i in range(1, n + 1):
            cid, mid, _, mcc, _, mdr, fee_fijo, cur = comercios[rng_f.randrange(len(comercios))]
            online = mcc in ("5732", "5816")
            term = None if online else (1 if cid == 1 else 2)
            canal = "CARD_NOT_PRESENT" if online else rng_f.choice(["QR", "CARD_PRESENT"])
            monto = rng_f.randint(500, 90000)
            creado = ahora - timedelta(days=rng_f.randint(0, 60), minutes=rng_f.randint(0, 1440))
            codigo = rng_f.choices(["00", "05", "51", "54"], weights=[85, 6, 5, 4])[0]
            # Un 10% de los QR se queda esperando al pagador en la primera foto.
            pendiente = canal == "QR" and rng_f.random() < 0.10 and not capturar_pendientes
            ref = f"PISYN{i:06d}"
            intents.append({
                "id": i, "intent_reference": ref, "merchant_id": cid, "terminal_id": term,
                "amount_minor": monto, "currency_code": cur, "channel": canal,
                "status": "REQUIRES_CONFIRMATION" if pendiente else
                          ("SUCCEEDED" if codigo == "00" else "REQUIRES_PAYMENT_METHOD"),
                "created_at": creado,
            })
            if pendiente:
                continue
            charges.append({
                "id": i, "charge_reference": f"ch_{ref}_1", "payment_intent_id": i,
                "merchant_id": cid, "terminal_id": term, "amount_minor": monto,
                "amount_captured_minor": monto if codigo == "00" else 0,
                "currency_code": cur, "status": "CAPTURED" if codigo == "00" else "FAILED",
                "channel": canal, "mti": "0110", "stan": f"{i:06d}",
                "rrn": creado.strftime("%y%m%d") + f"{i:06d}", "response_code": codigo,
                "auth_code": f"MOCK-{1000 + i % 9000}" if codigo == "00" else None,
                "created_at": creado, "captured_at": creado if codigo == "00" else None,
            })
            if codigo == "00":
                fee = monto * mdr // 10_000 + fee_fijo
                ledger.append({"id": i, "merchant_id": cid, "source_type": "CHARGE",
                               "charge_id": i, "gross_minor": monto, "fee_minor": fee,
                               "net_minor": monto - fee, "currency_code": cur})
        return {
            "merchants": merchants, "terminals": terminals,
            "payment_intents": pd.DataFrame(intents), "charges": pd.DataFrame(charges),
            "balance_transactions": pd.DataFrame(ledger),
        }

    antes = {k: _like_dms(v, k) for k, v in una_foto(cobros, False).items()}
    despues = {k: _like_dms(v, k) for k, v in una_foto(cobros + cobros // 4, True).items()}
    return antes, despues
