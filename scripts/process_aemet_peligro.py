from pathlib import Path
from datetime import datetime, timezone, timedelta
import json
import re
import zipfile
import tarfile
import tempfile
import requests
import numpy as np
import rasterio
from rasterio.mask import mask as raster_mask
from rasterio.transform import array_bounds
from rasterio.warp import transform_geom, transform_bounds
from PIL import Image


URL_AEMET = "https://www.aemet.es/es/api-eltiempo/incendios/download"

DOCS_DIR = Path("docs")
DATA_DIR = Path("data")

DOCS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

CV_GEOJSON = DOCS_DIR / "cv.geojson"
LAYERS_JSON = DOCS_DIR / "layers.json"
METADATA_JSON = DOCS_DIR / "metadata.json"


COLORS = {
    1: (75, 150, 227, 220),   # Muy bajo
    2: (81, 209, 246, 220),   # Bajo
    3: (87, 229, 32, 220),    # Moderado
    4: (249, 251, 47, 220),   # Alto
    5: (239, 133, 4, 220),    # Muy alto
    6: (245, 35, 0, 220),     # Extremo
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


def safe_extract_tar(tar: tarfile.TarFile, path: Path):
    base_path = path.resolve()

    for member in tar.getmembers():
        member_path = (path / member.name).resolve()

        if not str(member_path).startswith(str(base_path)):
            raise RuntimeError(f"Ruta insegura dentro del TAR: {member.name}")

    tar.extractall(path)


def safe_extract_zip(z: zipfile.ZipFile, path: Path):
    base_path = path.resolve()

    for member in z.infolist():
        member_path = (path / member.filename).resolve()

        if not str(member_path).startswith(str(base_path)):
            raise RuntimeError(f"Ruta insegura dentro del ZIP: {member.filename}")

    z.extractall(path)


def extract_download(download_path: Path, extract_dir: Path):
    print("Comprobando tipo de archivo descargado...")

    if tarfile.is_tarfile(download_path):
        print("Detectado TAR/TAR.GZ. Extrayendo...")
        with tarfile.open(download_path, "r:*") as tar:
            safe_extract_tar(tar, extract_dir)
        return

    if zipfile.is_zipfile(download_path):
        print("Detectado ZIP. Extrayendo...")
        with zipfile.ZipFile(download_path, "r") as z:
            safe_extract_zip(z, extract_dir)
        return

    if is_tiff_file(download_path):
        print("Detectado GeoTIFF directo. Copiando como .tif...")
        target = extract_dir / "aemet_peligro_directo.tif"
        target.write_bytes(download_path.read_bytes())
        return

    raise RuntimeError(
        "La descarga no es TAR.GZ, ZIP ni GeoTIFF."
    )


def find_tifs(folder: Path):
    tifs = sorted(folder.rglob("*.tif")) + sorted(folder.rglob("*.tiff"))

    print("Archivos encontrados tras extraer:")
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            print(" -", p.relative_to(folder))

    return tifs


def get_day_code(path: Path) -> str:
    """
    Extrae D00, D01, D02... del nombre del TIFF.
    """

    match = re.search(r"_D(\d{2})", path.name, re.IGNORECASE)

    if match:
        return f"D{match.group(1)}"

    return "D00"


def get_day_offset(day_code: str) -> int:
    """
    Convierte D00, D01, D02... en 0, 1, 2...
    """

    match = re.search(r"D(\d{2})", day_code, re.IGNORECASE)

    if match:
        return int(match.group(1))

    return 0


def get_base_date_from_tif_name(path: Path):
    """
    Extrae la fecha base del nombre del TIFF.

    En este producto de AEMET, todos los TIFF llevan la misma fecha en el nombre.
    Esa fecha corresponde al D00 del producto.

    Ejemplo:
    archivo_20260529_..._D00.tif  -> D00 = 2026-05-29
    archivo_20260529_..._D01.tif  -> D01 = 2026-05-30
    archivo_20260529_..._D02.tif  -> D02 = 2026-05-31
    """

    match = re.search(r"(\d{8})", path.name)

    if not match:
        return None

    date_code = match.group(1)

    try:
        return datetime.strptime(date_code, "%Y%m%d").date()
    except ValueError:
        return None


def get_base_date_from_tifs(tifs):
    """
    Busca la fecha base en los nombres de los TIFF seleccionados.
    Usa la primera fecha válida encontrada.
    """

    dates = []

    for tif in tifs:
        date_value = get_base_date_from_tif_name(tif)

        if date_value:
            dates.append(date_value)

    if not dates:
        raise RuntimeError(
            "No se ha podido encontrar ninguna fecha YYYYMMDD en el nombre de los TIFF. "
            "No se puede calcular la fecha real de D00, D01, D02..."
        )

    unique_dates = sorted(set(dates))

    if len(unique_dates) > 1:
        print("Aviso: se han encontrado varias fechas base en los TIFF:")
        for d in unique_dates:
            print(" -", d)

        print("Se usará la fecha más antigua como D00:", unique_dates[0])

    return unique_dates[0]


def get_valid_date_from_day(day_code: str, base_date) -> str:
    """
    Calcula la fecha válida de la capa.

    IMPORTANTE:
    D00 no es necesariamente hoy.
    D00 es la fecha base que aparece en el nombre de los TIFF de AEMET.
    D01 = fecha base + 1 día.
    D02 = fecha base + 2 días.
    etc.

    Devuelve formato YYYYMMDD para layers.json.
    """

    offset = get_day_offset(day_code)
    valid_date = base_date + timedelta(days=offset)
    return valid_date.strftime("%Y%m%d")


def load_cv_geometries():
    if not CV_GEOJSON.exists():
        raise FileNotFoundError(
            "No existe docs/cv.geojson. Súbelo al repositorio para recortar a la Comunitat Valenciana."
        )

    data = json.loads(CV_GEOJSON.read_text(encoding="utf-8"))

    if data["type"] == "FeatureCollection":
        geometries = [feature["geometry"] for feature in data["features"]]

    elif data["type"] == "Feature":
        geometries = [data["geometry"]]

    elif data["type"] in ["Polygon", "MultiPolygon"]:
        geometries = [data]

    else:
        raise ValueError("cv.geojson no tiene una geometría válida.")

    if not geometries:
        raise ValueError("cv.geojson no contiene geometrías.")

    return geometries


def geometries_to_raster_crs(geometries, dst_crs):
    """
    El cv.geojson normalmente estará en EPSG:4326.
    Si el GeoTIFF de AEMET viene en otro CRS, transformamos el polígono
    antes de recortar.
    """

    if dst_crs is None:
        print("Aviso: el GeoTIFF no tiene CRS. Se usará cv.geojson sin reproyectar.")
        return geometries

    dst_crs_str = str(dst_crs)

    if dst_crs_str.upper() in ["EPSG:4326", "OGC:CRS84"]:
        return geometries

    print(f"Reproyectando cv.geojson desde EPSG:4326 a {dst_crs_str}")

    transformed = [
        transform_geom(
            "EPSG:4326",
            dst_crs,
            geom,
            precision=6
        )
        for geom in geometries
    ]

    return transformed


def make_rgba_from_data(data_masked):
    """
    Convierte el raster recortado en imagen RGBA.
    Todo lo que esté fuera del polígono o sea NoData queda transparente.
    """

    data = np.asarray(data_masked)
    invalid_mask = np.ma.getmaskarray(data_masked)

    rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)

    if np.issubdtype(data.dtype, np.floating):
        data_values = np.rint(data).astype(np.int16)
    else:
        data_values = data.astype(np.int16)

    visible_data = data_values[~invalid_mask]

    if visible_data.size > 0:
        unique, counts = np.unique(visible_data, return_counts=True)

        print("Valores encontrados dentro de la CV:")
        for value, count in zip(unique, counts):
            value_int = int(value)

            if value_int in LABELS:
                print(f"  {value_int} - {LABELS[value_int]}: {int(count)} píxeles")
            else:
                print(f"  {value_int} - valor no clasificado: {int(count)} píxeles")
    else:
        print("Aviso: no hay datos visibles dentro del recorte.")

    valid_values = list(COLORS.keys())
    valid_mask = np.isin(data_values, valid_values) & (~invalid_mask)

    for value, color in COLORS.items():
        value_mask = (data_values == value) & valid_mask
        rgba[value_mask] = color

    rgba[~valid_mask] = (0, 0, 0, 0)

    return rgba


