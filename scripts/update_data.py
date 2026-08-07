#!/usr/bin/env python3
"""Genera el JSON TC consultando solo el perfil del Mando de Canarias."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from lxml import html


BASE_URL = "https://contrataciondelestado.es"
PROFILE_LIST_URL = f"{BASE_URL}/wps/portal/plataforma/perfil_contratante/lista_perfiles"
PROFILE_ID = "gwHbdMZ49t4="
PROFILE_URL = (
    f"{BASE_URL}/wps/poc?uri=deeplink:perfilContratante&"
    f"idBp={urllib.parse.quote(PROFILE_ID, safe='')}"
)
TARGET_ORGAN = "Jefatura de Asuntos Económicos del Mando de Canarias"
TARGET_ACRONYM = "TC"
WORKS_CODE = "3"
START_YEAR = 2025
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "licitaciones-tc.json"
USER_AGENT = "licitaciones-tc-canarias-data/2.0 (+GitHub Actions)"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def matches_acronym(value: str, acronym: str = TARGET_ACRONYM) -> bool:
    return re.search(
        rf"(?<![^\W_]){re.escape(acronym)}(?![^\W_])",
        value,
        flags=re.IGNORECASE,
    ) is not None


def expediente_year(expediente: str) -> int | None:
    match = re.match(r"\s*(\d{4})(?:/|\b)", expediente)
    return int(match.group(1)) if match else None


def parse_amount(value: str) -> float | None:
    raw = re.sub(r"[^\d,.-]", "", value)
    if not raw:
        return None
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[1]
        raw = raw.replace(".", "").replace(",", "." if len(tail) <= 2 else "")
    try:
        return float(raw)
    except ValueError:
        return None


def xml_name(name: str) -> str:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        known = {
            "http://www.w3.org/2005/Atom": "atom",
            "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2": "cbc",
            "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2": "cac",
            "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2": "cbc-place-ext",
            "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2": "cac-place-ext",
        }
        return f"{known.get(namespace, namespace)}:{local_name}"
    return name


def xml_to_data(element: ET.Element) -> object:
    children = list(element)
    attributes = {f"@{xml_name(key)}": value for key, value in element.attrib.items()}
    text = (element.text or "").strip()
    if not children and not attributes:
        return text
    result: dict[str, object] = dict(attributes)
    if text:
        result["#text"] = text
    for child in children:
        key = xml_name(child.tag)
        value = xml_to_data(child)
        if key not in result:
            result[key] = value
        elif isinstance(result[key], list):
            result[key].append(value)
        else:
            result[key] = [result[key], value]
    return result



def local_name(tag: str) -> str:
    """Nombre XML sin espacio de nombres."""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def direct_children(element: ET.Element, *names: str) -> list[ET.Element]:
    wanted = set(names)
    return [child for child in element if local_name(child.tag) in wanted]


def first_text(element: ET.Element, *names: str) -> str | None:
    """Primer texto no vacío de un descendiente con uno de esos nombres."""
    wanted = set(names)
    for child in element.iter():
        if local_name(child.tag) in wanted:
            text = clean_text(child.text)
            if text:
                return text
    return None


def first_code_label(element: ET.Element, *names: str) -> str | None:
    """Etiqueta legible de un código CODICE; usa el valor si no trae @name."""
    wanted = set(names)
    for child in element.iter():
        if local_name(child.tag) in wanted:
            label = clean_text(child.attrib.get("name", ""))
            if label:
                return label
            text = clean_text(child.text)
            if text:
                return text
    return None


def amount_text(element: ET.Element, *names: str) -> float | None:
    text = first_text(element, *names)
    return parse_amount(text or "") if text else None


def result_rows_from_xml(root: ET.Element, estado_listado: str) -> list[dict[str, object]]:
    """Normaliza el resultado CODICE a una fila por lote/adjudicatario.

    El XML CODICE puede expresar una misma licitación con varios TenderResult.
    Cada uno se conserva como una fila independiente para que Excel trate los
    lotes como contratos operativos separados.
    """
    rows: list[dict[str, object]] = []
    for ordinal, tender_result in enumerate(
        (node for node in root.iter() if local_name(node.tag) == "TenderResult"), 1
    ):
        awarded = direct_children(tender_result, "AwardedTenderedProject")
        awarded_project = awarded[0] if awarded else tender_result
        monetary = direct_children(awarded_project, "LegalMonetaryTotal")
        monetary_total = monetary[0] if monetary else awarded_project
        lot_nodes = [
            node
            for node in awarded_project.iter()
            if local_name(node.tag) == "ProcurementProjectLot"
        ]
        lote = first_text(lot_nodes[0], "ID") if lot_nodes else None
        resultado = first_code_label(
            tender_result, "ResultCode", "ResultStatus", "TenderResultCode"
        ) or estado_listado
        fecha_acuerdo = first_text(
            tender_result, "AwardDate", "DecisionDate", "AwardingDecisionDate"
        )
        contract_nodes = direct_children(tender_result, "Contract")
        contract = contract_nodes[0] if contract_nodes else tender_result
        fecha_formalizacion = first_text(
            contract, "FormalizationDate", "ContractFormalizationDate", "IssueDate"
        )
        fecha_vigor = first_text(
            contract, "EffectiveDate", "StartDate", "ContractStartDate"
        )
        importe_sin_iva = amount_text(
            monetary_total, "TaxExclusiveAmount", "TaxExclusiveTotalAmount"
        )
        importe_con_iva = amount_text(
            monetary_total, "PayableAmount", "TaxInclusiveAmount"
        )
        if importe_sin_iva is None:
            importe_sin_iva = amount_text(
                awarded_project, "TaxExclusiveAmount", "EstimatedOverallContractAmount"
            )
        if importe_con_iva is None:
            importe_con_iva = amount_text(
                awarded_project, "PayableAmount", "TaxInclusiveAmount"
            )
        numero_ofertas = amount_text(
            tender_result, "ReceivedTenderQuantity", "TendererRequirement"
        )
        winning_parties = direct_children(tender_result, "WinningParty") or [None]
        for party_ordinal, party in enumerate(winning_parties, 1):
            adjudicatario = (
                first_text(party, "PartyName", "Name") if party is not None else None
            )
            nif = (
                first_text(party, "CompanyID", "ID") if party is not None else None
            )
            rows.append(
                {
                    "idResultado": f"{ordinal}-{party_ordinal}",
                    "lote": lote,
                    "resultado": resultado,
                    "adjudicatario": adjudicatario,
                    "nifAdjudicatario": nif,
                    "importeAdjudicacionSinIVA": importe_sin_iva,
                    "importeAdjudicacionConIVA": importe_con_iva,
                    "fechaAcuerdoAdjudicacion": fecha_acuerdo,
                    "fechaFormalizacion": fecha_formalizacion,
                    "fechaEntradaVigor": fecha_vigor,
                    "numeroOfertasRecibidas": numero_ofertas,
                }
            )
    return rows


def flatten_result_rows(expedientes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Repite el expediente por cada resultado/lote para carga tabular."""
    flat: list[dict[str, object]] = []
    empty_result = {
        "idResultado": None,
        "lote": None,
        "resultado": None,
        "adjudicatario": None,
        "nifAdjudicatario": None,
        "importeAdjudicacionSinIVA": None,
        "importeAdjudicacionConIVA": None,
        "fechaAcuerdoAdjudicacion": None,
        "fechaFormalizacion": None,
        "fechaEntradaVigor": None,
        "numeroOfertasRecibidas": None,
    }
    for expediente in expedientes:
        results = expediente.get("resultados") or [empty_result]
        for result in results:
            row = dict(expediente)
            row.update(result)
            row["id"] = (
                f"{expediente['expediente']}#{result['idResultado']}"
                if result.get("idResultado")
                else str(expediente["expediente"])
            )
            flat.append(row)
    return flat

