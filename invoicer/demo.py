"""Generate docs/demo_invoice.pdf (and a PNG preview) from obviously fake data.

Never reads or writes the real data file.
"""

from pathlib import Path

from .pdf import render_invoice

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

DEMO_SENDER = {
    "name": "Jane Doe Cleaning",
    "phone": "(555) 555-0100",
    "email": "jane@example.com",
    "street": "123 Example Street",
    "city_state_zip": "Springfield, TX 75000",
    "payable_to": "Jane Doe Cleaning",
    "footer_text": "Materials and labors included in this invoice.",
}

DEMO_INVOICE = {
    "number": 1001,
    "date": "01/15/2026",
    "bill_to": {
        "attention": "John Sample",
        "attention2": "Mary Placeholder",
        "company": "Example Property Management LLC",
        "street": "456 Demo Avenue, Suite 200",
        "city_state": "Springfield, TX",
        "zip": "75000",
    },
    "job_address": "789 Fictional Lane, Unit 4B\nSpringfield, TX 75001",
    "items": [
        {"description": "Deep clean", "price": "250.00"},
        {"description": "Carpet shampoo - 3 rooms", "price": "180.00"},
        {"description": "Window washing (interior and exterior)", "price": "95.50"},
    ],
    "tax_rate": "8.25",
}


def run_demo():
    DOCS_DIR.mkdir(exist_ok=True)
    pdf_path = DOCS_DIR / "demo_invoice.pdf"
    render_invoice(DEMO_SENDER, DEMO_INVOICE, pdf_path)
    print(f"Wrote {pdf_path}")
    try:
        import pymupdf  # PyMuPDF, optional: only needed for the README preview image
    except ImportError:
        print("Skipped PNG preview (pip install pymupdf to enable).")
        return
    png_path = DOCS_DIR / "demo_invoice.png"
    with pymupdf.open(pdf_path) as doc:
        doc[0].get_pixmap(dpi=110).save(png_path)
    print(f"Wrote {png_path}")
