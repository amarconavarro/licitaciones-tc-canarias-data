# Licitaciones TC Canarias

Genera diariamente un JSON con las licitaciones que cumplen estos criterios:

- Órgano de contratación: **Jefatura de Asuntos Económicos del Mando de Canarias**.
- Tipo de contrato: **Obras**.
- El objeto contiene el acrónimo completo **TC**.

La fuente es el conjunto oficial de datos abiertos de la Plataforma de Contratación del Sector Público, sindicación 643, en formato Atom/XML CODICE. No se utilizan capturas de pantalla ni OCR.

## Archivos

- `scripts/update_data.py`: descarga, interpreta y consolida los datos.
- `data/state.json`: estado interno más reciente de las licitaciones del órgano; lo genera la primera ejecución.
- `data/licitaciones-tc.json`: salida pública consumible por la web y por Excel.
- `.github/workflows/update-data.yml`: actualización automática diaria y ejecución manual.

## Funcionamiento

La primera ejecución procesa los archivos anuales oficiales desde 2025. Después se conserva el estado más reciente de cada identificador único de licitación. Las ejecuciones diarias leen el feed incremental y solo recorren páginas posteriores a la última marca temporal guardada.

Cuando un expediente cambia de estado, la versión nueva sustituye a la anterior. Si deja de cumplir los filtros, desaparece del JSON público.

## Datos conservados

Cada fila de `data/licitaciones-tc.json` mantiene los campos normalizados que usan la web y Excel —expediente, tipo, objeto, estado, importe y fechas— junto con otros campos prácticos como los códigos, el órgano, su identificador y el enlace oficial.

Además, `datosOpenPLACSP` conserva recursivamente todo el contenido del último registro XML oficial del expediente: textos, atributos y elementos repetidos. De este modo no se descartan campos que puedan resultar útiles más adelante y Power Query puede expandir o filtrar ese bloque sin modificar el scraper.

## Ejecución local

Requiere Python 3.11 o posterior y no utiliza dependencias externas.

```bash
python -m unittest discover -s tests -v
python scripts/update_data.py --backfill
python scripts/update_data.py
```

## Fuente oficial

- [Catálogo de licitaciones publicadas en PLACSP](https://www.hacienda.gob.es/es-es/gobiernoabierto/datos%20abiertos/paginas/licitacionescontratante.aspx)
- [Manual de OpenPLACSP](https://contrataciondelestado.es/datosabiertos/DGPE_PLACSP_OpenPLACSP_v.2.2.pdf)
