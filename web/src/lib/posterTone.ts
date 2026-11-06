/** 海报主色调：只取色相与饱和度（限制上限，免得卡片底色过艳），明度由使用方决定。参照 MoviePilot 的做法。 */
export interface PosterTone {
  /** 0–359 */
  hue: number;
  /** 0–55（百分比） */
  saturation: number;
}

const SAMPLE_W = 24;
const SAMPLE_H = 36;
const SATURATION_CAP = 0.55;
const CACHE_LIMIT = 200;
const cache = new Map<string, Promise<PosterTone | null>>();

/** 从 RGBA 像素算主色调：按饱和度加权，压低过暗、过亮像素的权重，让主体色而不是黑边或高光决定结果。 */
export function toneFromPixels(pixels: ArrayLike<number>): PosterTone | null {
  let red = 0; let green = 0; let blue = 0; let total = 0;
  for (let i = 0; i + 3 < pixels.length; i += 4) {
    if (pixels[i + 3] < 128) continue;
    const r = pixels[i]; const g = pixels[i + 1]; const b = pixels[i + 2];
    const max = Math.max(r, g, b); const min = Math.min(r, g, b);
    const saturation = max ? (max - min) / max : 0;
    const lightness = (max + min) / 510;
    const weight = 0.15 + saturation * Math.max(0, 1 - Math.abs(lightness - 0.5) * 1.6);
    red += r * weight; green += g * weight; blue += b * weight; total += weight;
  }
  if (!total) return null;
  return rgbToTone(red / total, green / total, blue / total);
}

function rgbToTone(red: number, green: number, blue: number): PosterTone {
  const r = red / 255; const g = green / 255; const b = blue / 255;
  const max = Math.max(r, g, b); const min = Math.min(r, g, b); const d = max - min;
  let hue = 0;
  if (d) {
    if (max === r) hue = ((g - b) / d) % 6;
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
  }
  const lightness = (max + min) / 2;
  const saturation = d ? d / (1 - Math.abs(2 * lightness - 1)) : 0;
  return { hue: Math.round((hue * 60 + 360) % 360), saturation: Math.round(Math.min(saturation, SATURATION_CAP) * 100) };
}

/** 读取海报（TMDB 图片允许跨域）缩小采样；读不到（跨域、网络）时为 null，结果按地址缓存。
 *  用页面上不显示的 w92 尺寸：显示用的海报是普通请求，TMDB 对不带 Origin 的请求不回跨域头，浏览器缓存了那份响应后，
 *  同一地址再按跨域读取会失败。 */
export function loadPosterTone(path: string | null | undefined): Promise<PosterTone | null> {
  const url = path ? `https://image.tmdb.org/t/p/w92${path}` : null;
  if (!url || typeof document === 'undefined') return Promise.resolve(null);
  const hit = cache.get(url);
  if (hit) return hit;
  const task = new Promise<PosterTone | null>((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = SAMPLE_W; canvas.height = SAMPLE_H;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) { resolve(null); return; }
        ctx.drawImage(img, 0, 0, SAMPLE_W, SAMPLE_H);
        resolve(toneFromPixels(ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data));
      } catch {
        resolve(null); // 画布被跨域污染
      }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
  cache.set(url, task);
  if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value as string);
  return task;
}
