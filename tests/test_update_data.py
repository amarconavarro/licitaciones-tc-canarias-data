import unittest

from lxml import html

from scripts.update_data import (
    form_request,
    latest_xml_link,
    matches_acronym,
    parse_amount,
    parse_listing,
    select_rows,
    xml_to_data,
    unchanged,
    flatten_result_rows,
    result_rows_from_xml,
    update_change_history,
)
from xml.etree import ElementTree as ET


FORM = '''<html><form action="/buscar">
<input type="hidden" name="javax.faces.ViewState" value="abc">
<select name="ns:form:busReasProc07"><option value="00" selected>Todos</option><option value="3">Obras</option></select>
<textarea name="ns:form:busReasProc17"></textarea>
<input type="submit" name="ns:form:busReasProc18" value="Buscar">
</form></html>'''

LISTING = '''<html><table summary="Tabla de Licitaciones del Perfil del Contratante">
<tr><th>Expediente</th><th>Tipo</th><th>Objeto</th><th>Estado</th><th>Importe</th><th>Fechas</th></tr>
<tr><td><a href="/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=x">2026/ETSAE0814/00002179E</a></td>
<td>Obras</td><td>Acto Hoya Fría_TC:507</td><td>Evaluación</td><td>329.999,10 EUR</td><td>Present. Oferta: 29/07/2026</td></tr>
</table></html>'''


