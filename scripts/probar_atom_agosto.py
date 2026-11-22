"""Prueba de lectura: no escribe en el repositorio."""
import json
import re
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

zip_path = Path(sys.argv[1])
start = time.monotonic()
sha = subprocess.check_output(["git", "rev-list", "-1", "--before=2026-09-01T00:00:00Z", "HEAD", "--", "data/licitaciones-tc.json"], text=True).strip()
old = json.loads(subprocess.check_output(["git", "show", f"{sha}:data/licitaciones-tc.json"]))
reference = {r["expediente"]: r for r in old["data"]}
wanted = {"2026/ETSAE0814/00002696", "2026/ETSAE0814/00001725E"}
rows = {}
entries = 0
def field(e, path):
    return (e.findtext(path) or "").strip()
with ZipFile(zip_path) as z:
    for member in z.namelist():
        if not member.lower().endswith(".atom"):
            continue
        with z.open(member) as f:
            for _, entry in ET.iterparse(f, events=("end",)):
                if entry.tag != "{http://www.w3.org/2005/Atom}entry":
                    continue
                entries += 1
                s = entry.find("{*}ContractFolderStatus")
                if s is not None:
                    organ = field(s, "{*}LocatedContractingParty/{*}Party/{*}PartyName/{*}Name")
                    exp = field(s, "{*}ContractFolderID")
                    p = s.find("{*}ProcurementProject")
                    if organ.casefold() == "jefatura de asuntos económicos del mando de canarias" and p is not None and field(p, "{*}TypeCode") == "3" and exp[:4] in ("2025","2026"):
                        title = field(p, "{*}Name")
                        amount = field(p, "{*}BudgetAmount/{*}TaxExclusiveAmount")
                        tc = bool(re.search(r"(?<![^\W_])TC(?![^\W_])", title, re.I))
                        stamp = field(entry, "{*}updated")
                        if exp not in rows or stamp > rows[exp][0]:
                            rows[exp] = (stamp, tc, amount, field(s,"{*}ContractFolderStatusCode"), title)
                entry.clear()
selected = {k:v for k,v in rows.items() if v[1] or Decimal(v[2] or "0") > 40000}
print("ZIP MB:", round(zip_path.stat().st_size/1e6,1), "Segundos de análisis:", round(time.monotonic()-start), "Entradas:",entries,"Expedientes filtrados:",len(selected),"Referencia:",sha)
for k in sorted(wanted):
    print(k, "ATOM (fecha, TC, base sin impuestos, estado, objeto):", rows.get(k), "JSON agosto:", {f:reference.get(k,{}).get(f) for f in ("importe","estado","objeto")})
if not wanted <= selected.keys() or not wanted <= reference.keys():
    raise SystemExit("Falta algún expediente de control: revisar antes de migrar.")
