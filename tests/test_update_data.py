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


if __name__ == "__main__":
    unittest.main()
