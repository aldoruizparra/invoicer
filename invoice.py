#!/usr/bin/env python3
"""Local terminal invoice generator.

Usage:
    python invoice.py new      Create a new invoice
    python invoice.py repeat   New invoice prefilled from the last one
    python invoice.py setup    Create or edit the sender profile and tax rate
    python invoice.py list     View or delete saved contacts, addresses, items
    python invoice.py demo     Write docs/demo_invoice.pdf from fake data

Weekly contractor invoices (fills the client's .xlsx template):
    python invoice.py from-email <file.eml>   Build the weekly invoice from a work-log email
    python invoice.py setup-weekly            Edit contractor info / replace the template
"""

import sys

COMMANDS = ("new", "repeat", "setup", "list", "demo", "from-email", "setup-weekly")


def main(argv):
    command = argv[0] if argv else None
    expected_args = 2 if command == "from-email" else 1
    if command not in COMMANDS or len(argv) != expected_args:
        print(__doc__.strip())
        return 1

    if command == "demo":
        from invoicer.demo import run_demo
        run_demo()
        return 0

    from invoicer import flow, store, weekly
    data = store.load()
    try:
        if command == "setup":
            flow.run_setup(data)
        elif command == "list":
            flow.run_list(data)
        elif command == "setup-weekly":
            weekly.run_setup_weekly(data)
        elif command == "from-email":
            weekly.run_from_email(data, argv[1])
        else:
            run = flow.run_new if command == "new" else flow.run_repeat
            path = run(data)
            print(f"\nInvoice saved: {path}")
    except (KeyboardInterrupt, flow.Cancelled):
        print("\nCancelled. Nothing was saved." if command in ("from-email", "setup-weekly")
              else "\nCancelled. Invoice number not used.")
        return 1
    except (OSError, ValueError) as e:
        print(f"\nError: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
