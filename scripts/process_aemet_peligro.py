<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />

  <title>MetVlc · Peligro de incendios AEMET</title>

  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  />

  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f9f9fe;
      color: #263238;
    }

    .page {
      max-width: 1200px;
      margin: 25px auto;
      background: #ffffff;
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 8px 28px rgba(0,0,0,0.14);
    }

    .header {
      background: linear-gradient(135deg, #0b2f4f 0%, #0b6fa4 65%, #ff6a2a 100%);
      color: white;
      padding: 18px 24px;
      text-align: center;
    }

    .header h1 {
      margin: 0;
      font-size: 25px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }

    .header p {
      margin: 6px 0 0;
      font-size: 14px;
      color: #e8f5ff;
    }

    .controls {
      display: flex;
      gap: 10px;
      justify-content: center;
      flex-wrap: wrap;
      padding: 15px;
      background: #f6f9fb;
      border-bottom: 1px solid #dfe8ee;
    }

    .controls button {
      border: 0;
      padding: 9px 15px;
      border-radius: 10px;
      background: #0b2f4f;
      color: white;
      font-weight: 700;
      cursor: pointer;
      box-shadow: 0 3px 10px rgba(0,0,0,0.12);
      transition: 0.2s ease;
      font-size: 13px;
    }

    .controls button:hover {
      background: #14527d;
    }

    .controls button.active {
      background: #ff6a2a;
    }

    #map {
      width: 100%;
      height: 720px;
      background: #dce8ee;
    }

    .legend {
      padding: 14px 18px;
      background: #ffffff;
      border-top: 1px solid #dfe8ee;
      font-size: 14px;
      line-height: 1.8;
      text-align: center;
    }

    .legend span {
      display: inline-block;
      width: 18px;
      height: 14px;
      margin-right: 6px;
      vertical-align: middle;
      border: 1px solid rgba(0,0,0,0.15);
    }

    .meta {
      font-size: 13px;
      color: #51636d;
      padding: 0 18px 18px;
      background: #ffffff;
      text-align: center;
      line-height: 1.5;
    }

    .leaflet-control-layers {
      border-radius: 10px !important;
      box-shadow: 0 4px 14px rgba(0,0,0,0.18) !important;
      font-size: 13px;
    }

    @media screen and (max-width: 900px) {
      .header h1 {
        font-size: 21px;
      }

      #map {
        height: 560px;
      }

      .legend {
        font-size: 13px;
      }

      .controls button {
        font-size: 12px;
        padding: 8px 11px;
      }
    }
  </style>
</head>

