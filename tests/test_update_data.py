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
    flatten_result_rows,
    result_rows_from_xml,
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

    def test_filters_by_year_works_and_tc(self):
        row = parse_listing(html.fromstring(LISTING))[0]
        old = dict(row, expediente="2024/OLD")
        service = dict(row, expediente="2026/S", tipoCodigo="2")
        no_tc = dict(row, expediente="2026/N", objeto="Reforma general")
        self.assertEqual(select_rows([row, old, service, no_tc]), [row])

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
                  <ResultCode>Formalizado</ResultCode>
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


if __name__ == "__main__":
    unittest.main()
