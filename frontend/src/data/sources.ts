import type { Source, SourceDetail, Stat } from "./types";
import { series } from "./util";

export const sourceStats: Stat[] = [
  { label: "Total sources", value: "1,842", icon: "worlds", tint: "blue", delta: { value: "4.3%", dir: "up", label: "vs yesterday" }, spark: series(41, 16, 1500, 1900, 12) },
  { label: "Healthy collectors", value: "168", icon: "shield", tint: "green", delta: { value: "5.1%", dir: "up", label: "vs yesterday" }, spark: series(42, 16, 140, 172, 1) },
  { label: "Repair events today", value: "23", icon: "zap", tint: "amber", delta: { value: "2", dir: "down", label: "vs yesterday" }, spark: series(43, 16, 10, 30, 0.3) },
  { label: "Extraction completeness", value: "82.4%", icon: "gauge", tint: "green", delta: { value: "2.7pp", dir: "up", label: "vs yesterday" }, donut: 0.824 },
  { label: "Images approved", value: "1,243", icon: "image", tint: "blue", delta: { value: "8.6%", dir: "up", label: "vs yesterday" }, spark: series(44, 16, 900, 1300, 9) },
];

export const sources: Source[] = [
  { id: "s1", domain: "bestbuy.com", category: "Refrigerators", collector: "bd_retailer_us", items: 2341, completeness: 92, lastRun: "2m ago", health: "healthy", brand: "bestbuy" },
  { id: "s2", domain: "homedepot.com", category: "Refrigerators", collector: "bd_retailer_us", items: 1987, completeness: 86, lastRun: "5m ago", health: "healthy", brand: "homedepot" },
  { id: "s3", domain: "lowes.com", category: "Refrigerators", collector: "bd_retailer_us", items: 1654, completeness: 78, lastRun: "9m ago", health: "degraded", brand: "lowes" },
  { id: "s4", domain: "samsung.com", category: "Refrigerators", collector: "bd_brand_direct", items: 321, completeness: 95, lastRun: "3m ago", health: "healthy", brand: "samsung" },
  { id: "s5", domain: "lg.com", category: "Refrigerators", collector: "bd_brand_direct", items: 287, completeness: 90, lastRun: "6m ago", health: "healthy", brand: "lg" },
  { id: "s6", domain: "whirlpool.com", category: "Refrigerators", collector: "bd_brand_direct", items: 298, completeness: 67, lastRun: "14m ago", health: "degraded", brand: "whirlpool" },
  { id: "s7", domain: "ajmadison.com", category: "Refrigerators", collector: "bd_retailer_us", items: 1145, completeness: 71, lastRun: "17m ago", health: "degraded", brand: "ajmadison" },
  { id: "s8", domain: "bing.com/images", category: "Refrigerator Images", collector: "bd_images_global", items: 24771, completeness: 88, lastRun: "2m ago", health: "healthy", brand: "bing" },
  { id: "s9", domain: "bestbuy.com", category: "Product Manuals", collector: "bd_documents_us", items: 1003, completeness: 94, lastRun: "4m ago", health: "healthy", brand: "bestbuy" },
  { id: "s10", domain: "appliancepartspros.com", category: "Parts & Accessories", collector: "bd_retailer_us", items: 5632, completeness: 61, lastRun: "22m ago", health: "repairing", brand: "app" },
  { id: "s11", domain: "thingiverse.com", category: "CAD Models", collector: "bd_cad_global", items: 7235, completeness: 86, lastRun: "15m ago", health: "healthy", brand: "thingiverse" },
  { id: "s12", domain: "sketchfab.com", category: "CAD Models", collector: "bd_cad_global", items: 6103, completeness: 81, lastRun: "22m ago", health: "healthy", brand: "sketchfab" },
];

export const bestBuyDetail: SourceDetail = {
  product: "Samsung 28 cu. ft. 4-Door French Door Refrigerator Stainless Steel",
  model: "RF28R7201SR",
  imageSeed: 7,
  specs: [
    ["Model", "RF28R7201SR"],
    ["Dimensions (W×D×H)", "35.75\" × 34.5\" × 70\""],
    ["Mass", "301 lb"],
    ["Material", "Stainless Steel"],
    ["Manual URL", "https://images.samsung.com/is/content/samsung/p6pim/us/…"],
    ["Part hints", "water filter, crisper drawer, ice maker, door bin, temp sensor"],
  ],
  provenance: [
    ["First seen", "May 22, 2025 09:14 AM"],
    ["First successful extract", "May 22, 2025 09:17 AM"],
    ["Last successful extract", "May 24, 2025 10:12 AM"],
    ["Collector", "bd_retailer_us"],
    ["Source URL", "https://www.bestbuy.com/site/samsung-28-cu-ft-4-door-french-door-…"],
  ],
  photos: [
    { id: 1, score: 97, state: "selected", front: 98, background: 96, isolation: 97, identity: 97, seed: 7 },
    { id: 2, score: 91, state: "secondary", front: 92, background: 88, isolation: 90, identity: 93, seed: 21 },
    { id: 3, score: 84, state: "candidate", front: 90, background: 72, isolation: 85, identity: 88, seed: 33 },
    { id: 4, score: 64, state: "rejected", front: 76, background: 42, isolation: 68, identity: 71, seed: 48 },
  ],
  repairs: [
    { time: "8:42 AM", title: "HTML change detected", desc: "DOM structure changed: 12 elements impacted", kind: "detect" },
    { time: "8:43 AM", title: "Extraction failure", desc: "Missing key fields: price, availability, specs", kind: "fail" },
    { time: "8:44 AM", title: "Healing started", desc: "Selector re-learning + fallback strategies applied", kind: "heal" },
    { time: "8:47 AM", title: "Preview approved", desc: "Extractor preview validated by rules", kind: "approve" },
    { time: "8:49 AM", title: "Rerun complete", desc: "All fields extracted successfully", kind: "done" },
  ],
};
