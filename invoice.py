#!/usr/bin/env python3
"""Local terminal invoice generator.

Usage:
    python invoice.py new      Create a new invoice
    python invoice.py repeat   New invoice prefilled from the last one
    python invoice.py setup    Create or edit the sender profile and tax rate
    python invoice.py list     View or delete saved contacts, addresses, items
    python invoice.py demo     Write docs/demo_invoice.pdf from fake data
"""

import sys

COMMANDS = ("new", "repeat", "setup", "list", "demo")


def main(argv):
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 1
    command = argv[0]

    if command == "demo":
        from invoicer.demo import run_demo
        run_demo()
        return 0

    from invoicer import flow, store
    data = store.load()
    try:
        if command == "setup":
            flow.run_setup(data)
        elif command == "list":
            flow.run_list(data)
        else:
            run = flow.run_new if command == "new" else flow.run_repeat
            path = run(data)
            print(f"\nInvoice saved: {path}")
    except (KeyboardInterrupt, flow.Cancelled):
        print("\nCancelled. Invoice number not used.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
