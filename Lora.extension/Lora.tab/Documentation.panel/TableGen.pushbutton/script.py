# -*- coding: utf-8 -*-


# Lora shared extension library (single source of truth)
import os as _lora_os, sys as _lora_sys
_lora_lib = _lora_os.path.abspath(_lora_os.path.join(_lora_os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lora_lib not in _lora_sys.path:
    _lora_sys.path.insert(0, _lora_lib)
from LoraDialogTheme import lora_select_from_list, lora_alert, lora_apply_winforms_theme
from LoraCore import element_id_value, ids_equal, safe_text

"""Lora Table Generator - import arbitrary CSV rows into a Revit Legend or Drafting View."""
__title__ = "Table Generator"
__author__ = "Lora"
__doc__ = "Import a CSV file as a dynamic text-and-grid table in a Legend or Drafting View."

import os
import traceback
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Core")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from System import Array, Byte
from System.IO import StreamReader
from System.Text import Encoding
from System.Windows.Forms import (
    OpenFileDialog, DialogResult, MessageBox, MessageBoxButtons,
    MessageBoxIcon, Form, Label, ComboBox, Button, TextBox,
    FormBorderStyle, FormStartPosition, ComboBoxStyle,
    DialogResult as WinDialogResult
)

try:
    from pyrevit import revit
    doc = revit.doc
    uidoc = revit.uidoc
except:
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

# Drawing defaults only; data columns/headers are never predefined.
TEXT_SIZE_MM = 2.0
ROW_HEIGHT_MM = 8.0
MIN_COL_WIDTH_MM = 22.0
MAX_COL_WIDTH_MM = 100.0
CHAR_WIDTH_MM = 1.65
LEFT_MARGIN_MM = 5.0
TOP_MARGIN_MM = 5.0


def mm(value):
    return float(value) / 304.8


def notify(message, error=False):
    lora_alert(message, title="Lora Table Generator", warn_icon=error)


def parse_csv_text(text):
    """CSV parser supporting commas, escaped quotes, CR/LF and quoted newlines."""
    rows, row, cell = [], [], []
    in_quotes = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < len(text) and text[i + 1] == '"':
                    cell.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                cell.append(ch)
        else:
            if ch == '"' and not cell:
                in_quotes = True
            elif ch == ',':
                row.append(''.join(cell))
                cell = []
            elif ch == '\n' or ch == '\r':
                row.append(''.join(cell))
                cell = []
                if row and not (len(row) == 1 and row[0] == ''):
                    rows.append(row)
                row = []
                if ch == '\r' and i + 1 < len(text) and text[i + 1] == '\n':
                    i += 1
            else:
                cell.append(ch)
        i += 1
    if in_quotes:
        raise Exception("The CSV contains an unclosed quoted field.")
    if cell or row:
        row.append(''.join(cell))
        if row and not (len(row) == 1 and row[0] == ''):
            rows.append(row)
    if not rows:
        raise Exception("The selected CSV file is empty.")
    # Preserve blank cells and normalize ragged rows to the maximum column count.
    col_count = max([len(r) for r in rows])
    for r in rows:
        while len(r) < col_count:
            r.append('')
    return rows


def read_csv(path):
    reader = StreamReader(path, True)
    try:
        text = reader.ReadToEnd()
    finally:
        reader.Close()
    if text and text[0] == u'\ufeff':
        text = text[1:]
    return parse_csv_text(text)


def select_file():
    dlg = OpenFileDialog()
    dlg.Title = "Select CSV file to import"
    dlg.Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*"
    dlg.Multiselect = False
    if dlg.ShowDialog() == DialogResult.OK:
        return dlg.FileName
    return None


def get_settings(default_title):
    form = Form()
    form.Text = "Lora Table Generator"
    form.Width = 390
    form.Height = 205
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    form.MaximizeBox = False
    form.MinimizeBox = False

    label1 = Label()
    label1.Text = "Create the imported CSV table in:"
    label1.Left, label1.Top, label1.Width = 16, 16, 340
    form.Controls.Add(label1)

    combo = ComboBox()
    combo.Left, combo.Top, combo.Width = 16, 40, 340
    combo.DropDownStyle = ComboBoxStyle.DropDownList
    combo.Items.Add("Legend View")
    combo.Items.Add("Drafting View")
    combo.SelectedIndex = 0
    form.Controls.Add(combo)

    label2 = Label()
    label2.Text = "View name:"
    label2.Left, label2.Top, label2.Width = 16, 72, 340
    form.Controls.Add(label2)

    title_box = TextBox()
    title_box.Left, title_box.Top, title_box.Width = 16, 94, 340
    title_box.Text = default_title
    form.Controls.Add(title_box)

    ok = Button()
    ok.Text = "Create Table"
    ok.Left, ok.Top, ok.Width = 166, 130, 90
    ok.DialogResult = WinDialogResult.OK
    form.Controls.Add(ok)
    cancel = Button()
    cancel.Text = "Cancel"
    cancel.Left, cancel.Top, cancel.Width = 262, 130, 90
    cancel.DialogResult = WinDialogResult.Cancel
    form.Controls.Add(cancel)
    form.AcceptButton = ok
    form.CancelButton = cancel
    lora_apply_winforms_theme(form)
    if form.ShowDialog() != WinDialogResult.OK:
        return None, None
    title = str(title_box.Text).strip() or default_title
    return str(combo.SelectedItem), title


def find_text_type():
    types = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    if not types:
        return None
    target = mm(TEXT_SIZE_MM)
    for typ in types:
        try:
            size = typ.get_Parameter(BuiltInParameter.TEXT_SIZE).AsDouble()
            if abs(size - target) <= mm(0.5):
                return typ
        except:
            pass
    return types[0]


def find_drafting_type():
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if vft.ViewFamily == ViewFamily.Drafting:
            return vft
    return None


def unique_view_name(title):
    names = set()
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            if not v.IsTemplate:
                names.add(v.Name.lower())
        except:
            pass
    name = title
    index = 2
    while name.lower() in names:
        name = "%s (%d)" % (title, index)
        index += 1
    return name


def make_view(kind, title):
    if kind == "Drafting View":
        vft = find_drafting_type()
        if vft is None:
            raise Exception("No Drafting View type was found in this project.")
        view = ViewDrafting.Create(doc, vft.Id)
    else:
        source = None
        for candidate in FilteredElementCollector(doc).OfClass(View):
            try:
                if candidate.ViewType == ViewType.Legend and not candidate.IsTemplate:
                    source = candidate
                    break
            except:
                pass
        if source is None:
            raise Exception("This Revit project has no Legend View to duplicate. Create one manually (View > Legends > Legend), then run Table Generator again.")
        new_id = source.Duplicate(ViewDuplicateOption.Duplicate)
        view = doc.GetElement(new_id)
        if view is None:
            raise Exception("Revit could not duplicate the source Legend View.")
        # Rename immediately while the duplicated view is known to be valid.
        # Revit can invalidate the duplicated view when deleting a broad set of
        # view-specific elements collected from it, so do not bulk-delete the
        # duplicate's contents here. The source Legend View should be a blank
        # or suitable template Legend View.
        view.Name = unique_view_name(title)
    try:
        view.Scale = 1
    except:
        pass
    return view


def column_widths(rows):
    count = len(rows[0])
    widths = []
    for c in range(count):
        longest = 0
        for r in rows:
            try:
                length = len(r[c])
            except:
                length = 0
            if length > longest:
                longest = length
        # This controls layout only; it does not alter imported cell data.
        width_mm = max(MIN_COL_WIDTH_MM, min(MAX_COL_WIDTH_MM, longest * CHAR_WIDTH_MM + 6.0))
        widths.append(mm(width_mm))
    return widths


def create_text(view, text_type, value, x, y):
    if value is None or str(value) == '':
        return
    options = TextNoteOptions(text_type.Id)
    options.HorizontalAlignment = HorizontalTextAlignment.Left
    TextNote.Create(doc, view.Id, XYZ(x + mm(1.5), y - mm(2.0), 0), str(value), options)


def create_line(view, x1, y1, x2, y2):
    line = Line.CreateBound(XYZ(x1, y1, 0), XYZ(x2, y2, 0))
    doc.Create.NewDetailCurve(view, line)


def build_table(view, rows, text_type):
    widths = column_widths(rows)
    row_h = mm(ROW_HEIGHT_MM)
    left = mm(LEFT_MARGIN_MM)
    top = -mm(TOP_MARGIN_MM)
    x_positions = [left]
    for width in widths:
        x_positions.append(x_positions[-1] + width)
    bottom = top - row_h * len(rows)

    # Horizontal grid lines across the entire CSV data region.
    for r in range(len(rows) + 1):
        y = top - r * row_h
        create_line(view, x_positions[0], y, x_positions[-1], y)
    # Vertical grid lines across all rows.
    for x in x_positions:
        create_line(view, x, top, x, bottom)
    # Import every CSV cell as-is. No header assumptions, no predefined columns.
    for r, row in enumerate(rows):
        y = top - r * row_h
        for c, value in enumerate(row):
            create_text(view, text_type, value, x_positions[c], y)


def main():
    if doc.IsFamilyDocument:
        notify("Open a Revit project (.rvt), not a family document.", True)
        return
    path = select_file()
    if not path:
        return
    try:
        rows = read_csv(path)
    except Exception as ex:
        notify("Could not read CSV.\n\n%s" % str(ex), True)
        return
    default_title = os.path.splitext(os.path.basename(path))[0]
    kind, title = get_settings(default_title)
    if not kind:
        return
    text_type = find_text_type()
    if text_type is None:
        notify("No TextNoteType was found in this project.", True)
        return
    try:
        try:
            from pyrevit import revit
            transaction = revit.Transaction("Lora Table Generator - Import CSV")
        except:
            transaction = Transaction(doc, "Lora Table Generator - Import CSV")
        with transaction:
            view = make_view(kind, title)
            build_table(view, rows, text_type)
            doc.Regenerate()
        try:
            uidoc.ActiveView = view
        except:
            pass
        notify("CSV table created successfully.\n\nView: %s\nRows: %d\nColumns: %d\nSource: %s\n\nThe table uses Revit text notes and detail-line grid elements." % (view.Name, len(rows), len(rows[0]), os.path.basename(path)))
    except Exception as ex:
        notify("Table generation failed.\n\n%s\n\n%s" % (str(ex), traceback.format_exc()), True)

main()
