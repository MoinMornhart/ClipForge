import sys


def run() -> int:
    if len(sys.argv) > 1:
        from clipforge.cli import main

        return main()
    from clipforge.ui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(run())
