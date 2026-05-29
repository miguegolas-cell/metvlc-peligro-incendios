from pathlib import Path
from datetime import datetime, timezone
import json
import re
import zipfile
import tempfile
import requests
import numpy as np
import rasterio
from PIL import Image


URL_AEMET = "https://www.aemet.es/es/api-eltiempo/incendios/download"

DOCS_DIR = Path("docs")
DATA_DIR = Path("data")

DOCS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

LAYERS_JSON = DOCS_DIR / "layers.json"
METADATA_JSON = DOCS_DIR / "metadata.json"


# Paleta oficial detectada en SLD/QML de AEMET
COLORS = {
    1: (75, 150, 227, 210),   # Muy bajo
    2: (81, 209, 246, 210),   # Bajo
    3: (87, 229, 32, 210),    # Moderado
    4: (249, 251, 47, 210),   # Alto
    5: (239, 133, 4, 210),    # Muy alto
    6: (245, 35, 0, 210),     # Extremo
}

LABELS = {
    1: "Muy bajo",
    2: "Bajo",
    3: "Moderado",
    4: "Alto",
    5: "Muy alto",
    6: "Extremo",
}


def is_tiff_file(path: Path) -> bool:
    """
    Detecta TIFF/GeoTIFF por cabecera binaria.
    TIFF little endian: II*
    TIFF big endian: MM*
    """
    if not path.exists() or path.stat().st_size < 4:
        return False

    header = path.read_bytes()[:4]
    return header in [b"II*\x00", b"MM\x00*"]


def download_file(url: str, out_path: Path) -> Path:
    headers = {
        "User-Agent": "MetVlc-Peligro-Incendios/1.0"
    }

    response = requests.get(url, headers=headers, timeout=120)
    response.raise_for_status()

    out_path.write_bytes(response.content)

    print("URL descargada:", url)
    print("Content-Type:", response.headers.get("content-type"))
    print("Tamaño descarga:", out_path.stat().st_size, "bytes")

    if out_path.stat().st_size < 1000:
        raise RuntimeError("La descarga es demasiado pequeña. Puede haber fallado.")

    return out_path


def extract_download(download_path: Path, extract_dir: Path):
    """
    AEMET puede devolver:
    - ZIP con TIF/QML/SLD
    - TIF directo
    - HTML de error
    """
    print("Comprobando tipo de archivo descargado...")

    if zipfile.is_zipfile(download_path):
        print("Detectado ZIP. Extrayendo...")
        with zipfile.ZipFile(download_path, "r") as z:
            z.extractall(extract_dir)
        return

    if is_tiff_file(download_path):
        print("Detectado GeoTIFF directo. Copiando como .tif...")
        target = extract_dir / "aemet_peligro_directo.tif"
        target.write_bytes(download_path.read_bytes())
        return

    # Si llega aquí, probablemente es HTML o JSON de error
    sample = download_path.read_bytes()[:800]
    try:
        print("Primeros caracteres de la descarga:")
        print(sample.decode("utf-8", errors="replace"))
    except Exception:
        print(sample)

    raise RuntimeError(
        "La descarga no es ZIP ni GeoTIFF. Probablemente AEMET ha devuelto HTML/JSON o una respuesta no esperada."
    )


def find_tifs(folder: Path):
    tifs = sorted(folder.rglob("*.tif")) + sorted(folder.rglob("*.tiff"))

    print("Archivos encontrados tras extraer:")
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            print(" -", p.relative_to(folder))

    return tifs


def get_day_code(path: Path) -> str:
    match = re.search(r"_D(\d{2})", path.name, re.IGNORECASE)
    if match:
        return f"D{match.group(1)}"
    return "D00"


def get_date_from_name(path: Path) -> str:
    match = re.search(r"(\d{8})", path.name)
    if match:
        return match.group(1)
    return ""


def raster_to_png(tif_path: Path, png_path: Path):
    with rasterio.open(tif_path) as src:
        data = src.read(1)
        bounds = src.bounds
        crs = str(src.crs) if src.crs else None
        nodata = src.nodata

        rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)

        for value, color in COLORS.items():
            mask = data == value
            rgba[mask] = color

        # Transparencia para nodata
        if nodata is not None:
            rgba[data == nodata] = (0, 0, 0, 0)

        # Transparencia para valores no clasificados
        valid_values = set(COLORS.keys())
        valid_mask = np.isin(data, list(valid_values))
        rgba[~valid_mask] = (0, 0, 0, 0)

        img = Image.fromarray(rgba, mode="RGBA")
        img.save(png_path)

        return {
            "bounds": {
                "west": float(bounds.left),
                "south": float(bounds.bottom),
                "east": float(bounds.right),
                "north": float(bounds.top),
            },
            "crs": crs,
            "width": int(src.width),
            "height": int(src.height),
            "nodata": None if nodata is None else float(nodata),
        }


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # Guardamos primero con nombre genérico
        download_path = DATA_DIR / "aemet_incendios_download.bin"

        print("Descargando datos AEMET...")
        download_file(URL_AEMET, download_path)

        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir(exist_ok=True)

        print("Extrayendo o identificando archivo...")
        extract_download(download_path, extract_dir)

        tifs = find_tifs(extract_dir)

        if not tifs:
            raise RuntimeError("No se han encontrado archivos .tif en la descarga.")

        layers = []

        for tif in tifs:
            day_code = get_day_code(tif)
            date_code = get_date_from_name(tif)

            png_name = f"peligro_{day_code}.png"
            png_path = DOCS_DIR / png_name

            print(f"Procesando {tif.name} → {png_name}")
            raster_info = raster_to_png(tif, png_path)

            layers.append({
                "day": day_code,
                "date": date_code,
                "source_file": tif.name,
                "png": png_name,
                "bounds": raster_info["bounds"],
                "crs": raster_info["crs"],
                "width": raster_info["width"],
                "height": raster_info["height"],
            })

        layers = sorted(layers, key=lambda x: x["day"])

        LAYERS_JSON.write_text(
            json.dumps({
                "product": "Peligro de incendios forestales AEMET",
                "source": "AEMET",
                "updated_utc": datetime.now(timezone.utc).isoformat(),
                "layers": layers,
                "legend": [
                    {"value": 1, "label": "Muy bajo", "color": "#4B96E3"},
                    {"value": 2, "label": "Bajo", "color": "#51D1F6"},
                    {"value": 3, "label": "Moderado", "color": "#57E520"},
                    {"value": 4, "label": "Alto", "color": "#F9FB2F"},
                    {"value": 5, "label": "Muy alto", "color": "#EF8504"},
                    {"value": 6, "label": "Extremo", "color": "#F52300"},
                ]
            }, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        METADATA_JSON.write_text(
            json.dumps({
                "product": "Peligro de incendios forestales AEMET",
                "source": "AEMET",
                "download_url": URL_AEMET,
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "num_layers": len(layers),
                "layers": [layer["day"] for layer in layers],
            }, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print(f"Capas generadas: {len(layers)}")
        print(f"Archivo: {LAYERS_JSON}")
        print(f"Archivo: {METADATA_JSON}")


if __name__ == "__main__":
    main()
