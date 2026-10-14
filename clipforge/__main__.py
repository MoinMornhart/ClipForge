import sys


def run() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--gui":  # vom Starter ClipForge.exe, optional mit Datei/Link
        from clipforge.ui.app import main as gui_main

        return gui_main(initial=args[1] if len(args) > 1 else None)
    if args:
        from clipforge.cli import main

        return main()
    from clipforge.ui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(run())