def suffix(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def form_request(document: html.HtmlElement, submit_suffix: str, overrides=None):
    """Devuelve URL y cuerpo de un formulario JSF, conservando su ViewState."""
    overrides = overrides or {}
    candidates = document.xpath(
        f'//form[.//*[@name and substring-after(concat(":", @name), ":")="{submit_suffix}"]]'
    )
    if not candidates:
        # XPath 1.0 no ofrece ends-with; esta ruta cubre nombres sin prefijo.
        candidates = [
            form
            for form in document.xpath("//form")
            if any(
                suffix(node.get("name")) == submit_suffix
                for node in form.xpath('.//*[@name]')
            )
        ]
    if not candidates:
        raise RuntimeError(f"No se encontró el formulario para {submit_suffix}")
    form = candidates[0]
    pairs: list[tuple[str, str]] = []
    for node in form.xpath('.//input[@name]'):
        name = node.get("name")
        kind = (node.get("type") or "text").lower()
        if kind in {"submit", "button", "image", "file", "reset"}:
            continue
        if kind in {"checkbox", "radio"} and node.get("checked") is None:
            continue
        pairs.append((name, str(overrides.get(suffix(name), node.get("value", "")))))
    for node in form.xpath('.//select[@name]'):
        name = node.get("name")
        selected = node.xpath('./option[@selected]') or node.xpath('./option[1]')
        if selected:
            pairs.append(
                (name, str(overrides.get(suffix(name), selected[0].get("value", ""))))
            )
    for node in form.xpath('.//textarea[@name]'):
        name = node.get("name")
        pairs.append((name, str(overrides.get(suffix(name), node.text or ""))))
    submit = next(
        node
        for node in form.xpath('.//*[@name]')
        if suffix(node.get("name")) == submit_suffix
    )
    pairs.append((submit.get("name"), submit.get("value", "")))
    action = urllib.parse.urljoin(BASE_URL, form.get("action"))
    return action, urllib.parse.urlencode(pairs).encode("utf-8")


def parse_listing(document: html.HtmlElement) -> list[dict[str, object]]:
    """Extrae las seis columnas visibles y el enlace de cada expediente."""
    rows: list[dict[str, object]] = []
    for anchor in document.xpath('//a[contains(@href, "deeplink:detalle_licitacion")]'):
        tr = anchor.xpath('ancestor::tr[1]')
        if not tr:
            continue
        cells = tr[0].xpath('./td')
        if len(cells) < 6:
            continue
        values = [clean_text(cell.text_content()) for cell in cells[:6]]
        expediente, tipo, objeto, estado, importe_texto, fechas = values
        rows.append(
            {
                "id": expediente,
                "expediente": expediente,
                "organ": TARGET_ORGAN,
                "organId": PROFILE_ID,
                "tipoCodigo": WORKS_CODE,
                "tipo": tipo,
                "objeto": objeto,
                "estado": estado,
                "importe": parse_amount(importe_texto),
                "importeTexto": importe_texto,
                "fechas": fechas,
                "enlace": urllib.parse.urljoin(BASE_URL, anchor.get("href")),
            }
        )
    # Algunas vistas repiten enlaces accesibles al mismo expediente.
    return list({str(row["expediente"]): row for row in rows}.values())


def has_submit(document: html.HtmlElement, submit_suffix: str) -> bool:
    return any(
        suffix(node.get("name")) == submit_suffix and node.get("disabled") is None
        for node in document.xpath('.//*[@name]')
    )


def latest_xml_link(document: html.HtmlElement) -> dict[str, str] | None:
    documents = []
    for anchor in document.xpath('//a[@href][.//img]'):
        alt = clean_text(" ".join(anchor.xpath('.//img/@alt'))).lower()
        if "xml" not in alt:
            continue
        row = anchor.xpath('ancestor::tr[1]')
        row_text = clean_text(row[0].text_content()) if row else ""
        date_match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", row_text)
        date = date_match.group(1) if date_match else ""
        documents.append(
            {
                "url": urllib.parse.urljoin(BASE_URL, anchor.get("href")),
                "fecha": date,
                "descripcion": row_text,
            }
        )
    if not documents:
        return None
    return max(
        documents,
        key=lambda item: (
            datetime.strptime(item["fecha"], "%d/%m/%Y") if item["fecha"] else datetime.min,
            documents.index(item),
        ),
    )


class PortalClient:
    def __init__(self, attempts: int = 4, timeout: int = 120):
        self.attempts = attempts
        self.timeout = timeout
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )

    def request(self, url: str, data: bytes | None = None, accept="text/html") -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                request = urllib.request.Request(url, data=data, headers=headers)
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt + 1 < self.attempts:
                    time.sleep(2**attempt)
        raise RuntimeError(f"No se pudo descargar {url}: {last_error}")

    def document(self, url: str, data: bytes | None = None) -> html.HtmlElement:
        return html.fromstring(self.request(url, data))

    def post_form(self, document, submit_suffix, overrides=None):
        url, data = form_request(document, submit_suffix, overrides)
        return self.document(url, data)

    def open_profile_bids(self):
        # La primera visita crea la sesión de WebSphere necesaria para el deeplink.
        self.request(PROFILE_LIST_URL)
        profile = self.document(PROFILE_URL)
        return self.post_form(profile, "linkPrepLic")

    def works(self, max_pages=100):
        page = self.open_profile_bids()
        page = self.post_form(page, "busReasProc18", {"busReasProc07": WORKS_CODE})
        all_rows: dict[str, dict[str, object]] = {}
        for number in range(1, max_pages + 1):
            rows = parse_listing(page)
            if not rows:
                raise RuntimeError(f"La página {number} no contiene licitaciones")
            all_rows.update({str(row["expediente"]): row for row in rows})
            print(f"Perfil Obras, página {number}: {len(rows)} expedientes", flush=True)
            if not has_submit(page, "siguienteLink"):
                break
            page = self.post_form(page, "siguienteLink")
        else:
            raise RuntimeError("Se alcanzó el límite de páginas del perfil")
        return list(all_rows.values())

    def enrich_xml(self, row: dict[str, object]) -> dict[str, object]:
        detail = self.document(str(row["enlace"]))
        source = latest_xml_link(detail)
        if not source:
            row["datosOpenPLACSP"] = None
            row["resultados"] = []
            row["avisoXML"] = "El expediente no publica un documento XML"
            return row
        payload = self.request(source["url"], accept="application/xml,text/xml,*/*")
        if b"<html" in payload[:1000].lower():
            intermediate = html.fromstring(payload)
            refresh = intermediate.xpath(
                '//meta[translate(@http-equiv,"REFSH","refsh")="refresh"]/@content'
            )
            if refresh and "url=" in refresh[0].lower():
                next_url = refresh[0].split("=", 1)[1].strip(" '\"")
                payload = self.request(
                    urllib.parse.urljoin(source["url"], next_url),
                    accept="application/xml,text/xml,*/*",
                )
        root = ET.fromstring(payload)
        row["datosOpenPLACSP"] = {xml_name(root.tag): xml_to_data(root)}
        row["resultados"] = result_rows_from_xml(root, str(row["estado"]))
        row["xmlFuente"] = source
        return row


