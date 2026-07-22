import os

import cv2
import numpy as np
import rasterio
from rasterio.mask import mask
import geopandas as gpd
from shapely.geometry import Polygon

# --- FILE PATHS (relative to the project root) ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join(BASE_DIR, "data", "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "output")

mask_path = os.path.join(INPUT_DIR, "panels.geojson")
map_path = os.path.join(INPUT_DIR, "odm_orthophoto.tif")
output_image_path = os.path.join(OUTPUT_DIR, "fault_analysis_result.png")
output_geojson_path = os.path.join(OUTPUT_DIR, "detected_faults.geojson")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Fail early with a clear message if the input files are missing
for path in (mask_path, map_path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Input file not found: {path}\n"
            "Place 'odm_orthophoto.tif' and 'panels.geojson' inside data/input/."
        )

print("1. Reading map and masks...")

# --- 1. READING MAP AND MASKS ---
with rasterio.open(map_path) as src:
    map_crs = src.crs

    # Read the GeoJSON file and align its coordinate system with the map
    gdf = gpd.read_file(mask_path)
    if gdf.crs != map_crs:
        gdf = gdf.to_crs(map_crs)

    geometries = [feature["geometry"] for feature in gdf.__geo_interface__["features"]]

    # Crop the map to only include the panel areas
    out_image, out_transform = mask(src, geometries, crop=True)

# --- 2. IMAGE PROCESSING AND HOTSPOT DETECTION ---
print("2. Processing image and detecting hotspots...")

# Convert to OpenCV format
img_cv = np.moveaxis(out_image, 0, -1)
if img_cv.shape[2] == 3:
    img_cv = cv2.cvtColor(img_cv, cv2.COLOR_RGB2BGR)

gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

# Mask for processing only inside the panels (dropping the background)
processing_mask = (gray > 0).astype(np.uint8)
panel_pixels = gray[processing_mask == 1]

# Sensitivity adjustment: Consider the brightest 1% as a fault
threshold_value = np.percentile(panel_pixels, 99)
ret, thresh = cv2.threshold(gray, threshold_value, 255, cv2.THRESH_BINARY)

# Prevent anomalies that spill outside the panels
hotspot_mask = cv2.bitwise_and(thresh, thresh, mask=processing_mask * 255)

# Save the black-and-white mask to visually inspect what the system found
cv2.imwrite(output_image_path, hotspot_mask)

# --- 3. CONVERTING BRIGHT SPOTS TO GPS COORDINATES AND EXPORTING ---
print("3. Extracting GPS coordinates of bright spots and generating GeoJSON...")

# Find external contours of only the white (bright) spots using OpenCV
contours, _ = cv2.findContours(hotspot_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

defective_area_polygons = []

for contour in contours:
    # Ignore spots smaller than 5 pixels (noise/garbage), only take significant glares
    if cv2.contourArea(contour) > 5:
        geo_points = []

        # Convert each pixel around the spot into a GPS coordinate
        for point in contour:
            x, y = point[0]
            # Pixel -> Coordinate conversion using out_transform
            lon, lat = rasterio.transform.xy(out_transform, y, x)
            geo_points.append((lon, lat))

        # Create a Polygon and append to the list if valid
        if len(geo_points) >= 3:
            polygon = Polygon(geo_points)
            defective_area_polygons.append(polygon)

# Convert the shapes to a format QGIS can read (GeoDataFrame) and save
if len(defective_area_polygons) > 0:
    gdf_hotspots = gpd.GeoDataFrame(geometry=defective_area_polygons, crs=map_crs)
    gdf_hotspots.to_file(output_geojson_path, driver="GeoJSON")
    print(f"\nPROCESS COMPLETE! File is ready: {output_geojson_path}")
    print("-> Open QGIS, load your drone map, and drag & drop this file onto it.")
    print("-> Go to layer settings and paint it solid Red. All done!")
else:
    print("Warning: No significant bright spots found to record on the map.")
