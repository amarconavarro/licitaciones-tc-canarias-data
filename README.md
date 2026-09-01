# Licitaciones TC Canarias

Genera diariamente un JSON con las licitaciones que cumplen estos criterios:

- Perfil de contratante: **Jefatura de Asuntos Económicos del Mando de Canarias** (`idBp=gwHbdMZ49t4=`).
- Año del expediente: **2025 o posterior**.
- Tipo de contrato: **Obras**.
- El objeto contiene el acrónimo completo **TC**, o el importe de licitación es **superior a 40.000 €**.

El scraper abre exclusivamente ese perfil público de la Plataforma de Contratación del Sector Público, selecciona «Obras», recorre sus páginas y aplica localmente el filtro de texto o importe. Un importe de 40.000,00 € exactos no entra por cuantía, aunque sí entraría si el objeto contiene TC. No descarga los ZIP nacionales, no usa capturas de pantalla y no utiliza OCR.

## Archivos

- `scripts/update_data.py`: consulta el perfil, pagina los resultados y genera el JSON.
- `data/licitaciones-tc.json`: salida consumible por la web, Power Query y Excel.
- `.github/workflows/update-data.yml`: actualización automática diaria y ejecución manual.

## Datos conservados

El JSON se entrega en formato tabular: una fila por resultado/lote. Si un expediente tiene varios lotes, cada lote sale como una fila distinta, repitiendo los datos generales del expediente. Las licitaciones sin resultado siguen apareciendo una sola vez con las columnas de resultado vacías.

Además de expediente, tipo, objeto, estado, importe, fechas y enlace, cada fila incluye directamente:

- `lote`
- `resultado`
- `adjudicatario`
- `nifAdjudicatario` (NIF o identificador de la UTE)
- `importeAdjudicacionSinIVA` y `importeAdjudicacionConIVA`
- `fechaAcuerdoAdjudicacion`, `fechaFormalizacion` y `fechaEntradaVigor`
- `numeroOfertasRecibidas`
- `historialCambios`: eventos detectados durante los últimos siete días. Distingue
  expedientes `nuevo` y `modificado`; estos últimos incluyen el campo y sus valores
  `anterior` y `nuevo` para las columnas visibles del Site.

Para los expedientes seleccionados también se descarga el documento XML más reciente publicado por PLACSP y se conserva completo y recursivamente en `datosOpenPLACSP` (textos, atributos, elementos repetidos y campos adicionales). En ejecuciones sucesivas se reutiliza ese XML si los campos visibles del expediente no han cambiado. Si un documento XML puntual falla, la licitación permanece en el JSON con un aviso en vez de perderse.

## Ejecución local

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update_data.py --start-year 2025
```

## Fuente oficial

- [Perfil de contratante](https://contrataciondelestado.es/wps/poc?uri=deeplink:perfilContratante&idBp=gwHbdMZ49t4%3D)
- [Datos abiertos de PLACSP](https://contrataciondelestado.es/wps/portal/plataforma/datos_abiertos)
