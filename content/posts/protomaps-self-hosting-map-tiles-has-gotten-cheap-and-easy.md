---
title: "Protomaps and PMTiles: How Serverless Vector Tiles Cut Mapping Costs by 90%"
date: 2026-11-15T23:38:16Z
slug: "protomaps-self-hosting-map-tiles-has-gotten-cheap-and-easy"
description: "Self-hosting web maps used to require heavy database clusters. Protomaps changes the equation with PMTiles and HTTP range requests on static storage."
tags: ["Open Source", "Developer Tools", "Web Development", "GIS"]
draft: false
---

For more than a decade, web engineers requiring interactive maps faced a frustrating operational fork in the road. You either outsourced the problem to commercial platforms like Mapbox or Google Maps Platform—risking runaway costs once your monthly active users scaled—or you took on the burden of self-hosting an entire geospatial pipeline. That self-hosted route typically meant provisioning an always-on PostgreSQL/PostGIS instance, running continuous ingestion pipelines with Osm2pgsql, setting up a tile-rendering daemon like TileServer GL or Tegola, and fronting the whole cluster with Redis caches and reverse proxies.

Protomaps breaks this operational deadlock. By introducing **PMTiles**—a single-file archive format for tiled pyramid data—Protomaps replaces running application servers and spatial databases with static file storage and HTTP range requests. Combined with modern zero-egress cloud storage and edge compute, developers can now host planet-scale interactive vector maps for pennies per month with zero servers to patch, monitor, or scale.

---

## Key Technical Features & Architecture

Protomaps is an open-source mapping ecosystem composed of three foundational elements: the PMTiles storage specification, free vector basemap data pipelines derived from OpenStreetMap, and client-side SDK integrations for renderers such as MapLibre GL JS.

```
+-------------------------------------------------------------+
|                Browser / Client (MapLibre GL)               |
|         Registers 'pmtiles://' custom protocol handler      |
+-------------------------------------------------------------+
                              |
                     HTTP Range Requests
                     (e.g., bytes=1024-4096)
                              |
                              v
+-------------------------------------------------------------+
|      Edge CDN / Static Object Storage (Cloudflare R2 / S3)  |
|            Single Archive File: "basemap.pmtiles"           |
|  [ Header | Root Directory | Leaf Directories | Tile Data ] |
+-------------------------------------------------------------+
```

### The PMTiles Specification

At the heart of Protomaps is PMTiles (currently in version 3). Historically, map tiles were either generated dynamically from a spatial database on every request or pre-rendered into millions of individual `.pbf` or `.png` files stored in nested directories (`/z/x/y.pbf`) on disk or in S3. The millions-of-files approach degrades file system performance and creates massive object-storage API request fees during deployment.

PMTiles solves this by packing the entire pyramid of tiles into a single contiguous binary archive. 

- **Internal Directory Tree:** The file contains an internal directory structure organized using Hilbert curves to enforce spatial locality. Tiles close to each other geographically sit close to each other on disk.
- **Deduplication:** Repeated tiles (such as empty ocean water tiles or uniform desert terrain) are stored once. The internal directory maps multiple tile coordinates to the same byte offset.
- **Sparse File Support:** PMTiles supports non-uniform bounds, meaning regional extracts contain only the exact bounding box needed without dead payload weight.

### HTTP Byte-Range Requests

Rather than running an active web server process that unpacks an archive on demand, PMTiles exploits standard HTTP `Range` headers (`Range: bytes=start-end`).

When a client pans or zooms:
1. The client checks its local in-memory directory cache.
2. If the tile offset is unknown, it issues an HTTP Range request to read the PMTiles header and root directory (typically under 16 KB).
3. If deep zoom levels require a leaf directory, the client fetches that small byte range.
4. Finally, the client requests the exact byte slice containing the target vector tile payload (Mapbox Vector Tile format, or MVT) and passes it directly to the browser's WebGL renderer.

