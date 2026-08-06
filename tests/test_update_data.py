import io
import unittest

from scripts.update_data import TARGET_ORGAN, matches_acronym, process_atom, public_rows


def feed(objeto="Acto Hoya Fría TC 507-20/25", updated="2026-07-30T10:00:00+02:00", tipo="3", estado="EV"):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:cbc="urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2"
 xmlns:cac="urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2"
 xmlns:cac-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2"
 xmlns:at="http://purl.org/atompub/tombstones/1.0">
 <updated>{updated}</updated>
 <entry>
  <id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/123</id>
  <link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=test"/>
  <title>{objeto}</title><updated>{updated}</updated>
  <cac-place-ext:ContractFolderStatus>
   <cbc:ContractFolderID>2026/ETSAE0814/00002179E</cbc:ContractFolderID>
   <cbc-place-ext:ContractFolderStatusCode>{estado}</cbc-place-ext:ContractFolderStatusCode>
   <cac-place-ext:LocatedContractingParty><cac:Party><cac:PartyName><cbc:Name>{TARGET_ORGAN}</cbc:Name></cac:PartyName></cac:Party></cac-place-ext:LocatedContractingParty>
   <cac:ProcurementProject><cbc:Name>{objeto}</cbc:Name><cbc:TypeCode>{tipo}</cbc:TypeCode><cac:BudgetAmount><cbc:EstimatedOverallContractAmount>329999.10</cbc:EstimatedOverallContractAmount></cac:BudgetAmount></cac:ProcurementProject>
   <cac:TenderingProcess><cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>2026-07-29</cbc:EndDate></cac:TenderSubmissionDeadlinePeriod></cac:TenderingProcess>
  </cac-place-ext:ContractFolderStatus>
 </entry>
</feed>'''.encode()


def deleted_feed(when="2026-08-02T10:00:00+02:00"):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:at="http://purl.org/atompub/tombstones/1.0">
 <updated>{when}</updated>
 <at:deleted-entry when="{when}" ref="https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/123"/>
</feed>'''.encode()


class UpdateDataTests(unittest.TestCase):
    def test_acronym_is_a_complete_token(self):
        self.assertTrue(matches_acronym("Hoya Fría_TC:507"))
        self.assertFalse(matches_acronym("CONTRATO"))

    def test_extracts_expected_fields(self):
        records = {}
        process_atom(io.BytesIO(feed()), records)
        record = records[next(iter(records))]
        self.assertEqual(record["expediente"], "2026/ETSAE0814/00002179E")
        self.assertEqual(record["tipo"], "Obras")
        self.assertEqual(record["estado"], "Evaluación")
        self.assertEqual(record["importe"], 329999.10)
        self.assertEqual(record["fechaLimite"], "2026-07-29")

    def test_newer_update_replaces_state(self):
        records = {}
        process_atom(io.BytesIO(feed()), records)
        process_atom(io.BytesIO(feed(updated="2026-08-01T10:00:00+02:00", estado="RES")), records)
        self.assertEqual(records[next(iter(records))]["estado"], "Resuelta")

    def test_older_update_does_not_replace_state(self):
        records = {}
        process_atom(io.BytesIO(feed(updated="2026-08-01T10:00:00+02:00", estado="RES")), records)
        process_atom(io.BytesIO(feed(updated="2026-07-01T10:00:00+02:00", estado="EV")), records)
        self.assertEqual(records[next(iter(records))]["estado"], "Resuelta")

    def test_deleted_tender_is_not_resurrected_by_an_older_entry(self):
        records = {}
        process_atom(io.BytesIO(feed()), records)
        process_atom(io.BytesIO(deleted_feed()), records)
        process_atom(io.BytesIO(feed(updated="2026-07-01T10:00:00+02:00")), records)
        self.assertEqual(public_rows(records.values()), [])

    def test_non_works_and_non_tc_are_excluded_from_public_json(self):
        records = {}
        process_atom(io.BytesIO(feed(tipo="2")), records)
        process_atom(io.BytesIO(feed(objeto="Reforma sin acrónimo", updated="2026-08-01T10:00:00+02:00")), records)
        self.assertEqual(public_rows(records.values()), [])


if __name__ == "__main__":
    unittest.main()
