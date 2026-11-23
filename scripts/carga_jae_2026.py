"""Carga inicial aislada: un fragmento por mes y validación final sin alterar la base."""
import collections, datetime as dt, json, os, re, subprocess, sys, tempfile, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ORG = "jefatura de asuntos económicos del mando de canarias"
ROOT = "https://contrataciondelsectorpublico.gob.es/sindicacion/"
FEEDS = ["sindicacion_643/licitacionesPerfilesContratanteCompleto3", "sindicacion_1143/contratosMenoresPerfilesContratantes"]
TYPES = {"1":"Suministros","2":"Servicios","3":"Obras"}
STATE = {"PUB":"Publicada","EV":"Evaluación","ADJ":"Adjudicada","RES":"Resuelta","ANUL":"Anulada","PRE":"Anuncio previo"}
def t(e,path):
    return (e.findtext(path) or "").strip()
def stamp(s):
    d=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
    if not d.tzinfo: raise ValueError("Fecha ATOM sin zona: "+s)
    return d.astimezone(dt.timezone.utc).isoformat()
def put(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=1),encoding="utf-8")
def parse(e,minor):
    s=e.find("{*}ContractFolderStatus")
    if s is None: return
    org=t(s,"{*}LocatedContractingParty/{*}Party/{*}PartyName/{*}Name")
    if " ".join(org.casefold().split())!=ORG: return
    exp=t(s,"{*}ContractFolderID")
    if not exp.startswith("2026/"): return
    p=s.find("{*}ProcurementProject")
    if p is None: raise ValueError("Sin proyecto: "+exp)
    typ=t(p,"{*}TypeCode"); state=t(s,"{*}ContractFolderStatusCode")
    amt=t(p,"{*}BudgetAmount/{*}TaxExclusiveAmount")
    lots={t(l,"{*}ID"):t(l,"{*}ProcurementProject/{*}Name") for l in s.findall("{*}ProcurementProjectLot") if t(l,"{*}ID")}
    result=[]
    for tr in s.findall("{*}TenderResult"):
        lot=t(tr,".//{*}ProcurementProjectLotID") or t(tr,".//{*}ProcurementProjectLot/{*}ID") or None
        code=t(tr,"{*}ResultCode"); el=tr.find("{*}ResultCode")
        label=el.get("name") if el is not None else None
        for party in tr.findall("{*}WinningParty") or [None]:
            result.append(dict(lote=lot,resultadoCodigo=code,resultado=label or {"8":"Adjudicado","9":"Formalizado","3":"Desierto","4":"Desistimiento"}.get(code,code or None),adjudicatario=t(party,"{*}PartyName/{*}Name") if party is not None else None,fechaFormalizacion=t(tr,"{*}Contract/{*}IssueDate") or None,fechaAcuerdoAdjudicacion=t(tr,"{*}AwardDate") or None,importeAdjudicacionSinIVA=t(tr,".//{*}LegalMonetaryTotal/{*}TaxExclusiveAmount") or None))
    for lot in lots.keys()-{r["lote"] for r in result}: result.append(dict(lote=lot))
    link=e.find("{*}link[@rel='alternate']")
    if link is None: link=e.find("{*}link")
    return dict(expediente=exp,atomId=t(e,"{*}id"),actualizado=stamp(t(e,"{*}updated")),tipoCodigo=typ,tipo=TYPES.get(typ,"Otro ("+typ+")"),objeto=t(p,"{*}Name"),importe=float(amt) if amt else None,estadoCodigo=state,estado=STATE.get(state,state),menor=minor,enlace=link.get("href") if link is not None else None,lotes=lots,resultados=result)
def month(m):
    out=[]; audit=[]; deleted={}
    with tempfile.TemporaryDirectory() as tmp:
        for i,feed in enumerate(FEEDS):
            path=Path(tmp)/"mes.zip"
            subprocess.run(["curl","-fsSL","--retry","2","--max-time","900","--max-filesize","2500000000","-o",str(path),ROOT+feed+"_2026"+m+".zip"],check=True)
            n=0; chosen=0
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    if not member.lower().endswith(".atom"): continue
                    with z.open(member) as stream:
                        for _,e in ET.iterparse(stream,events=("end",)):
                            if e.tag=="{http://www.w3.org/2005/Atom}entry":
                                n+=1; row=parse(e,bool(i))
                                if row: out.append(row); chosen+=1
                                e.clear()
                            elif e.tag.endswith("}deleted-entry"):
                                ref=e.get("ref"); when=e.get("when")
                                if ref and when: deleted[ref]=max(deleted.get(ref,""),stamp(when))
                                e.clear()
            if not n: raise ValueError("ZIP sin entradas: "+feed+" "+m)
            audit.append(dict(mes=m,fuente=feed,bytes=path.stat().st_size,entradas=n,seleccionadas=chosen))
            path.unlink()
    put(Path("fragmentos")/(m+".json"),dict(audit=audit,data=out,deleted=deleted))
def merge():
    docs=[json.loads((Path("fragmentos")/("%02d.json"%m)).read_text()) for m in range(1,12)]
    audit=[a for d in docs for a in d["audit"]]
    assert len({(a["mes"],a["fuente"]) for a in audit})==22
    deleted={}; latest={}
    for d in docs:
        for ref,tm in d["deleted"].items(): deleted[ref]=max(deleted.get(ref,""),tm)
    for d in docs:
        for r in d["data"]:
            if deleted.get(r["atomId"],"")>=r["actualizado"]: continue
            k=r["expediente"]
            if k not in latest or latest[k]["actualizado"]<r["actualizado"]: latest[k]=r
    rows=[]
    for k,r in sorted(latest.items(),reverse=True):
        for i,s in enumerate(r["resultados"] or [{}]):
            rows.append({**{a:b for a,b in r.items() if a not in ("resultados","lotes")},**s,"objetoLote":r["lotes"].get(s.get("lote")),"id":k+"#"+str(i)})
    if not rows: raise ValueError("Resultado vacío")
    baseline=json.loads(Path("data/licitaciones-tc.json").read_text())["data"]
    expected={r["expediente"] for r in baseline if r["expediente"].startswith("2026/")}
    cobra={k for k,r in latest.items() if r["tipo"]=="Obras" and (re.search(r"(?<![^\W_])TC(?![^\W_])",r["objeto"],re.I) or (r["importe"] or 0)>40000)}
    report=dict(archivos=len(audit),expedientes=len(latest),filas=len(rows),porTipo=dict(collections.Counter(r["tipo"] for r in latest.values())),menores=sum(r["menor"] for r in latest.values()),obrasCOBRA=len(cobra),respaldo2026=len(expected),faltanEnCOBRA=sorted(expected-cobra),adicionalesCOBRA=sorted(cobra-expected),fuentes=audit)
    put(Path("salida/jae-2026.json"),dict(anioExpediente=2026,mesesHasta=11,data=rows))
    put(Path("salida/validacion.json"),report)
    print(json.dumps({k:v for k,v in report.items() if k!="fuentes"},ensure_ascii=False,indent=2))
if __name__=="__main__":
    if sys.argv[1]=="merge": merge()
    else: month(sys.argv[1])