def get_bounds_wgs84(transform, width, height, src_crs):
    """
    Devuelve bounds en EPSG:4326 para que Leaflet coloque bien el PNG.

    rasterio.array_bounds devuelve:
    west, south, east, north
    """

    west, south, east, north = array_bounds(height, width, transform)

    if src_crs is not None and str(src_crs).upper() not in ["EPSG:4326", "OGC:CRS84"]:
        west, south, east, north = transform_bounds(
            src_crs,
            "EPSG:4326",
            west,
            south,
            east,
            north,
            densify_pts=21
        )

    return {
        "west": float(west),
        "south": float(south),
        "east": float(east),
        "north": float(north),
    }


def raster_to_png(tif_path: Path, png_path: Path, cv_geometries):
    with rasterio.open(tif_path) as src:
        print("CRS raster:", src.crs)
        print("Bounds raster original:", src.bounds)
        print("Tamaño raster original:", src.width, "x", src.height)
        print("NoData:", src.nodata)

        cv_geometries_raster = geometries_to_raster_crs(cv_geometries, src.crs)

        try:
            cropped_data, cropped_transform = raster_mask(
                src,
                cv_geometries_raster,
                crop=True,
                filled=False,
                indexes=1
            )
        except ValueError as exc:
            raise RuntimeError(
                "No hay solape entre el GeoTIFF de AEMET y docs/cv.geojson. "
                "Revisa que cv.geojson esté en EPSG:4326 y que represente la Comunitat Valenciana."
            ) from exc

        if cropped_data.ndim == 3:
            cropped_data = cropped_data[0]

        height, width = cropped_data.shape

        print("Tamaño raster recortado:", width, "x", height)

        rgba = make_rgba_from_data(cropped_data)

        alpha_pixels = int(np.count_nonzero(rgba[:, :, 3]))
        print("Píxeles visibles en PNG:", alpha_pixels)

        if alpha_pixels == 0:
            print(
                "Aviso: el PNG no tiene píxeles visibles. "
                "Puede que el TIFF no tenga valores 1-6 dentro de la CV."
            )

        img = Image.fromarray(rgba, mode="RGBA")
        img.save(png_path, optimize=True)

        bounds = get_bounds_wgs84(
            cropped_transform,
            width,
            height,
            src.crs
        )

        print("Bounds PNG en EPSG:4326:", bounds)

        return {
            "bounds": bounds,
            "crs": str(src.crs) if src.crs else None,
            "width": int(width),
            "height": int(height),
            "nodata": None if src.nodata is None else float(src.nodata),
        }


