# Licitaciones TC Canarias

Genera diariamente un JSON con las licitaciones que cumplen estos criterios:

- Perfil de contratante: **Jefatura de Asuntos Económicos del Mando de Canarias** (`idBp=gwHbdMZ49t4=`).
- Año del expediente: **2025 o posterior**.
- Tipo de contrato: **Obras**.
- El objeto contiene el acrónimo completo **TC**.

El scraper abre exclusivamente ese perfil público de la Plataforma de Contratación del Sector Público, selecciona «Obras», recorre sus páginas y aplica localmente el filtro de texto. No descarga los ZIP nacionales, no usa capturas de pantalla y no utiliza OCR.

## Archivos

- `scripts/update_data.py`: consulta el perfil, pagina los resultados y genera el JSON.
- `data/licitaciones-tc.json`: salida consumible por la web, Power Query y Excel.
- `.github/workflows/update-data.yml`: actualización automática diaria y ejecución manual.

## Datos conservados

Cada fila mantiene las seis columnas visibles —expediente, tipo, objeto, estado, importe y fechas—, el enlace oficial y los identificadores del perfil. Para los expedientes seleccionados también se descarga el documento XML más reciente publicado por PLACSP y se conserva completo y recursivamente en `datosOpenPLACSP` (textos, atributos, elementos repetidos y campos adicionales).

En ejecuciones sucesivas se reutiliza ese XML si los campos visibles del expediente no han cambiado. Si un documento XML puntual falla, la licitación permanece en el JSON con un aviso en vez de perderse.

## Ejecución local

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/update_data.py --start-year 2025
```

## Fuente oficial

- [Perfil de contratante](https://contrataciondelestado.es/wps/poc?uri=deeplink:perfilContratante&idBp=gwHbdMZ49t4%3D)
- [Datos abiertos de PLACSP](https://contrataciondelestado.es/wps/portal/plataforma/datos_abiertos)