<body>

  <div class="page">

    <div class="header">
      <h1>Peligro de incendios forestales</h1>
      <p>Producto georreferenciado oficial · Fuente: AEMET · Comunitat Valenciana</p>
    </div>

    <div class="controls" id="layerButtons">
      Cargando capas...
    </div>

    <div id="map"></div>

    <div class="legend">
      <b>Leyenda:</b>
      &nbsp;&nbsp;
      <span style="background:#4B96E3;"></span> Muy bajo
      &nbsp;&nbsp;
      <span style="background:#51D1F6;"></span> Bajo
      &nbsp;&nbsp;
      <span style="background:#57E520;"></span> Moderado
      &nbsp;&nbsp;
      <span style="background:#F9FB2F;"></span> Alto
      &nbsp;&nbsp;
      <span style="background:#EF8504;"></span> Muy alto
      &nbsp;&nbsp;
      <span style="background:#F52300;"></span> Extremo
    </div>

    <div class="meta" id="metadata">
      Cargando metadatos...
    </div>

  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

  <script>
    let layersInfo = null;
    let currentOverlay = null;
    let cvBoundaryLayer = null;
    let cvBounds = null;
    let updatedUTC = "";

    const map = L.map("map", {
      preferCanvas: true,
      zoomControl: true
    }).setView([39.45, -0.45], 8);

    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    });

    const esriTopo = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 18,
        attribution: "Tiles &copy; Esri"
      }
    );

    const openTopo = L.tileLayer(
      "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
      {
        maxZoom: 17,
        attribution: "Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap"
      }
    );

    esriTopo.addTo(map);

    const baseMaps = {
      "Relieve · Esri": esriTopo,
      "Topográfico · OpenTopo": openTopo,
      "Calles · OSM": osm
    };

    L.control.layers(baseMaps, null, {
      collapsed: false
    }).addTo(map);

    map.createPane("rasterPane");
    map.getPane("rasterPane").style.zIndex = 320;

    map.createPane("boundaryPane");
    map.getPane("boundaryPane").style.zIndex = 360;

    function formatDateFromLayer(layer) {
      if (!layer.date || !layer.day) {
        return "";
      }

      /*
        layer.date viene desde el nombre del archivo AEMET.
        Ejemplo:
        down_20260529_peligro_p_D00.tif

        layer.date = 20260529
        layer.day = D00, D01, D02...
      */

      const year = Number(layer.date.slice(0, 4));
      const month = Number(layer.date.slice(4, 6)) - 1;
      const day = Number(layer.date.slice(6, 8));

      const offset = Number(layer.day.replace("D", ""));

      const fecha = new Date(Date.UTC(year, month, day));
      fecha.setUTCDate(fecha.getUTCDate() + offset);

      return fecha.toLocaleDateString("es-ES", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric"
      });
    }

    function dayLabel(layer) {
      const fecha = formatDateFromLayer(layer);

      if (!fecha) {
        return layer.day || "";
      }

      return `${layer.day} · ${fecha}`;
    }

    function setActiveButton(day) {
      document.querySelectorAll(".controls button").forEach(btn => {
        btn.classList.remove("active");
      });

      const btn = document.getElementById("btn-" + day);
      if (btn) {
        btn.classList.add("active");
      }
    }

    function buildButtons(layers) {
      const container = document.getElementById("layerButtons");
      container.innerHTML = "";

      layers.forEach(layer => {
        const btn = document.createElement("button");
        btn.id = "btn-" + layer.day;
        btn.textContent = dayLabel(layer);
        btn.onclick = function() {
          drawLayer(layer);
        };

        container.appendChild(btn);
      });
    }

    function drawCVBoundary(geojson) {
      if (cvBoundaryLayer) {
        map.removeLayer(cvBoundaryLayer);
      }

      cvBoundaryLayer = L.geoJSON(geojson, {
        pane: "boundaryPane",
        style: {
          color: "#0b2f4f",
          weight: 2.4,
          fill: false,
          opacity: 1
        }
      }).addTo(map);

      cvBounds = cvBoundaryLayer.getBounds();

      map.fitBounds(cvBounds, {
        padding: [20, 20]
      });

      map.setMaxBounds(cvBounds.pad(0.35));
    }

    function formatUTCToMadrid(isoString) {
      if (!isoString) return "—";

      const fecha = new Date(isoString);

      return fecha.toLocaleString("es-ES", {
        timeZone: "Europe/Madrid",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      });
    }

    function drawLayer(layer) {
      if (!layer) return;

      if (currentOverlay) {
        map.removeLayer(currentOverlay);
      }

      const b = layer.bounds;

      const bounds = [
        [b.south, b.west],
        [b.north, b.east]
      ];

      currentOverlay = L.imageOverlay(layer.png + "?v=" + new Date().getTime(), bounds, {
        pane: "rasterPane",
        opacity: 0.70,
        interactive: false
      }).addTo(map);

      setActiveButton(layer.day);

      if (cvBounds) {
        map.fitBounds(cvBounds, {
          padding: [20, 20]
        });
      }

      const fechaProducto = formatDateFromLayer(layer);
      const actualizado = formatUTCToMadrid(updatedUTC);

      document.getElementById("metadata").innerHTML = `
        <b>Fuente:</b> AEMET ·
        <b>Capa:</b> ${dayLabel(layer)} ·
        <b>Fecha válida:</b> ${fechaProducto || "—"}<br>
        <b>Archivo:</b> ${layer.source_file || ""}<br>
        <b>Actualizado:</b> ${actualizado} hora peninsular<br>
        Producto automático de peligro de incendios forestales basado en datos meteorológicos.
        Visualización recortada a la Comunitat Valenciana.
      `;
    }

    Promise.all([
      fetch("layers.json?v=" + new Date().getTime()).then(response => {
        if (!response.ok) {
          throw new Error("No se pudo cargar layers.json");
        }
        return response.json();
      }),

      fetch("cv.geojson?v=" + new Date().getTime()).then(response => {
        if (!response.ok) {
          throw new Error("No se pudo cargar cv.geojson");
        }
        return response.json();
      })
    ])
    .then(([layersData, cvData]) => {
      layersInfo = layersData;
      updatedUTC = layersData.updated_utc || "";

      if (!layersData.layers || layersData.layers.length === 0) {
        throw new Error("layers.json no contiene capas");
      }

      drawCVBoundary(cvData);
      buildButtons(layersData.layers);

      drawLayer(layersData.layers[0]);
    })
    .catch(error => {
      document.getElementById("layerButtons").innerHTML =
        "No se han podido cargar las capas.";
      document.getElementById("metadata").innerHTML =
        "Error al cargar el producto de peligro de incendios AEMET.";
      console.error(error);
    });
  </script>

</body>
</html>