def main():
    cv_geometries = load_cv_geometries()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        download_path = DATA_DIR / "aemet_incendios_download.bin"

        print("Descargando datos AEMET...")
        download_file(URL_AEMET, download_path)

        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir(exist_ok=True)

        print("Extrayendo o identificando archivo...")
        extract_download(download_path, extract_dir)

        tifs = find_tifs(extract_dir)

        # Ignoramos Canarias y usamos solo Península/Baleares.
        tifs = [
            tif for tif in tifs
            if "_p_" in tif.name.lower()
        ]

        if not tifs:
            raise RuntimeError(
                "No se han encontrado archivos .tif de Península/Baleares (_p_) en la descarga."
            )

        print("Archivos GeoTIFF seleccionados para Península/Baleares:")
        for tif in tifs:
            print(" -", tif.name)

        # Aquí está la corrección importante.
        # La fecha base no es la fecha actual.
        # La fecha base de D00 se lee del nombre de los TIFF.
        base_date = get_base_date_from_tifs(tifs)

        print("Fecha base AEMET para D00:", base_date)

        for old_png in DOCS_DIR.glob("peligro_D*.png"):
            print("Eliminando capa antigua:", old_png.name)
            old_png.unlink()

        layers = []

        for tif in tifs:
            day_code = get_day_code(tif)
            date_code = get_valid_date_from_day(day_code, base_date)

            png_name = f"peligro_{day_code}.png"
            png_path = DOCS_DIR / png_name

            print(f"Procesando {tif.name} → {png_name}")
            print(f"Código de día: {day_code} → fecha válida: {date_code}")

            raster_info = raster_to_png(tif, png_path, cv_geometries)

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
                "base_date_aemet": base_date.strftime("%Y%m%d"),
                "base_date_note": "D00 se calcula a partir de la fecha incluida en el nombre de los TIFF de AEMET.",
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
                "base_date_aemet": base_date.strftime("%Y%m%d"),
                "num_layers": len(layers),
                "layers": [layer["day"] for layer in layers],
                "dates": [
                    {
                        "day": layer["day"],
                        "date": layer["date"],
                        "png": layer["png"],
                        "source_file": layer["source_file"]
                    }
                    for layer in layers
                ],
                "note": (
                    "Se ignoran los archivos de Canarias (_c_) y se usan solo Península/Baleares (_p_). "
                    "Cada PNG se recorta a la Comunitat Valenciana mediante docs/cv.geojson. "
                    "El exterior del polígono queda transparente. "
                    "La fecha de D00 se obtiene de la fecha incluida en el nombre de los TIFF de AEMET. "
                    "D01, D02, etc. se calculan sumando días a esa fecha base."
                ),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print(f"Capas generadas: {len(layers)}")
        print(f"Archivo: {LAYERS_JSON}")
        print(f"Archivo: {METADATA_JSON}")


if __name__ == "__main__":
    main()