def load_previous() -> dict[str, dict[str, object]]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return {str(row["expediente"]): row for row in payload.get("data", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def unchanged(row, previous):
    keys = ("tipo", "objeto", "estado", "importe", "fechas", "enlace")
    return (
        previous
        and previous.get("datosOpenPLACSP") is not None
        and "resultados" in previous
        and all(row.get(key) == previous.get(key) for key in keys)
    )


def enrich_in_new_session(row: dict[str, object]) -> dict[str, object]:
    """Cada hilo usa su propia sesión/cookies para no mezclar estados JSF."""
    client = PortalClient()
    client.request(PROFILE_LIST_URL)
    try:
        return client.enrich_xml(row)
    except Exception as error:  # Un XML ausente no debe borrar la fila visible.
        row["datosOpenPLACSP"] = None
        row["resultados"] = []
        row["avisoXML"] = str(error)
        return row


def select_rows(rows, start_year=START_YEAR):
    return [
        row
        for row in rows
        if (expediente_year(str(row.get("expediente", ""))) or 0) >= start_year
        and str(row.get("tipoCodigo")) == WORKS_CODE
        and matches_acronym(str(row.get("objeto", "")))
    ]


def write_json(payload: dict[str, object]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, OUTPUT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=START_YEAR)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--skip-xml", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    client = PortalClient()
    works = client.works(args.max_pages)
    selected = select_rows(works, args.start_year)
    previous = load_previous()
    enriched: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []
    for row in selected:
        old = previous.get(str(row["expediente"]))
        if unchanged(row, old):
            row.update(
                {key: value for key, value in old.items() if key in {"datosOpenPLACSP", "xmlFuente", "resultados"}}
            )
            enriched.append(row)
        elif args.skip_xml:
            row["datosOpenPLACSP"] = None
            row["resultados"] = []
            enriched.append(row)
        else:
            pending.append(row)

    if pending:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(enrich_in_new_session, row): row for row in pending}
            for number, future in enumerate(as_completed(futures), 1):
                row = future.result()
                enriched.append(row)
                print(
                    f"XML {number}/{len(pending)}: {row['expediente']}",
                    flush=True,
                )
    print(f"Seleccionadas: {len(selected)} licitaciones TC", flush=True)

    enriched.sort(key=lambda row: str(row["expediente"]), reverse=True)
    output_rows = flatten_result_rows(enriched)
    output_rows.sort(
        key=lambda row: (str(row["expediente"]), str(row.get("lote") or "")),
        reverse=True,
    )
    generated = utc_now()
    write_json(
        {
            "schemaVersion": 4,
            "data": output_rows,
            "generatedAt": generated,
            "sourceUpdated": generated,
            "source": PROFILE_URL,
            "filters": {
                "organ": TARGET_ORGAN,
                "organId": PROFILE_ID,
                "tipo": "Obras",
                "tipoCodigo": WORKS_CODE,
                "objetoContieneAcronimo": TARGET_ACRONYM,
                "desdeAnio": args.start_year,
            },
            "count": len(output_rows),
            "expedientesCount": len(enriched),
            "rowModel": "una fila por resultado y lote",
            "profileWorksScanned": len(works),
            "mode": "perfil-contratante",
            "preservaDatosOpenPLACSP": True,
        }
    )
    print(
        f"JSON generado: {len(enriched)} expedientes / {len(output_rows)} filas",
        flush=True,
    )


if __name__ == "__main__":
    main()