Because standard CDNs and object storage providers (AWS S3, Google Cloud Storage, Cloudflare R2, MinIO) natively support HTTP byte-range requests, a static bucket behaves as a high-throughput tile server.

### Edge-Assisted vs. Direct Client Architectures

Protomaps supports two primary deployment topologies:

1. **Direct Client-to-Storage:** The browser uses the `pmtiles` JavaScript protocol plugin. MapLibre GL requests `pmtiles://https://storage.example.com/map.pmtiles/{z}/{x}/{y}`. The client runs range queries straight against the storage bucket over HTTPS.
2. **Edge Proxy / CDN Worker:** A lightweight serverless function (such as an AWS Lambda@Edge or Cloudflare Worker) sits between the user and the storage bucket. The worker exposes traditional REST endpoints (`/tiles/{z}/{x}/{y}.mvt`), translates those incoming coordinate requests to byte-range lookups on the backing bucket, and sets cache-control headers. This provides backward compatibility for legacy clients (like standard Leaflet configurations) that cannot execute custom range-request logic.

---

## Comparison: Protomaps vs. Traditional Alternatives

| Evaluation Vector | Protomaps (PMTiles) | Commercial SaaS (Mapbox) | Self-Hosted PostGIS + TileServer GL |
| :--- | :--- | :--- | :--- |
| **Ease of Deployment** | High. Single file upload to S3/R2 with zero server dependencies. | Immediate. Managed API with immediate token generation. | Low. Requires provisioning database instances, schema migrations, and render daemons. |
| **Runtime Infrastructure** | Static object storage + optional edge worker. | Fully managed third-party cloud. | 1–2 application servers + PostgreSQL instance + caching proxy. |
| **Pricing / Cost Structure** | Predictable. Only pay for static storage and object read requests (fractions of a cent per 10k hits). | Per-request pricing. Scales rapidly after free tier limits are crossed. | Fixed base cost ($40–$200+/month for compute/DB) regardless of traffic volume. |
| **Dynamic Data Queries** | Static only. Dynamic filtering must happen client-side or via separate APIs. | Extensive. Rich APIs for geocoding, directions, and dynamic data styling. | High. Can execute complex spatial SQL queries and render dynamic data per request. |
| **Offline / Local Use** | Native. A single `.pmtiles` file can run locally inside a mobile app or desktop wrapper. | Limited or constrained by SDK licensing. | Complex. Requires bundling embedded databases like SQLite/SpatiaLite. |
| **Best Use Case** | Content sites, SaaS dashboards, regional maps, cost-sensitive apps. | Turnkey enterprise apps needing real-time geocoding, turn-by-turn routing, and global POIs. | Custom GIS portals with constantly shifting custom spatial layers and complex joins. |

---

## Practical Implementation: Deploying a Vector Map

Setting up a working self-hosted vector map using Protomaps requires minimal infrastructure. Below is a complete implementation using public storage and a modern web frontend.

### Step 1: Obtain a PMTiles Archive

You can extract an area from OpenStreetMap using the `pmtiles` command-line utility, or download pre-compiled regional extracts directly from the Protomaps build distribution:

```bash
# Install the pmtiles CLI (macOS example via Homebrew, or download release binary)
brew install pmtiles

# Download a sample extract (e.g., a regional city extract)
curl -O https://build.protomaps.com/20261115/monaco.pmtiles
```

### Step 2: Upload to CORS-Enabled Storage

Upload the file to your preferred static storage. Crucially, your bucket must expose `ETag` headers and allow `GET` and `HEAD` methods with range requests via CORS.

Here is an example AWS S3 CORS configuration:

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": ["https://yourdomain.com"],
    "ExposeHeaders": ["Content-Range", "ETag", "Accept-Ranges"]
  }
]
```

### Step 3: Frontend Client Integration

Include MapLibre GL JS along with the Protomaps PMTiles protocol plugin. The plugin hooks into MapLibre's networking pipeline, intercepting `pmtiles://` URLs to fetch byte ranges directly.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Protomaps Integration</title>
  <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=no" />
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet" />
  <script src="https://unpkg.com/pmtiles@3.2.0/dist/pmtiles.js"></script>
  <style>
    body { margin: 0; padding: 0; }
    #map { position: absolute; top: 0; bottom: 0; width: 100%; }
  </style>