class UpdateDataTests(unittest.TestCase):
    @staticmethod
    def change_row(**overrides):
        row = {
            "expediente": "2026/ETSAE0814/00000001",
            "tipo": "Obras",
            "objeto": "Reforma TC 507-01/26",
            "estado": "Evaluación",
            "importe": 100000.0,
            "fechas": "Present. Oferta:01/09/2026",
            "resultados": [],
        }
        row.update(overrides)
        return row

    def test_acronym_is_a_complete_token(self):
        self.assertTrue(matches_acronym("Hoya Fría_TC:507"))
        self.assertTrue(matches_acronym("TC 507-20/25"))
        self.assertFalse(matches_acronym("CONTRATO"))

    def test_amount_accepts_spanish_and_english_formats(self):
        self.assertEqual(parse_amount("329.999,10 EUR"), 329999.10)
        self.assertEqual(parse_amount("329,999.10"), 329999.10)

    def test_jsf_form_keeps_viewstate_and_overrides_type(self):
        url, body = form_request(html.fromstring(FORM), "busReasProc18", {"busReasProc07": "3"})
        decoded = body.decode()
        self.assertEqual(url, "https://contrataciondelestado.es/buscar")
        self.assertIn("javax.faces.ViewState=abc", decoded)
        self.assertIn("busReasProc07=3", decoded)
        self.assertIn("busReasProc18=Buscar", decoded)

    def test_extracts_six_visible_columns(self):
        row = parse_listing(html.fromstring(LISTING))[0]
        self.assertEqual(row["expediente"], "2026/ETSAE0814/00002179E")
        self.assertEqual(row["tipo"], "Obras")
        self.assertEqual(row["estado"], "Evaluación")
        self.assertEqual(row["importe"], 329999.10)
        self.assertEqual(row["fechas"], "Present. Oferta: 29/07/2026")

    def test_filters_by_year_works_and_tc_or_amount_over_40000(self):
        row = parse_listing(html.fromstring(LISTING))[0]
        old = dict(row, expediente="2024/OLD")
        service = dict(row, expediente="2026/S", tipoCodigo="2")
        no_tc_high = dict(
            row, expediente="2026/N-HIGH", objeto="Reforma general", importe=40000.01
        )
        no_tc_exact = dict(
            row, expediente="2026/N-EXACT", objeto="Reforma general", importe=40000.0
        )
        no_tc_low = dict(
            row, expediente="2026/N-LOW", objeto="Reforma general", importe=39999.99
        )
        tc_low = dict(row, expediente="2026/TC-LOW", importe=1000.0)
        self.assertEqual(
            select_rows([row, old, service, no_tc_high, no_tc_exact, no_tc_low, tc_low]),
            [row, no_tc_high, tc_low],
        )

    def test_selects_newest_xml_document(self):
        detail = html.fromstring('''<table>
          <tr><td>01/01/2026 Anuncio</td><td><a href="/old"><img alt="Documento xml"></a></td></tr>
          <tr><td>05/08/2026 Adjudicación</td><td><a href="/new"><img alt="Documento xml"></a></td></tr>
        </table>''')
        self.assertTrue(latest_xml_link(detail)["url"].endswith("/new"))

    def test_preserves_all_xml_fields_attributes_and_repetitions(self):
        root = ET.fromstring('<r id="1"><x currencyID="EUR">10</x><x>20</x><extra>dato</extra></r>')
        data = xml_to_data(root)
        self.assertEqual(data["@id"], "1")
        self.assertEqual(data["x"][0]["@currencyID"], "EUR")
        self.assertEqual(data["x"][1], "20")
        self.assertEqual(data["extra"], "dato")


    def test_extracts_one_row_per_lot_and_winning_party(self):
        root = ET.fromstring(
            """<ContractFolderStatus>
                <TenderResult>
                  <ResultCode name="Formalizado">9</ResultCode>
                  <AwardDate>2026-08-01</AwardDate>
                  <ReceivedTenderQuantity>4</ReceivedTenderQuantity>
                  <WinningParty><PartyName>UTE Alfa Beta</PartyName><CompanyID>U12345678</CompanyID></WinningParty>
                  <AwardedTenderedProject>
                    <ProcurementProjectLot><ID>1</ID></ProcurementProjectLot>
                    <LegalMonetaryTotal>
                      <TaxExclusiveAmount>100.00</TaxExclusiveAmount>
                      <PayableAmount>107.00</PayableAmount>
                    </LegalMonetaryTotal>
                  </AwardedTenderedProject>
                  <Contract><IssueDate>2026-08-06</IssueDate><StartDate>2026-08-08</StartDate></Contract>
                </TenderResult>
                <TenderResult>
                  <ResultCode>Adjudicado</ResultCode>
                  <WinningParty><PartyName>Constructora Gamma</PartyName><CompanyID>B87654321</CompanyID></WinningParty>
                  <AwardedTenderedProject><ProcurementProjectLot><ID>2</ID></ProcurementProjectLot></AwardedTenderedProject>
                </TenderResult>
            </ContractFolderStatus>"""
        )
        results = result_rows_from_xml(root, "Resuelta")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["lote"], "1")
        self.assertEqual(results[0]["resultado"], "Formalizado")
        self.assertEqual(results[0]["adjudicatario"], "UTE Alfa Beta")
        self.assertEqual(results[0]["nifAdjudicatario"], "U12345678")
        self.assertEqual(results[0]["importeAdjudicacionSinIVA"], 100.0)
        self.assertEqual(results[0]["importeAdjudicacionConIVA"], 107.0)
        self.assertEqual(results[0]["fechaAcuerdoAdjudicacion"], "2026-08-01")
        self.assertEqual(results[0]["fechaFormalizacion"], "2026-08-06")
        self.assertEqual(results[0]["fechaEntradaVigor"], "2026-08-08")
        self.assertEqual(results[0]["numeroOfertasRecibidas"], 4.0)
        rows = flatten_result_rows([{"expediente": "2026/TC/1", "resultados": results}])
        self.assertEqual([row["lote"] for row in rows], ["1", "2"])

    def test_invalidates_cache_created_before_results_schema(self):
        keys = ("tipo", "objeto", "estado", "importe", "fechas", "enlace")
        current = {key: "igual" for key in keys}
        previous = dict(current, datosOpenPLACSP={"xml": "conservado"})
        self.assertFalse(unchanged(current, previous))
        previous["resultados"] = []
        self.assertTrue(unchanged(current, previous))

    def test_marks_a_new_tender_without_marking_existing_baseline_rows(self):
        detected_at = "2026-09-01T06:23:00Z"
        new_row = self.change_row()
        update_change_history(new_row, None, detected_at)
        self.assertEqual(new_row["historialCambios"][0]["tipo"], "nuevo")

        existing = self.change_row()
        update_change_history(existing, self.change_row(), detected_at)
        self.assertEqual(existing["historialCambios"], [])

    def test_records_previous_and_new_values_for_visible_changes(self):
        previous = self.change_row(
            estado="Evaluación",
            fechas="Present. Oferta:01/09/2026",
        )
        current = self.change_row(
            estado="Resuelta",
            fechas="Publicación PLACSP:Adjudicación:02/09/2026",
        )
        update_change_history(current, previous, "2026-09-02T16:23:00Z")
        event = current["historialCambios"][0]
        self.assertEqual(event["tipo"], "modificado")
        self.assertEqual(
            event["cambios"],
            [
                {"campo": "Estado", "anterior": "Evaluación", "nuevo": "Resuelta"},
                {
                    "campo": "Fechas",
                    "anterior": "Present. Oferta:01/09/2026",
                    "nuevo": "Publicación PLACSP:Adjudicación:02/09/2026",
                },
            ],
        )

    def test_result_comparison_is_stable_and_detects_the_changed_lot(self):
        first = {"idResultado": "1-1", "lote": "1", "resultado": "Adjudicado", "adjudicatario": "Alfa"}
        second = {"idResultado": "2-1", "lote": "2", "resultado": "Adjudicado", "adjudicatario": "Beta"}
        previous = self.change_row(resultados=[second, first])
        same = self.change_row(resultados=[first, second])
        update_change_history(same, previous, "2026-09-01T06:23:00Z")
        self.assertEqual(same["historialCambios"], [])

        changed_second = dict(second, resultado="Formalizado")
        current = self.change_row(resultados=[first, changed_second])
        update_change_history(current, previous, "2026-09-01T16:23:00Z")
        changes = current["historialCambios"][0]["cambios"]
        self.assertEqual(
            changes,
            [{
                "campo": "Resultado",
                "anterior": "Lote 1: Adjudicado | Lote 2: Adjudicado",
                "nuevo": "Lote 1: Adjudicado | Lote 2: Formalizado",
            }],
        )

    def test_keeps_only_change_events_from_the_last_seven_days(self):
        previous = self.change_row(
            historialCambios=[
                {"tipo": "nuevo", "detectadoEn": "2026-08-20T06:23:00Z", "cambios": []},
                {"tipo": "modificado", "detectadoEn": "2026-08-30T06:23:00Z", "cambios": []},
            ]
        )
        current = self.change_row()
        update_change_history(current, previous, "2026-09-01T06:23:00Z")
        self.assertEqual(len(current["historialCambios"]), 1)
        self.assertEqual(
            current["historialCambios"][0]["detectadoEn"],
            "2026-08-30T06:23:00Z",
        )


if __name__ == "__main__":
    unittest.main()
