"""Ensayo de conciliación, sin persistencia ni cambios en el JSON del Site."""
import collections
import copy
import datetime as dt
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as E
from decimal import Decimal
from pathlib import Path

baseline = json.loads(Path("data/licitaciones-tc.json").read_bytes())
def instant(v):
    d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Fecha sin zona: " + v)
    return d
cut = instant(baseline["generatedAt"])
assert cut.date().isoformat() == "2026-11-17", cut
start = instant("2026-11-16T00:00:00Z")
old = collections.defaultdict(list)
for row in baseline["data"]:
    old[row["expediente"]].append(row)
def t(node, path):
    return (node.findtext(path) or "").strip()
def norm(s):
    return " ".join(str(s or "").casefold().split())
def sortedrows(values):
    return sorted(tuple(norm(x) for x in v) for v in values)
latest, historical = {}, {}
for fn in sys.argv[1:]:
    n = hits = 0
    top = None
    with zipfile.ZipFile(fn) as z:
        for name in z.namelist():
            if not name.lower().endswith(".atom"):
                continue
            with z.open(name) as f:
                for _, e in E.iterparse(f, events=("end",)):
                    if e.tag != "{http://www.w3.org/2005/Atom}entry":
                        continue
                    n += 1
                    epoch = instant(t(e, "{*}updated"))
                    top = max(top or epoch, epoch)
                    s = e.find("{*}ContractFolderStatus")
                    if s is not None and norm(t(s,"{*}LocatedContractingParty/{*}Party/{*}PartyName/{*}Name")) == norm("Jefatura de Asuntos Económicos del Mando de Canarias"):
                        hits += 1
                        p = s.find("{*}ProcurementProject")
                        exp = t(s,"{*}ContractFolderID")
                        if p is not None and t(p,"{*}TypeCode") == "3" and re.match(r"202[56]/",exp):
                            title = t(p,"{*}Name")
                            amount = t(p,"{*}BudgetAmount/{*}TaxExclusiveAmount")
                            if re.search(r"(?<![^\W_])TC(?![^\W_])",title,re.I) or Decimal(amount or "0") > 40000:
                                outcomes = []
                                for res in s.findall("{*}TenderResult"):
                                    lot = t(res,".//{*}ProcurementProjectLotID") or t(res,".//{*}ProcurementProjectLot/{*}ID")
                                    parties = [t(x,"{*}PartyName/{*}Name") for x in res.findall("{*}WinningParty")] or [""]
                                    formal = t(res,"{*}Contract/{*}IssueDate") or t(res,"{*}Contract/{*}FormalizationDate")
                                    for party in parties:
                                        outcomes.append((lot,party,formal,t(res,"{*}ResultCode")))
                                v = (epoch,title,amount,t(s,"{*}ContractFolderStatusCode"),outcomes)
                                if epoch >= start and (exp not in latest or epoch > latest[exp][0]):
                                    latest[exp] = v
                                if epoch <= cut and exp in old and (exp not in historical or epoch > historical[exp][0]):
                                    historical[exp] = v
                    e.clear()
    if not n:
        raise ValueError("Sin entradas en " + fn)
    print(Path(fn).name, "entradas:",n,"órgano:",hits,"última publicación nacional:",top, flush=True)
    if top < cut:
        raise ValueError("Fuente sin cobertura posterior al respaldo")
candidate = copy.deepcopy(dict(old))
for exp,(stamp,title,amount,state,outcomes) in sorted(latest.items()):
    print("Solape" if stamp <= cut else "Posterior",exp,stamp,"estado ATOM:",state,"estado base:",old.get(exp,[{}])[0].get("estado"),"resultados:",outcomes)
    if stamp > cut:
        # Candidato parcial; no generar formato de producción hasta validar mapeo.
        candidate[exp] = {"objeto":title,"importe":amount,"estadoATOM":state,"resultadosATOM":outcomes}
print("Expedientes base:",len(old),"candidato:",len(candidate),"altas:",sorted(candidate.keys()-old.keys()),"base idéntica:",candidate==dict(old))
verified = 0
for exp, (_, _, _, _, outcomes) in sorted(historical.items()):
    actual = sortedrows([r[:3] for r in outcomes])
    expected = sortedrows([(r.get("lote"),r.get("adjudicatario"),r.get("fechaFormalizacion")) for r in old[exp]])
    if any(r[2] for r in actual+expected):
        print("Contraste histórico lote/adjudicatario/formalización",exp,"ATOM:", actual,"JSON:", expected, "coincide:",actual==expected)
        verified += actual==expected
print("Formalizaciones históricas coincidentes:",verified,"(si es cero, pendiente de validar; no promover)")
