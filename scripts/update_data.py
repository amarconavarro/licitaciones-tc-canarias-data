#!/usr/bin/env python3
"""Genera un JSON de licitaciones TC del Mando de Canarias desde OpenPLACSP.

La carga inicial procesa los ZIP anuales oficiales. Las ejecuciones posteriores
leen únicamente las páginas nuevas del feed Atom diario y actualizan el estado
persistido por identificador único de licitación.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable
from xml.etree import ElementTree as ET


DATASET_PATH = "/sindicacion/sindicacion_643"
PRIMARY_HOST = "https://contrataciondelsectorpublico.gob.es"
FALLBACK_HOST = "https://contrataciondelestado.es"
LIVE_FEED_NAME = "licitacionesPerfilesContratanteCompleto3.atom"
ANNUAL_ZIP_PATTERN = "licitacionesPerfilesContratanteCompleto3_{year}.zip"

TARGET_ORGAN = "Jefatura de Asuntos Económicos del Mando de Canarias"
TARGET_ACRONYM = "TC"
WORKS_CODE = "3"

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "state.json"
OUTPUT_PATH = ROOT / "data" / "licitaciones-tc.json"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "at": "http://purl.org/atompub/tombstones/1.0",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cbc-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "cac-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}

STATUS_NAMES = {
    "PRE": "Anuncio previo",
    "PUB": "Publicada",
    "EV": "Evaluación",
    "EV_PRE": "Evaluación previa",
    "ADJ": "Adjudicada",
    "RES": "Resuelta",
    "ANUL": "Anulada",
    "CERRADA": "Cerrada",
}

TYPE_NAMES = {
    "1": "Suministros",
    "2": "Servicios",
    "3": "Obras",
    "21": "Gestión de servicios públicos",
    "31": "Concesión de obras",
    "32": "Concesión de servicios",
    "40": "Colaboración público-privada",
    "50": "Administrativo especial",
    "60": "Privado",
    "70": "Patrimonial",
    "8": "Otros",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def newer(candidate: str | None, current: str | None) -> bool:
    return parse_timestamp(candidate) > parse_timestamp(current)


def node_text(element: ET.Element, path: str) -> str | None:
    node = element.find(path, NS)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def number(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def matches_acronym(value: str, acronym: str = TARGET_ACRONYM) -> bool:
    return re.search(
        rf"(?<![^\W_]){re.escape(acronym)}(?![^\W_])",
        value,
        flags=re.IGNORECASE,
    ) is not None


def entry_to_record(entry: ET.Element) -> dict[str, object]:
    link = entry.find("atom:link", NS)
    source_id = node_text(entry, "atom:id") or ""
    updated = node_text(entry, "atom:updated") or ""
    expediente = node_text(
        entry, "cac-place-ext:ContractFolderStatus/cbc:ContractFolderID"
    ) or ""
    estado_codigo = node_text(
        entry,
        "cac-place-ext:ContractFolderStatus/cbc-place-ext:ContractFolderStatusCode",
    ) or ""
    organ = node_text(
        entry,
        "cac-place-ext:ContractFolderStatus/"
        "cac-place-ext:LocatedContractingParty/cac:Party/cac:PartyName/cbc:Name",
    ) or ""
    organ_id = None
    party = entry.find(
        "cac-place-ext:ContractFolderStatus/"
        "cac-place-ext:LocatedContractingParty/cac:Party",
        NS,
    )
    if party is not None:
        for identifier in party.findall("cac:PartyIdentification/cbc:ID", NS):
            if identifier.get("schemeName") == "ID_PLATAFORMA" and identifier.text:
                organ_id = identifier.text.strip()
                break
    project = entry.find(
        "cac-place-ext:ContractFolderStatus/cac:ProcurementProject", NS
    )
    objeto = node_text(project, "cbc:Name") if project is not None else None
    tipo_codigo = node_text(project, "cbc:TypeCode") if project is not None else None
    importe = (
        number(
            node_text(
                project,
                "cac:BudgetAmount/cbc:EstimatedOverallContractAmount",
            )
        )
        if project is not None
        else None
    )
    fecha_limite = node_text(
        entry,
        "cac-place-ext:ContractFolderStatus/cac:TenderingProcess/"
        "cac:TenderSubmissionDeadlinePeriod/cbc:EndDate",
    )
    return {
        "id": source_id,
        "deleted": False,
        "excluded": False,
        "expediente": expediente,
        "organ": organ,
        "organId": organ_id,
        "tipoCodigo": tipo_codigo or "",
        "tipo": TYPE_NAMES.get(tipo_codigo or "", tipo_codigo or ""),
        "objeto": objeto or node_text(entry, "atom:title") or "",
        "estadoCodigo": estado_codigo,
        "estado": STATUS_NAMES.get(estado_codigo, estado_codigo),
        "importe": importe,
        "fechaLimite": fecha_limite,
        "fechaActualizacion": updated,
        "enlace": link.get("href") if link is not None else None,
    }


def apply_entry(entry: ET.Element, records: dict[str, dict[str, object]]) -> None:
    source_id = node_text(entry, "atom:id") or ""
    updated = node_text(entry, "atom:updated") or ""
    if not source_id:
        return

    existing = records.get(source_id)
    if existing and not newer(updated, str(existing.get("fechaActualizacion") or "")):
        return

    record = entry_to_record(entry)
    if record["organ"] == TARGET_ORGAN:
        records[source_id] = record
    elif existing:
        records[source_id] = {
            "id": source_id,
            "fechaActualizacion": updated,
            "deleted": False,
            "excluded": True,
        }


def apply_deleted_entry(
    deleted: ET.Element, records: dict[str, dict[str, object]]
) -> None:
    source_id = deleted.get("ref") or ""
    when = deleted.get("when") or ""
    existing = records.get(source_id)
    if existing and newer(when, str(existing.get("fechaActualizacion") or "")):
        existing["fechaActualizacion"] = when
        existing["estadoCodigo"] = "CERRADA"
        existing["estado"] = "Cerrada"
        existing["deleted"] = True


def process_atom(
    stream: BinaryIO, records: dict[str, dict[str, object]]
) -> tuple[str | None, str | None, int]:
    root = ET.parse(stream).getroot()
    feed_updated = node_text(root, "atom:updated")
    next_url = None
    for link in root.findall("atom:link", NS):
        if link.get("rel") == "next":
            next_url = link.get("href")
            break

    processed = 0
    for entry in root.findall("atom:entry", NS):
        apply_entry(entry, records)
        processed += 1
    for deleted in root.findall("at:deleted-entry", NS):
        apply_deleted_entry(deleted, records)
        processed += 1
    return feed_updated, next_url, processed


def request_bytes(url: str, attempts: int = 4) -> bytes:
    hosts = [PRIMARY_HOST, FALLBACK_HOST]
    path = url
    for host in hosts:
        if url.startswith(PRIMARY_HOST):
            path = url.removeprefix(PRIMARY_HOST)
        elif url.startswith(FALLBACK_HOST):
            path = url.removeprefix(FALLBACK_HOST)
        candidate = host + path if path.startswith("/") else url
        for attempt in range(attempts):
            request = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": "licitaciones-tc-canarias-data/1.0",
                    "Accept": "application/atom+xml, application/xml, application/zip",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt + 1 == attempts:
                    last_error = error
                    break
                time.sleep(2**attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def download_file(url: str, destination: str, attempts: int = 4) -> None:
    hosts = [PRIMARY_HOST, FALLBACK_HOST]
    path = url
    for host in hosts:
        if url.startswith(PRIMARY_HOST):
            path = url.removeprefix(PRIMARY_HOST)
        elif url.startswith(FALLBACK_HOST):
            path = url.removeprefix(FALLBACK_HOST)
        candidate = host + path if path.startswith("/") else url
        for attempt in range(attempts):
            request = urllib.request.Request(
                candidate,
                headers={
                    "User-Agent": "licitaciones-tc-canarias-data/1.0",
                    "Accept": "application/zip",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    with open(destination, "wb") as output:
                        shutil.copyfileobj(response, output, length=1024 * 1024)
                return
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt + 1 == attempts:
                    last_error = error
                    break
                time.sleep(2**attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def annual_url(year: int) -> str:
    filename = ANNUAL_ZIP_PATTERN.format(year=year)
    return f"{PRIMARY_HOST}{DATASET_PATH}/{filename}"


def process_annual_zip(
    year: int, records: dict[str, dict[str, object]]
) -> tuple[int, str | None]:
    print(f"Descargando archivo oficial {year}...", flush=True)
    processed = 0
    with tempfile.NamedTemporaryFile(suffix=".zip") as temporary:
        download_file(annual_url(year), temporary.name)
        with zipfile.ZipFile(temporary.name) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".atom", ".xml"))
            ]
            members.sort(
                key=lambda name: (
                    Path(name).name == LIVE_FEED_NAME,
                    Path(name).name,
                )
            )
            if not members:
                raise RuntimeError(f"El ZIP oficial de {year} no contiene Atom/XML")
            latest_feed_update = None
            for name in members:
                with archive.open(name) as stream:
                    feed_updated, _, count = process_atom(stream, records)
                    processed += count
                    if feed_updated and (
                        not latest_feed_update
                        or newer(feed_updated, latest_feed_update)
                    ):
                        latest_feed_update = feed_updated
    print(f"{year}: {processed} actualizaciones procesadas", flush=True)
    return processed, latest_feed_update


def backfill(
    records: dict[str, dict[str, object]], start_year: int, end_year: int
) -> tuple[int, str | None]:
    records.clear()
    total = 0
    watermark = None
    for year in range(start_year, end_year + 1):
        processed, feed_updated = process_annual_zip(year, records)
        total += processed
        if feed_updated and (not watermark or newer(feed_updated, watermark)):
            watermark = feed_updated
    return total, watermark


def update_from_live_feed(
    records: dict[str, dict[str, object]], watermark: str | None, max_pages: int = 100
) -> tuple[int, str | None]:
    url = f"{PRIMARY_HOST}{DATASET_PATH}/{LIVE_FEED_NAME}"
    processed = 0
    newest = watermark
    for page in range(1, max_pages + 1):
        payload = request_bytes(url)
        feed_updated, next_url, count = process_atom_bytes(payload, records)
        processed += count
        if feed_updated and (not newest or newer(feed_updated, newest)):
            newest = feed_updated
        print(f"Feed diario, página {page}: {count} actualizaciones", flush=True)
        if watermark and feed_updated and not newer(feed_updated, watermark):
            break
        if not next_url:
            break
        url = next_url
    else:
        raise RuntimeError("Se alcanzó el límite de páginas del feed diario")
    return processed, newest


def process_atom_bytes(
    payload: bytes, records: dict[str, dict[str, object]]
) -> tuple[str | None, str | None, int]:
    import io

    return process_atom(io.BytesIO(payload), records)


def load_state() -> dict[str, object]:
    if not STATE_PATH.exists():
        return {"initialized": False, "watermark": None, "records": {}}
    with STATE_PATH.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload.get("records"), dict):
        raise RuntimeError("data/state.json no contiene un estado válido")
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=False)
        stream.write("\n")
    os.replace(temporary, path)


def public_rows(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for record in records:
        if record.get("deleted") or record.get("excluded"):
            continue
        if record.get("tipoCodigo") != WORKS_CODE:
            continue
        objeto = str(record.get("objeto") or "")
        if not matches_acronym(objeto):
            continue
        rows.append(
            {
                "expediente": record.get("expediente"),
                "tipo": "Obras",
                "objeto": objeto,
                "estado": record.get("estado"),
                "estadoCodigo": record.get("estadoCodigo"),
                "importe": record.get("importe"),
                "fechaPublicacion": None,
                "fechaLimite": record.get("fechaLimite"),
                "fechaActualizacion": record.get("fechaActualizacion"),
                "enlace": record.get("enlace"),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("fechaLimite") or ""),
            str(row.get("expediente") or ""),
        ),
        reverse=True,
    )


def save(state: dict[str, object], processed: int, mode: str) -> None:
    generated = utc_now()
    records = state["records"]
    assert isinstance(records, dict)
    rows = public_rows(records.values())
    state["initialized"] = True
    state["generatedAt"] = generated
    write_json(STATE_PATH, state)
    write_json(
        OUTPUT_PATH,
        {
            "data": rows,
            "generatedAt": generated,
            "sourceUpdated": state.get("watermark"),
            "source": f"{PRIMARY_HOST}{DATASET_PATH}/{LIVE_FEED_NAME}",
            "filters": {
                "organ": TARGET_ORGAN,
                "tipo": "Obras",
                "objetoContieneAcronimo": TARGET_ACRONYM,
            },
            "count": len(rows),
            "processedUpdates": processed,
            "mode": mode,
        },
    )
    print(f"JSON generado: {len(rows)} licitaciones TC", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--start-year", type=int, default=2012)
    parser.add_argument("--end-year", type=int, default=datetime.now().year)
    args = parser.parse_args()

    state = load_state()
    records = state["records"]
    assert isinstance(records, dict)
    needs_backfill = args.backfill or not state.get("initialized")
    if needs_backfill:
        processed, watermark = backfill(records, args.start_year, args.end_year)
        state["watermark"] = watermark
        save(state, processed, "backfill")
        return

    processed, watermark = update_from_live_feed(
        records, str(state.get("watermark") or "") or None
    )
    state["watermark"] = watermark
    save(state, processed, "incremental")


if __name__ == "__main__":
    main()
