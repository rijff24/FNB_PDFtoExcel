import tkinter as tk
from tkinter import filedialog, messagebox

from app.services.extraction_service import ExtractionService
from app.utils.file_helpers import select_pdf_file, select_save_location


def run() -> None:
    """Start the Tkinter desktop app (placeholder UI)."""
    root = tk.Tk()
    root.title("FNB PDF to Excel (scaffold)")

    service = ExtractionService()

    pdf_path_var = tk.StringVar(value="")

    tk.Label(root, text="FNB PDF to Excel", font=("Arial", 14, "bold")).pack(pady=(12, 6))
    tk.Label(root, textvariable=pdf_path_var, wraplength=520, justify="left").pack(pady=(0, 12))

    def on_select_pdf() -> None:
        path = select_pdf_file()
        if path:
            pdf_path_var.set(path)

    def on_extract() -> None:
        pdf_path = pdf_path_var.get().strip()
        if not pdf_path:
            messagebox.showwarning("Missing file", "Please select an FNB statement PDF first.")
            return

        save_path = select_save_location(default_ext=".xlsx")
        if not save_path:
            return

        try:
            # Extraction logic is scaffold-only right now.
            service.extract_to_excel(pdf_path=pdf_path, excel_path=save_path)
        except NotImplementedError:
            messagebox.showinfo("Not implemented", "Extraction is scaffolding-only for now.")

    actions = tk.Frame(root)
    actions.pack(pady=(0, 12))

    tk.Button(actions, text="Select PDF", command=on_select_pdf, width=18).grid(row=0, column=0, padx=6)
    tk.Button(actions, text="Extract", command=on_extract, width=18).grid(row=0, column=1, padx=6)

    tk.Button(root, text="Quit", command=root.destroy).pack(pady=(0, 12))

    root.mainloop()
