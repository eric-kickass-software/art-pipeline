// Ramp maths shared by the pages: hex / rgb / hsv and a ramp from a base colour (= hdart/imaging/palette.py
// ramp_from_base at the default tune; tests/test_ramps.py checks the two agree).
const MONK = ["#f6ede4","#f3e7db","#f7ead0","#eadaba","#d7bd96","#a07e56","#825c43","#604134","#3a312a","#292420"];
const TUNE0 = { hueOffset:0, saturation:1, shadowHue:0.72, shadowShift:0.12, shadowSat:0.25, shadowDark:0.55,
                lightHue:0.15, lightShift:0.08, lightDesat:0.30, lightLift:0.55 };
const SLIDERS = [["saturation","Saturation ×",0,2.5,0.01],["hueOffset","Hue offset",-0.1,0.1,0.002],
  ["shadowDark","Shadow depth",0,0.9,0.01],["shadowShift","Shadow hue shift",0,0.4,0.005],["shadowHue","Shadow hue target",0,1,0.005],
  ["shadowSat","Shadow saturation +",0,1,0.01],["lightLift","Highlight lift",0,1,0.01],["lightShift","Highlight hue shift",0,0.4,0.005],
  ["lightHue","Highlight hue target",0,1,0.005],["lightDesat","Highlight desaturate",0,1,0.01]];
const hex2rgb = h => [1,3,5].map(i => parseInt(h.slice(i,i+2),16));
const rgb2hex = c => "#" + c.map(v => Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,"0")).join("");
function rgb2hsv([r,g,b]) { r/=255; g/=255; b/=255; const mx=Math.max(r,g,b), mn=Math.min(r,g,b), d=mx-mn; let h=0;
  if (d) h = mx===r ? ((g-b)/d)%6 : mx===g ? (b-r)/d+2 : (r-g)/d+4; h=(h/6+1)%1; return [h, mx? d/mx:0, mx]; }
function hsv2rgb(h,s,v) { const i=Math.floor(h*6), f=h*6-i, p=v*(1-s), q=v*(1-f*s), t=v*(1-(1-f)*s);
  const [r,g,b] = [[v,t,p],[q,v,p],[p,v,t],[p,q,v],[t,p,v],[v,p,q]][((i%6)+6)%6]; return [r*255,g*255,b*255]; }
const shiftHue = (h, target, amt) => (h + ((((target - h + 0.5) % 1) + 1) % 1 - 0.5) * amt + 1) % 1;
function rampFrom(base, t, size) {   // = hdart.palette.ramp_from_base at the default tune, plus the editor's knobs
  let [h,s,v] = rgb2hsv(hex2rgb(base)); h = (h + t.hueOffset + 1) % 1; s = Math.min(1, s * t.saturation);
  const mid = Math.floor(size/2), out = [];
  for (let i=0;i<size;i++) { const k = (i-mid)/Math.max(mid,1); let hh, ss, vv;
    if (k < 0) { hh = shiftHue(h, t.shadowHue, t.shadowShift * -k); ss = Math.min(1, s*(1 + t.shadowSat*-k) + 0.05*-k); vv = v*(1 + t.shadowDark*k); }
    else { hh = shiftHue(h, t.lightHue, t.lightShift*k); ss = s*(1 - t.lightDesat*k); vv = Math.min(1, v + (1-v)*t.lightLift*k + 0.08*k); }
    out.push(hsv2rgb(hh, ss, vv)); }
  return out; }

export { MONK, TUNE0, SLIDERS, hex2rgb, rgb2hex, rgb2hsv, hsv2rgb, shiftHue, rampFrom };
