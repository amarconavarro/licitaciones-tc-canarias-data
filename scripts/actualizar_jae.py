"""Actualiza el índice JAE con los archivos ATOM mensuales de PLACSP."""
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
    if not re.match(r"^\d{4}/", exp) or int(exp[:4]) not in YEARS: return
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

from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo
NOW=dt.datetime.now(dt.timezone.utc)
YEAR=NOW.astimezone(ZoneInfo("Atlantic/Canary")).year
YEARS={y for y in [YEAR-1,YEAR] if y>=2026}
def run():
    path=Path("data/jae.json")
    doc=json.loads(path.read_text())
    if not doc.get("data"): raise ValueError("Falta base validada")
    old=collections.defaultdict(list)
    for row in doc["data"]:
        if int(row["expediente"][:4]) in YEARS: old[row["expediente"]].append(row)
    cutoff=stamp((dt.datetime.fromisoformat(doc["cursor"])-dt.timedelta(days=1)).isoformat())
    updates={}; deleted={}; heads=[]
    # PLACSP packages the same ATOM entries by publication month.
    # Read each required month once, including the overlap at month boundaries.
    start=dt.date.fromisoformat(cutoff[:10]).replace(day=1)
    month=start
    newest={}
    with tempfile.TemporaryDirectory() as tmp:
        archive=Path(tmp)/"publicaciones.zip"
        while month<=NOW.date():
            for i,feed in enumerate(FEEDS):
                url=ROOT+feed+"_"+month.strftime("%Y%m")+".zip"
                print("Descargando",url,flush=True)
                subprocess.run(["curl","-fsSL","--retry","2","--connect-timeout","20",
                                "--max-time","360","--max-filesize","2500000000","-o",str(archive),url],check=True)
                count=0
                with zipfile.ZipFile(archive) as z:
                    for member in z.namelist():
                        if not member.lower().endswith(".atom"): continue
                        with z.open(member) as stream:
                            for _,e in ET.iterparse(stream,events=("end",)):
                                if e.tag=="{http://www.w3.org/2005/Atom}entry":
                                    at=stamp(t(e,"{*}updated"))
                                    count+=1
                                    if at>=cutoff:
                                        row=parse(e,bool(i))
                                        if row and at>updates.get(row["expediente"],{}).get("actualizado",""):
                                            updates[row["expediente"]]=row
                                elif e.tag.endswith("}deleted-entry"):
                                    at=stamp(e.attrib["when"])
                                    if at>=cutoff:
                                        ref=e.attrib["ref"]
                                        deleted[ref]=max(deleted.get(ref,""),at)
                                else:
                                    continue
                                newest[feed]=max(newest.get(feed,""),at)
                                e.clear()
                if not count: raise ValueError("ZIP sin entradas: "+url)
                print(count,"entradas procesadas",flush=True)
            month=(month.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
    heads=list(newest.values())
    for exp,r in updates.items():
        prev=old.get(exp,[])
        if prev and r["actualizado"]<=max(x["actualizado"] for x in prev): continue
        new=[{**{k:v for k,v in r.items() if k not in ("resultados","lotes")},**s,"objetoLote":r["lotes"].get(s.get("lote")),"id":exp+"#"+str(i)} for i,s in enumerate(r["resultados"] or [{}])]
        for x in new:
            last=next((p for p in prev if p.get("lote")==x.get("lote") and p.get("adjudicatario")==x.get("adjudicatario")),next((p for p in prev if p.get("lote")==x.get("lote")),{}))
            fields={"objeto":"Objeto","estado":"Estado","resultado":"Resultado","adjudicatario":"Adjudicatario","importe":"Importe","fechaFormalizacion":"Formalización","fechaAcuerdoAdjudicacion":"Adjudicación"}
            changes=[f"{label}: {last.get(k) or '—'} → {x.get(k) or '—'}" for k,label in fields.items() if last.get(k)!=x.get(k)]
            if not prev or changes: x["marca"]={"tipo":"Actualizado" if prev else "Nuevo","fecha":NOW.isoformat(),"detalle":"; ".join(changes) if prev else "Nuevo expediente"}
            elif last.get("marca"): x["marca"]=last["marca"]
        old[exp]=new
    data=[x for k in sorted(old,reverse=True) for x in old[k] if deleted.get(x["atomId"],"")<x["actualizado"]]
    if not data: raise ValueError("No se publica un conjunto vacío")
    cursor=max(stamp(doc["cursor"]),min(heads))
    if data!=doc["data"] or cursor!=doc["cursor"]:
        put(path,dict(data=data,cursor=cursor,generatedAt=NOW.isoformat()))
    print("Expedientes:",len({x["expediente"] for x in data}),"filas:",len(data))
if __name__=="__main__": run()
