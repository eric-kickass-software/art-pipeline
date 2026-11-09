"""hdart: the art tools' command line. Run `uv run hdart --help` from P:/Tools/art-pipeline.

The Studio (`hdart studio`) is where art is made; these commands start it, export approved art into the active
project (config.toml profile) and back up the tools' data.
"""
import argparse


def cmd_studio(a):
    from .studio import serve
    serve(a.port, not a.no_browser)


def cmd_dungeon_export(a):
    from . import dungeon
    print(dungeon.export(a.set))


def cmd_puppet_export(a):
    from . import unity_export
    print(unity_export.export(a.race, a.size))


def cmd_item_export(a):
    from . import item_export as ie
    r = ie.export(a.items or None, a.race, a.size)
    for gid, e in r["exported"].items():
        print(f"{gid}: {', '.join(e['builds'])}" + (" + intake" if e["intake"] else ""))
        for w in e["warnings"]:
            print(f"  ! {w}")
    for gid, why in r["skipped"].items():
        print(f"skipped {why}" if why.startswith(gid) else f"skipped {gid}: {why}")
    print(f"index.json: {r['index']} packs")


def cmd_export(a):
    from . import item_export as ie, unity_export
    if not a.check:   # everything: the race's puppet, then every game item with approved art
        print(unity_export.export(a.race)); cmd_item_export(argparse.Namespace(items=None, race=a.race, size=None))
    problems = ie.check()
    for line in problems or [f"check: ok ({ie.art_root()})"]:
        print(line)
    if problems:
        raise SystemExit(1)


def cmd_dungeon_capture(a):
    from . import dungeon
    for f in dungeon.capture(a.set):
        print(dungeon.CAPTURES / f)


def cmd_backup(a):
    from .backup import backup
    r = backup(a.dest, dry_run=a.dry_run)
    print(f"{'would copy' if a.dry_run else 'copied'} {r['files']} files, {r['bytes'] / 1e6:.0f} MB -> {r['dest']}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="hdart", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)

    so = sub.add_parser("studio", help="the web tools: Character Workshop, Item Workshop, Editor, Dungeon Forge, Limner")
    so.add_argument("--port", type=int, default=4321)
    so.add_argument("--no-browser", action="store_true")
    so.set_defaults(fn=cmd_studio)

    de = sub.add_parser("dungeon-export", help="export a dungeon set's approved pieces to Unity (StreamingAssets)")
    de.add_argument("set", nargs="?", default="crypt")
    de.set_defaults(fn=cmd_dungeon_export)

    pe = sub.add_parser("puppet-export", help="export a race's painted puppet, hair, faces and ramps to Unity (StreamingAssets)")
    pe.add_argument("race", nargs="?", default="human")
    pe.add_argument("--size", type=int, help="canvas px (default: the rig canvas, 576; the paint's 1152 px rest pose scaled to this)")
    pe.set_defaults(fn=cmd_puppet_export)

    ie_ = sub.add_parser("item-export", help="export game items (Item Workshop pieces) as art packs + intake files for new ids")
    ie_.add_argument("items", nargs="*", help="game item ids (default: every one with approved art)")
    ie_.add_argument("--race", default="human"); ie_.add_argument("--size", type=int)
    ie_.set_defaults(fn=cmd_item_export)

    ex = sub.add_parser("export", help="export the puppet and every item to the game; --check only validates what's there")
    ex.add_argument("--check", action="store_true", help="validate Art/ (manifests, files, index maps, index.json) and intake files")
    ex.add_argument("--race", default="human")
    ex.set_defaults(fn=cmd_export)

    dc = sub.add_parser("dungeon-capture", help="export a dungeon set and screenshot Prototype V with it (Unity batch)")
    dc.add_argument("set", nargs="?", default="crypt")
    dc.set_defaults(fn=cmd_dungeon_capture)

    bk = sub.add_parser("backup", help="copy approved art, every tool's state and the specs to a dated folder (config: paths.backups)")
    bk.add_argument("--dest", help="backup root (default: config.toml [paths] backups)")
    bk.add_argument("--dry-run", action="store_true", help="only count what would be copied")
    bk.set_defaults(fn=cmd_backup)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
