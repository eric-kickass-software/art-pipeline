// The Character Workshop page (/workshop): the Template, Race, Face and Ramps tabs (one module each). Tab anchors:
// /workshop#races opens the Race tab. The Item Workshop (/items) and the Workshop Editor (/editor) are their own pages.
import "./races.js";
import "./face.js";
import "./ramps-tab.js";
// <imports>
import { guard, startPage, wantedTab } from "./common.js";
import { initTemplate } from "./template.js";
// </imports>

startPage("character");
{ const first = wantedTab();
  if (first.dataset.t === "template") guard(initTemplate)();   // Template is the first step
  first.click(); }