</head>
<body>
<div id="map"></div>
<script>
  // 1. Initialize the PMTiles protocol engine
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  // 2. Define the remote PMTiles resource
  const PMTILES_URL = "https://your-bucket.r2.cloudflarestorage.com/monaco.pmtiles";
  const p = new pmtiles.PMTiles(PMTILES_URL);
  protocol.add(p);

  // 3. Initialize MapLibre using a compatible vector style
  const map = new maplibregl.Map({
    container: 'map',
    style: 'https://api.protomaps.com/styles/v3/light/en.json', // or a self-hosted style JSON
    center: [7.42, 43.73], // Monaco coordinates
    zoom: 13
  });
</script>
</body>
</html>
```

---

## Limitations & Trade-offs

While Protomaps eliminates server operational overhead, the architecture comes with explicit technical trade-offs that teams must weigh during system design.

### Immutability and Update Frequencies
PMTiles archives are read-only, statically built files. If your system requires frequent, real-time spatial updates (such as live asset tracking or continuous user-generated polygon submissions), PMTiles is not a database replacement. Updating the base map requires generating a new archive or orchestrating regional patch archives, which makes rapid daily global updates network-intensive.

### Latency Profiles and Directory Hopping
Because the client relies on HTTP range queries, cold cache lookups may require two to three sequential HTTP requests to retrieve the header, resolve the leaf directory, and fetch the tile data. On high-latency mobile networks or poorly configured object storage, this creates initial rendering delay compared to edge-cached flat files or dynamic servers running high-memory Redis caches.

### Asset Hosting Dependencies
Deploying an interactive map requires more than just raw vector geometry. You must also host fonts (SDF glyphs for label rendering), sprite sheets for map icons, and vector style JSON files. Teams transitioning from Mapbox often forget that Mapbox bundles geocoding, turn-by-turn routing, satellite imagery, and style hosting into a single API token. With Protomaps, you handle search (geocoding) and styles through external or self-managed tooling.

---

## Frequently Asked Questions (FAQ)

### Can I use Protomaps with Leaflet or OpenLayers instead of MapLibre?
Yes. Protomaps provides client adapters for Leaflet and OpenLayers through the `pmtiles` package. However, because PMTiles typically encapsulates vector data (MVT format), MapLibre GL is the preferred renderer due to native WebGL hardware acceleration. If using Leaflet, you can either render raster PMTiles archives directly or use vector tile plugins like Leaflet.VectorGrid.

### How large is a full Planet PMTiles archive, and can commodity object storage handle it?
A full-planet basemap PMTiles archive (including global street networks, buildings, water boundaries, and terrain features up to zoom level 15) is roughly 100 to 120 GB. Modern object stores like Cloudflare R2 or Amazon S3 easily host single objects of this scale (S3 supports files up to 5 TB), meaning entire global maps fit in a single static file without splitting.

### How do you secure tiles or prevent unauthorized domain hotlinking?
Because PMTiles files sit on standard web endpoints, access control follows conventional web security patterns. You can protect your archive by restricting CORS origins on your storage bucket, serving tiles through an edge proxy (such as Cloudflare Workers or AWS CloudFront) that inspects JWTs or referrer headers, or using time-limited presigned URLs generated by your application backend.

## Related Technical Guides
- [Ggml v1.0.0](/posts/ggml-v1-0-0/)
- [KDE: The Documentary on Free Software [video]](/posts/kde-the-documentary-on-free-software-video/)
- [SpaceX catches the Starship to end Flight 15](/posts/spacex-catches-the-starship-to-end-flight-15/)
- [All AI-generated code should be Open Source](/posts/all-ai-generated-code-should-be-open-source/)
