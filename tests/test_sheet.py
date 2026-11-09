"""Set material sheet in hdart.dungeon (no GPU): linked templates follow the sheet, matching wall families, prompt
words, colour match to the floor master. Run: uv run python tests/test_sheet.py"""
import fixture_specs  # noqa: F401  (HD_SPECS: a copy of tests/fixtures/specs; before any hdart import)
import shutil, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
import hdart.dungeon as d

tmp = Path(tempfile.mkdtemp())
shutil.copytree(d.SETS, tmp / "sets"); d.SETS = tmp / "sets"; d.DUN = tmp / "dun"; d.ARCHIVE = tmp / "archive"
d.new_set("zs", biome="crypt")
fid = "tile.zs_floor_master"
d.upsert_piece("zs", {"id": fid, "kind": "tile", "shape": "floor", "method": "master", "variants": 1, "prompt": "grey flagstones",
                      "family": {"template": {"pattern": "flagstone", "params": {"across": 4}, "strength": 0.7, "seed": 1}}})
# an active floor master: warm brown stones
f = d.set_dir("zs") / "candidates" / fid / "m.png"; f.parent.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(1)
Image.fromarray(np.clip(np.array([150, 110, 80]) + rng.normal(0, 12, (64, 64, 3)), 0, 255).astype(np.uint8)).save(f)
np.save(f.with_suffix(".npy"), np.ones((4, 4), bool))
s = d.state("zs"); s["pieces"][fid] = {"candidates": ["candidates/" + fid + "/m.png"], "approved": ["candidates/" + fid + "/m.png"]}; d.save_state("zs", s)

# sheet from the floor: palette from its master, stone size from its template, the floor linked
spec = d.sheet_from_floor("zs", fid)
sh = d.sheet(spec)
assert sh["floor"] == fid and abs(sh["floor_stone"] - 0.25) < 1e-6, sh
r, g, b = (int(sh["palette"]["stones"][0][i:i + 2], 16) for i in (1, 3, 5))
assert r > g > b, sh["palette"]  # warm, like the master
tpl = d.family_template(d.piece_of(spec, fid))
assert tpl["link"] and tpl["palette"] == sh["palette"] and tpl["params"]["across"] == 4

# a matching wall family: linked running bond with the sheet's module
wid = d.matching_wall_family("zs", fid)
spec = d.load_set("zs"); w = d.piece_of(spec, wid)
wt = d.family_template(w)
assert w["method"] == "wall_master" and wt["link"] and wt["pattern"] == "running_bond" and wt["palette"] == sh["palette"]
assert wt["params"]["across"] == 3 and abs(wt["params"]["course"] - 0.15 * d.iso._KZ) < 1e-3, wt

# changing the sheet rewrites every linked template; an unlinked one keeps its own
d.upsert_piece("zs", d.link_family(spec, d.piece_of(spec, fid), False))
spec = d.update_sheet("zs", {"wall_course": 0.2, "wall_brick": 0.25, "joint": 0.02, "words": "black basalt, green moss"})
wt = d.family_template(d.piece_of(spec, wid)); ft = d.family_template(d.piece_of(spec, fid))
assert wt["params"]["across"] == 4 and abs(wt["params"]["course"] - 0.2 * d.iso._KZ) < 1e-3 and wt["params"]["mortar"] == 0.02
assert "link" not in ft and ft["params"]["mortar"] != 0.02
assert "black basalt" in d.wall_texture_prompt(d.set_style(spec), "x") and "black basalt" in d.floor_texture_prompt(d.set_style(spec), "x")
try: d.update_sheet("zs", {"nope": 1}); raise SystemExit("no error")
except ValueError: pass

# wall masters pulled toward the floor master's colours
grey = Image.fromarray(np.full((64, 32, 3), 128, np.uint8))
warm = np.asarray(d.match_to_floor("zs", spec, grey)).reshape(-1, 3).mean(0)
assert warm[0] > warm[2] + 10, warm
d.update_sheet("zs", {"match": 0})
assert np.asarray(d.match_to_floor("zs", d.load_set("zs"), grey)).mean() == 128
# stages: a draft as the active master is 'part' (finalize it); a full one is 'done'; no variations yet = todo
st = {x['key']: x for x in d.family_stages('zs', d.load_set('zs'), d.piece_of(d.load_set('zs'), fid))}
assert st['master']['state'] == 'done' and st['variations']['state'] == 'todo' and st['export']['state'] == 'todo', st
s = d.state('zs'); s['pieces'][fid]['meta'] = {'candidates/' + fid + '/m.png': {'draft': True}}; d.save_state('zs', s)
st = {x['key']: x for x in d.family_stages('zs', d.load_set('zs'), d.piece_of(d.load_set('zs'), fid))}
assert st['master']['state'] == 'part' and 'finalize' in st['master']['detail'], st['master']
ws = [x['key'] for x in d.family_stages('zs', d.load_set('zs'), d.piece_of(d.load_set('zs'), wid))]
assert ws == ['template', 'master', 'variations', 'doors', 'built', 'export'], ws
# fill gaps: refused on a draft master; else one job per (variation, size) without a candidate from the active master
try: d.fill_jobs('zs', fid); raise SystemExit('no error')
except ValueError as e: assert 'draft' in str(e)
s = d.state('zs'); s['pieces'][fid]['meta'] = {}; d.save_state('zs', s)
jobs = d.fill_jobs('zs', fid)
fam = d.family_settings(d.piece_of(d.load_set('zs'), fid))
assert len(jobs) == len(fam['variations']) * len(fam['sizes']), len(jobs)
j = jobs[0]; mrel = d.master_rel('zs', fid)
s = d.state('zs'); ps = s['pieces'].setdefault(j['piece'], {'candidates': [], 'approved': []})
ps['candidates'].append('candidates/x.png'); ps.setdefault('meta', {})['candidates/x.png'] = {'master': mrel, 'variation': j['variation']}; d.save_state('zs', s)
assert len(d.fill_jobs('zs', fid)) == len(jobs) - 1
assert d.review_list('zs', fid) == [{'pid': j['piece'], 'rel': 'candidates/x.png'}]
# recipes: save the set's floor + wall as a kit (with its sheet), use it in a fresh set
d.RECIPES = tmp / 'recipes'
rec = d.save_recipe('zs', [fid, wid], 'Basalt Kit', notes='test')
assert rec['id'] == 'basalt_kit' and [f['kind'] for f in rec['families']] == ['floor', 'wall'] and rec['sheet']['words'] == 'black basalt, green moss'
assert 'boards' not in rec['families'][1]['family'] and d.list_recipes()[0]['title'] == 'Basalt Kit'
d.new_set('zt', biome='sewer')
made = d.apply_recipe('zt', 'basalt_kit')
spec_t = d.load_set('zt')
assert len(made) == 2 and d.piece_of(spec_t, made[0])['method'] == 'master' and d.piece_of(spec_t, made[1])['method'] == 'wall_master', made
assert d.sheet(spec_t)['words'] == 'black basalt, green moss' and d.sheet(spec_t)['floor'] == made[0]
assert d.family_template(d.piece_of(spec_t, made[1]))['link'] and d.piece_of(spec_t, made[0])['family']['template']['pattern'] == 'flagstone'
again = d.apply_recipe('zt', 'basalt_kit')
assert set(again).isdisjoint(made) and len(again) == 2, again  # unique ids the second time
shutil.rmtree(tmp)
print("sheet tests passed")
