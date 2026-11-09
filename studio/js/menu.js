// The tool switcher (☰ in the header): every page has the same <nav id="appMenu"> of links; this marks the current
// tool and opens / closes the menu.

/** Wire the ☰ button; `here` is the page's data-app (character, items, editor, dungeon, limner). */
export function initMenu(here) {
  const btn = document.getElementById("appMenuBtn"), menu = document.getElementById("appMenu");
  menu.querySelectorAll("a").forEach(a => a.classList.toggle("on", a.dataset.app === here));
  btn.onclick = (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); };
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.classList.add("hidden"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.classList.add("hidden"); });
}
