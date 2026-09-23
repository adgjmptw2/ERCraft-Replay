import { itemArtBounds } from './item-art-bounds.js';
export function itemArtGeometry(width, height, bounds, size = 30) {
  if (!Array.isArray(bounds) || bounds.length !== 4) return null;
  const [left, top, right, bottom] = bounds;
  if (![width,height,...bounds].every(Number.isFinite) || left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) return null;
  const ratio = size / Math.max(right-left, bottom-top);
  return {width:width*ratio,height:height*ratio,x:(width-left-right)*ratio/2,y:(height-top-bottom)*ratio/2};
}
async function remoteGeometry(url) {
  return new Promise(resolve => {
    const img = new Image(); img.crossOrigin = 'anonymous';
    const timer = setTimeout(() => resolve(null), 5000);
    img.onerror = () => {clearTimeout(timer);resolve(null);};
    img.onload = () => {
      clearTimeout(timer);
      try {
        const canvas = document.createElement('canvas'); canvas.width=img.naturalWidth; canvas.height=img.naturalHeight;
        const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(img,0,0);
        const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
        let left=canvas.width,top=canvas.height,right=0,bottom=0;
        for(let y=0;y<canvas.height;y++) for(let x=0;x<canvas.width;x++) if(pixels[(y*canvas.width+x)*4+3]>=8){left=Math.min(left,x);top=Math.min(top,y);right=Math.max(right,x+1);bottom=Math.max(bottom,y+1);}
        resolve(itemArtGeometry(canvas.width,canvas.height,[left,top,right,bottom]));
      } catch { resolve(null); }
    };
    img.src=url;
  });
}
export async function applyMeasuredItemGeometry(data) {
  const icons=data.itemAssets?.icons || {};
  await Promise.all(Object.entries(icons).map(async ([code,asset]) => {
    const url=asset.imageDataUrl;
    if (!url) return;
    if (url.startsWith('data:')) {
      const measured=itemArtBounds[code];if(!measured)return;
      const bytes=new TextEncoder().encode(url);
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
      if(hash===measured.sourceSha256) asset.displayGeometry=itemArtGeometry(measured.width,measured.height,measured.bbox8);
    } else if (url.startsWith('https://cdn.dak.gg/assets/er/game-assets/12.3.0/')) {
      asset.displayGeometry=await remoteGeometry(url);
    }
  }));
}
