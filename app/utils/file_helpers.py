from tkinter import Tk
from tkinter import filedialog


def select_pdf_file() -> str:
    """Open a file picker for a PDF statement."""
    root = Tk()
    root.withdraw()  # Hide the extra root window.

    path = filedialog.askopenfilename(
        title="Select FNB PDF statement",
        filetypes=[("PDF files", "*.pdf")],
    )

    root.destroy()
    return path


def select_save_location(default_ext: str = ".xlsx") -> str:
    """Open a save dialog and return the chosen output path."""
    root = Tk()
    root.withdraw()

    path = filedialog.asksaveasfilename(
        title="Save Excel file",
        defaultextension=default_ext,
        filetypes=[("Excel files", "*.xlsx")],
    )

    root.destroy()
    return path
