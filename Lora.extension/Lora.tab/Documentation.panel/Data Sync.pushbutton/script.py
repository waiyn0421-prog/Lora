# -*- coding: utf-8 -*-


# Lora shared extension library (single source of truth)
import os as _lora_os, sys as _lora_sys
_lora_lib = _lora_os.path.abspath(_lora_os.path.join(_lora_os.path.dirname(__file__), "..", "..", "..", "lib"))
if _lora_lib not in _lora_sys.path:
    _lora_sys.path.insert(0, _lora_lib)
from LoraDialogTheme import lora_select_from_list, lora_alert, lora_apply_winforms_theme
from LoraCore import element_id_value, ids_equal, safe_text

"""Lora Schedule Export / Import - Excel workbook round-trip for Revit schedules."""
__title__ = "Data Sync"
__author__ = "Lora"
__doc__ = "Synchronize Revit schedule parameter data with Excel (.xlsx)."

import os, traceback, zipfile, xml.etree.ElementTree as ET, re
import System
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from System.Windows.Forms import (Form, Label, ComboBox, Button, TextBox, RadioButton,
    OpenFileDialog, SaveFileDialog, MessageBox, MessageBoxButtons, MessageBoxIcon,
    ComboBoxStyle, FormBorderStyle, FormStartPosition, DialogResult as WinDialogResult)

try:
    from pyrevit import revit
    doc = revit.doc
    uidoc = revit.uidoc
except:
    doc = __revit__.ActiveUIDocument.Document
    uidoc = __revit__.ActiveUIDocument

ID_COL = "__LORA_UNIQUEID__"
EID_COL = "__LORA_ELEMENTID__"
BASELINE_PREFIX = "__LORA_ORIGINAL__"





def canonical_header(value):
    """Normalize a field heading for resilient matching without changing the workbook."""
    try:
        value = unicode_safe(value).strip().lower()
    except Exception:
        value = str(value).strip().lower()
    return re.sub(r'[^a-z0-9]', '', value)


def unicode_safe(value):
    """Convert values to text safely in both IronPython 2 and Python 3."""
    if value is None:
        return u""
    try:
        return unicode(value)
    except NameError:
        try:
            return str(value)
        except Exception:
            return u""
    except Exception:
        try:
            return str(value)
        except Exception:
            return u""


def notify(msg, error=False):
    lora_alert(msg, title="Lora Data Sync", warn_icon=error)


# Minimal Open XML workbook writer/reader: no Excel installation or third-party package required.
NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def xcol(n):
    out=''
    while n:
        n, rem = divmod(n-1, 26); out=chr(65+rem)+out
    return out

def xml_text(value):
    # XML 1.0 disallows most control characters; strip them to keep .xlsx valid.
    value = unicode_safe(value)
    value = ''.join(ch for ch in value if ord(ch) in (9, 10, 13) or ord(ch) >= 32)
    return value.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def xlsx_write(path, headers, rows, schedule_name):
    allrows=[headers]+rows
    lastcol=xcol(len(headers)); lastrow=len(allrows)
    sheet=['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<worksheet xmlns="%s" xmlns:r="%s">'%(NS_MAIN,NS_REL), '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>', '<cols><col min="1" max="2" width="3" hidden="1"/>']
    visible_end = len(headers)
    for i,h in enumerate(headers[2:],3):
        if unicode_safe(h).startswith(BASELINE_PREFIX):
            sheet.append('<col min="%d" max="%d" width="3" hidden="1"/>'%(i,i))
        else:
            width=max(14,min(34,len(unicode_safe(h))+4))
            sheet.append('<col min="%d" max="%d" width="%d" customWidth="1"/>'%(i,i,width))
    sheet.append('</cols><sheetData>')
    for ri,row in enumerate(allrows,1):
        sheet.append('<row r="%d">'%ri)
        for ci,val in enumerate(row,1):
            ref='%s%d'%(xcol(ci),ri); text=xml_text(val)
            style=' s="1"' if ri==1 else (' s="2"' if ci<=2 else ' s="3"')
            sheet.append('<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'%(ref,style,text))
        sheet.append('</row>')
    sheet.append('</sheetData><autoFilter ref="A1:%s%d"/></worksheet>'%(lastcol,lastrow))
    styles="""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF263746"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD9E2F3"/></left><right style="thin"><color rgb="FFD9E2F3"/></right><top style="thin"><color rgb="FFD9E2F3"/></top><bottom style="thin"><color rgb="FFD9E2F3"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>"""
    content='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    rels='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    safe_name=(unicode_safe(schedule_name)[:31] or 'Schedule').replace(':',' ').replace('\\',' ').replace('/',' ')
    workbook='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>'''%xml_text(safe_name)
    wbrels='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',content); z.writestr('_rels/.rels',rels); z.writestr('xl/workbook.xml',workbook); z.writestr('xl/_rels/workbook.xml.rels',wbrels); z.writestr('xl/worksheets/sheet1.xml',''.join(sheet)); z.writestr('xl/styles.xml',styles)

def xlsx_read(path):
    z=zipfile.ZipFile(path,'r')
    try:
        wb=ET.fromstring(z.read('xl/workbook.xml')); relroot=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relmap={r.attrib.get('Id'):r.attrib.get('Target') for r in relroot}
        first=wb.find('{%s}sheets/{%s}sheet'%(NS_MAIN,NS_MAIN))
        if first is None: raise Exception('The Excel workbook contains no worksheet.')
        worksheet_name=unicode_safe(first.attrib.get('name','')).strip()
        target=relmap.get(first.attrib.get('{%s}id'%NS_REL),'worksheets/sheet1.xml').lstrip('/')
        if not target.startswith('xl/'): target='xl/'+target
        root=ET.fromstring(z.read(target)); shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            sr=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in sr.findall('{%s}si'%NS_MAIN): shared.append(''.join((t.text or '') for t in si.iter('{%s}t'%NS_MAIN)))
        rows=[]
        for rownode in root.findall('.//{%s}sheetData/{%s}row'%(NS_MAIN,NS_MAIN)):
            values={}
            for c in rownode.findall('{%s}c'%NS_MAIN):
                m=re.match(r'([A-Z]+)',c.attrib.get('r',''))
                if not m: continue
                col=0
                for ch in m.group(1): col=col*26+ord(ch)-64
                typ=c.attrib.get('t'); val=''
                if typ=='inlineStr': val=''.join((t.text or '') for t in c.iter('{%s}t'%NS_MAIN))
                else:
                    v=c.find('{%s}v'%NS_MAIN); val=v.text if v is not None and v.text is not None else ''
                    if typ=='s' and val!='': val=shared[int(val)]
                values[col-1]=val
            if values:
                vals=['']*(max(values.keys())+1)
                for k,v in values.items(): vals[k]=v
                rows.append(vals)
        if not rows: raise Exception('The Excel workbook is empty.')
        while rows and not any(unicode_safe(x).strip() for x in rows[-1]): rows.pop()
        headers=[unicode_safe(x).strip() for x in rows[0]]
        if len(set(headers))!=len(headers): raise Exception('The Excel workbook has duplicate column headings. Keep each heading unique.')
        return headers,rows[1:],worksheet_name
    finally: z.close()

def get_schedules():
    """Collect ViewSchedule views robustly across Revit API versions.

    Avoid WhereElementIsNotElementType() and avoid silently dropping a view if
    a property behaves differently in a particular Revit version.
    """
    result=[]
    errors=[]
    try:
        collector = FilteredElementCollector(doc).OfClass(ViewSchedule)
        views = list(collector.ToElements())
    except Exception as ex:
        # Let the caller show the real collector failure instead of a misleading
        # "no eligible schedules" message.
        raise Exception('Could not collect Revit schedules: %s' % unicode_safe(ex))

    for v in views:
        try:
            if v is None:
                continue
            try:
                if v.IsTemplate:
                    continue
            except Exception:
                pass
            # Title block revision schedules are not model element schedules.
            try:
                if v.IsTitleblockRevisionSchedule:
                    continue
            except Exception:
                pass
            # Key schedules do not represent element instances. If the API
            # property is unavailable, keep the schedule and let export validate it.
            try:
                if v.Definition.IsKeySchedule:
                    continue
            except Exception:
                pass
            result.append(v)
        except Exception as ex:
            try:
                errors.append('%s: %s' % (v.Name, unicode_safe(ex)))
            except Exception:
                errors.append(unicode_safe(ex))

    result.sort(key=lambda x: x.Name.lower())
    if not result and views:
        detail='\n'.join(errors[:5]) if errors else 'Schedules were found, but all were filtered out.'
        raise Exception('Revit found %d schedule view(s), but none could be listed.\n%s' % (len(views), detail))
    return result


def choose_schedule(schedules, title):
    form=Form(); form.Text=title; form.Width=560; form.Height=165
    form.FormBorderStyle=FormBorderStyle.FixedDialog; form.StartPosition=FormStartPosition.CenterScreen
    form.MaximizeBox=False; form.MinimizeBox=False
    label=Label(); label.Text='Select a Revit element schedule'; label.Left=16; label.Top=16; label.Width=500
    label.ForeColor=System.Drawing.Color.FromArgb(233,229,242); form.Controls.Add(label)
    combo=ComboBox(); combo.Left=16; combo.Top=42; combo.Width=510; combo.DropDownStyle=ComboBoxStyle.DropDownList; combo.BackColor=System.Drawing.Color.FromArgb(41,35,61); combo.ForeColor=System.Drawing.Color.FromArgb(233,229,242)
    for s in schedules: combo.Items.Add(s.Name)
    if schedules: combo.SelectedIndex=0
    form.Controls.Add(combo)
    ok=Button(); ok.Text='Continue'; ok.Left=330; ok.Top=78; ok.Width=92; ok.DialogResult=WinDialogResult.OK; form.Controls.Add(ok)
    cancel=Button(); cancel.Text='Cancel'; cancel.Left=434; cancel.Top=78; cancel.Width=92; cancel.DialogResult=WinDialogResult.Cancel; form.Controls.Add(cancel)
    form.AcceptButton=ok; form.CancelButton=cancel
    lora_apply_winforms_theme(form)
    if not schedules: return None
    if form.ShowDialog()!=WinDialogResult.OK: return None
    return schedules[combo.SelectedIndex]


def get_fields(schedule):
    definition=schedule.Definition
    fields=[]
    for i in range(definition.GetFieldCount()):
        field=definition.GetField(i)
        try:
            if field.IsHidden: continue
            if field.FieldType in (ScheduleFieldType.Count, ScheduleFieldType.Formula, ScheduleFieldType.Percentage):
                # Include for reference/export; import will skip non-parameter fields.
                pass
            name=field.GetName()
            if not name: name='Field_%d' % (i+1)
            # Keep unique Excel headers when same parameter appears more than once.
            base=name; suffix=2
            while any(x['header']==name for x in fields):
                name='%s (%d)' % (base,suffix); suffix+=1
            pid=field.ParameterId
            bip=None
            try:
                if element_id_value(pid) < 0: bip=BuiltInParameter(element_id_value(pid))
            except: pass
            # Keep several grounded parameter-name candidates. Some shared/project
            # schedule fields cannot be resolved on elements through ParameterId alone.
            lookup_names=[]
            for candidate_name in (field.GetName(), getattr(field, 'ColumnHeading', None)):
                try:
                    candidate_name=unicode_safe(candidate_name).strip()
                    if candidate_name and candidate_name not in lookup_names:
                        lookup_names.append(candidate_name)
                except: pass
            try:
                schedulable=field.GetSchedulableField()
                try:
                    candidate_name=unicode_safe(schedulable.GetName(doc)).strip()
                    if candidate_name and candidate_name not in lookup_names:
                        lookup_names.append(candidate_name)
                except: pass
            except: pass
            fields.append({'header':name,'field':field,'pid':pid,'bip':bip,'type':field.FieldType,'lookup_names':lookup_names})
        except: pass
    return fields


def collect_elements(schedule):
    catid=schedule.Definition.CategoryId
    collector=FilteredElementCollector(doc).WhereElementIsNotElementType()
    try: collector=collector.OfCategoryId(catid)
    except: pass
    elems=[]
    for e in collector:
        try:
            if e.ViewSpecific: continue
        except: pass
        elems.append(e)
    # Apply basic schedule filters where the filter targets a real parameter.
    # If a filter cannot be translated, retain category elements rather than risk data loss.
    try:
        filters=schedule.Definition.GetFilters()
        for sf in filters:
            try:
                pid=sf.FieldId
                field=schedule.Definition.GetField(pid)
                p_id=field.ParameterId
                if element_id_value(p_id) < 0: continue
                rule=None
                val=sf.GetValue()
                op=sf.FilterType
                if op == ScheduleFilterType.Equal:
                    rule=FilterRule.CreateEqualsRule(p_id, str(val), False)
                elif op == ScheduleFilterType.NotEqual:
                    rule=FilterRule.CreateNotEqualsRule(p_id, str(val), False)
                if rule:
                    provider=ParameterValueProvider(p_id)
                    pf=ElementParameterFilter(rule)
                    elems=[e for e in elems if e.get_Parameter(p_id) is not None]
                    elems=[e for e in elems if passes_filter(e,pf)]
            except: pass
    except: pass
    # Deterministic ordering for round-trip review.
    elems.sort(key=lambda e: (get_param_text(e, BuiltInParameter.ALL_MODEL_MARK) if e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK) else '', element_id_value(e.Id)))
    return elems


def passes_filter(element, element_filter):
    try: return element_filter.PassesFilter(doc, element.Id)
    except: return True


def get_param_text(element, key):
    try:
        p=element.get_Parameter(key)
        return parameter_text(p)
    except: return ''


def parameter_text(p):
    if p is None: return ''
    try:
        if p.StorageType == StorageType.String: return p.AsString() or ''
        if p.StorageType == StorageType.Integer: return str(p.AsInteger())
        if p.StorageType == StorageType.Double: return p.AsValueString() or str(p.AsDouble())
        if p.StorageType == StorageType.ElementId:
            eid=p.AsElementId(); el=doc.GetElement(eid)
            return el.Name if el else str(element_id_value(eid))
    except: pass
    try: return p.AsValueString() or ''
    except: return ''


def raw_parameter_value(parameter):
    """Read a parameter's underlying value for post-write verification."""
    if parameter is None:
        return None
    try:
        storage = parameter.StorageType
        if storage == StorageType.String:
            return ("String", parameter.AsString() or "")
        if storage == StorageType.Integer:
            return ("Integer", int(parameter.AsInteger()))
        if storage == StorageType.Double:
            return ("Double", float(parameter.AsDouble()))
        if storage == StorageType.ElementId:
            return ("ElementId", element_id_value(parameter.AsElementId()))
    except Exception:
        return None
    return None


def raw_values_differ(before, after):
    if before is None or after is None:
        return before != after
    if before[0] != after[0]:
        return True
    if before[0] == "Double":
        try:
            return abs(before[1] - after[1]) > 1e-10
        except Exception:
            return before != after
    return before[1] != after[1]


def get_element_parameter(element, field_info):
    """Resolve a schedule parameter by ID first, then by the field's real name.

    Shared/project parameters can fail Element.get_Parameter(ParameterId) on some
    Revit schedule fields. Name lookup is a fallback, checked on both instance and type.
    """
    try:
        pid = field_info['pid']
        bip = field_info.get('bip')
        candidates = [element]
        try:
            type_id = element.GetTypeId()
            if type_id and type_id != ElementId.InvalidElementId:
                type_element = doc.GetElement(type_id)
                if type_element is not None and type_element.Id != element.Id:
                    candidates.append(type_element)
        except:
            pass

        # First pass: prefer writable parameter-ID matches.
        for candidate in candidates:
            p = None
            try:
                if bip is not None:
                    p = candidate.get_Parameter(bip)
            except:
                p = None
            if p is None:
                try:
                    p = candidate.get_Parameter(pid)
                except:
                    p = None
            if p is not None and not p.IsReadOnly:
                return p

        # Second pass: name-based fallback for shared/project parameters.
        for candidate in candidates:
            for param_name in field_info.get('lookup_names', []):
                try:
                    p = candidate.LookupParameter(param_name)
                    if p is not None and not p.IsReadOnly:
                        return p
                except:
                    pass

        # Return any matched read-only parameter so caller can report it as skipped.
        for candidate in candidates:
            try:
                p = candidate.get_Parameter(bip) if bip is not None else None
                if p is None:
                    p = candidate.get_Parameter(pid)
                if p is not None:
                    return p
            except:
                pass
            for param_name in field_info.get('lookup_names', []):
                try:
                    p = candidate.LookupParameter(param_name)
                    if p is not None:
                        return p
                except:
                    pass
        return None
    except:
        return None


def export_schedule(schedule):
    fields=get_fields(schedule)
    if not fields: raise Exception('No visible schedule fields were found.')
    elems=collect_elements(schedule)
    # Visible parameter columns are followed by hidden baseline columns. During
    # import, the baseline lets Lora write only cells that the user actually changed.
    headers=[ID_COL,EID_COL]+[f['header'] for f in fields]+[BASELINE_PREFIX+f['header'] for f in fields]
    rows=[]
    for e in elems:
        row=[e.UniqueId,str(element_id_value(e.Id))]
        original_values=[]
        for f in fields:
            p=get_element_parameter(e,f)
            original_values.append(parameter_text(p))
        row.extend(original_values)
        row.extend(original_values)
        rows.append(row)
    dlg=SaveFileDialog(); dlg.Title='Export Revit schedule to Excel workbook'
    dlg.Filter='Excel Workbook (*.xlsx)|*.xlsx'; dlg.DefaultExt='xlsx'; dlg.AddExtension=True; dlg.FileName=schedule.Name.replace('\\','_').replace('/','_')+'.xlsx'
    if dlg.ShowDialog()!=WinDialogResult.OK: return
    export_path = dlg.FileName
    try:
        xlsx_write(export_path,headers,rows,schedule.Name)
    except Exception as write_error:
        # Excel commonly locks an existing workbook. Preserve it and retry with a
        # unique sibling filename rather than failing the whole export operation.
        message = unicode_safe(write_error).lower()
        locked = ('being used by another process' in message or
                  'cannot access the file' in message or
                  'access is denied' in message or
                  'permission denied' in message or
                  'used by another process' in message or
                  'errno 32' in message or 'error 32' in message)
        if not locked:
            raise
        import datetime
        folder = os.path.dirname(export_path)
        stem = os.path.splitext(os.path.basename(export_path))[0]
        suffix = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        export_path = os.path.join(folder, stem + '_DataSync_' + suffix + '.xlsx')
        xlsx_write(export_path,headers,rows,schedule.Name)
        notify('The selected workbook is currently open or locked by another application.\n\nData Sync saved a new copy instead:\n%s\n\nClose the workbook in Excel before overwriting the original next time.' % export_path)
        return
    notify('Data exported successfully as an Excel workbook (.xlsx).\n\nSchedule: %s\nElements: %d\nFields: %d\n\nSaved to: %s\n\nThe workbook includes hidden matching IDs and original-value baselines. Keep hidden columns and headings unchanged when importing.' % (schedule.Name,len(rows),len(fields),export_path))


def is_yes_no_parameter(parameter):
    """Detect Yes/No parameters across legacy and ForgeTypeId APIs."""
    try:
        data_type = parameter.Definition.GetDataType()
        return data_type == SpecTypeId.Boolean.YesNo
    except Exception:
        try:
            return parameter.Definition.ParameterType == ParameterType.YesNo
        except Exception:
            return False


def import_schedule(schedule):
    dlg=OpenFileDialog(); dlg.Title='Select edited Excel schedule workbook'; dlg.Filter='Excel Workbook (*.xlsx)|*.xlsx'; dlg.Multiselect=False
    if dlg.ShowDialog()!=WinDialogResult.OK: return
    headers,rows,worksheet_name=xlsx_read(dlg.FileName)
    if ID_COL not in headers:
        raise Exception('This workbook is missing the required %s column. Please use a workbook exported by Lora Data Sync and keep its hidden identifier columns.' % ID_COL)
    id_index=headers.index(ID_COL)

    # Resolve schedule deterministically: prefer exact worksheet-name match. If the
    # workbook name does not match a schedule, use the schedule the user selected in
    # the Data Sync dialog rather than guessing another schedule from weak overlap.
    all_schedules = get_schedules()
    worksheet_key = unicode_safe(worksheet_name).strip().lower()
    exact = [sc for sc in all_schedules
             if unicode_safe(sc.Name).strip().lower() == worksheet_key
             or unicode_safe(sc.Name)[:31].strip().lower() == worksheet_key]
    if exact:
        schedule = exact[0]
    schedule_fields = get_fields(schedule)
    fields_by_header = {f['header']: f for f in schedule_fields}
    fields_by_canonical = {}
    for f in schedule_fields:
        fields_by_canonical[canonical_header(f['header'])] = f

    # Workbook layout is [UniqueId, ElementId, visible fields..., baselines...].
    # Preserve the exact visible-column index and pair each field with its baseline.
    baseline_indices = {}
    visible_indices = {}
    for idx, h in enumerate(headers):
        if h.startswith(BASELINE_PREFIX):
            baseline_indices[h[len(BASELINE_PREFIX):]] = idx
        elif h not in (ID_COL, EID_COL):
            visible_indices[h] = idx

    # Compatibility for older workbook writers whose baseline headings were altered:
    # pair the visible and baseline blocks by their exported positions.
    if not baseline_indices:
        payload_count = len(headers) - 2
        if payload_count > 0 and payload_count % 2 == 0:
            n = payload_count // 2
            for i in range(n):
                visible_header = unicode_safe(headers[2 + i]).strip()
                if visible_header:
                    baseline_indices[visible_header] = 2 + n + i

    import_fields = []
    for original_header, baseline_idx in baseline_indices.items():
        field_info = fields_by_header.get(original_header)
        if field_info is None:
            field_info = fields_by_canonical.get(canonical_header(original_header))
        visible_idx = visible_indices.get(original_header)
        if visible_idx is None:
            canonical = canonical_header(original_header)
            for visible_header, candidate_idx in visible_indices.items():
                if canonical_header(visible_header) == canonical:
                    visible_idx = candidate_idx
                    break
        if field_info is not None and visible_idx is not None and baseline_idx < len(headers):
            import_fields.append((visible_idx, original_header, field_info, baseline_idx))

    if not import_fields:
        workbook_headers = [h for h in headers if h not in (ID_COL, EID_COL) and not h.startswith(BASELINE_PREFIX)]
        available_headers = [f['header'] for f in schedule_fields]
        raise Exception('No importable columns matched the target schedule.\nWorksheet: %s\nTarget schedule: %s\nWorkbook columns: %s\nSchedule columns: %s\n\nSelect the matching schedule in Data Sync and use a fresh workbook exported by this version.' % (worksheet_name, schedule.Name, ', '.join(workbook_headers[:12]), ', '.join(available_headers[:12])))

    # Require valid baseline cells to guarantee changed-only behavior.
    import_fields = [item for item in import_fields if item[3] is not None and item[3] < len(headers)]
    if not import_fields:
        raise Exception('No usable original-value baselines were found. Export a fresh workbook before importing.')

    lookup={}
    for e in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try: lookup[e.UniqueId]=e
        except: pass
    changed=0; unchanged=0; skipped=0; matched_rows=0; matched_parameters=0; errors=[]
    tx=Transaction(doc,'Lora Schedule Import')
    tx.Start()
    try:
        for rownum,row in enumerate(rows,2):
            while len(row)<len(headers): row.append('')
            uid=row[id_index].strip()
            element=lookup.get(uid)
            if element is None:
                skipped+=1; continue
            matched_rows += 1
            for idx,header,fi,baseline_idx in import_fields:
                p=get_element_parameter(element,fi)
                if p is None or p.IsReadOnly:
                    skipped += 1
                    continue
                matched_parameters += 1
                before_value = raw_parameter_value(p)
                value=unicode_safe(row[idx])
                baseline=unicode_safe(row[baseline_idx]) if baseline_idx is not None and baseline_idx < len(row) else ''
                if value.strip() == baseline.strip():
                    unchanged += 1
                    continue
                try:
                    if p.StorageType==StorageType.String:
                        # Blank cells are treated as no-op to avoid accidental clearing.
                        if value == '':
                            skipped += 1
                            continue
                        setter_result = p.Set(value)
                        if setter_result is False:
                            errors.append("Row %d, %s: Revit rejected the string value; no successful write was recorded." % (rownum,fi['header']))
                            continue
                    elif p.StorageType==StorageType.Integer:
                        if value.strip()=='': continue
                        if is_yes_no_parameter(p):
                            v=value.strip().lower()
                            setter_result = p.Set(1 if v in ('1','yes','true','y') else 0 if v in ('0','no','false','n') else int(value))
                        else:
                            setter_result = p.Set(int(value))
                        if setter_result is False:
                            errors.append("Row %d, %s: Revit rejected the integer value; no successful write was recorded." % (rownum,fi['header']))
                            continue
                    elif p.StorageType==StorageType.Double:
                        if value.strip()=='': continue
                        # Parse using the Revit project's display units. Revit stores doubles
                        # internally, so do not pass raw Excel floats directly to Parameter.Set.
                        # UnitFormatUtils converts the project's displayed value to Revit internal units.
                        try:
                            # SetValueString parses exactly as Revit's Properties palette
                            # does, using the parameter's display format/project units.
                            # Fall back to UnitFormatUtils for API versions where needed.
                            applied = False
                            try:
                                applied = bool(p.SetValueString(value))
                            except:
                                applied = False
                            if not applied:
                                from Autodesk.Revit.DB import UnitFormatUtils
                                out_value = clr.Reference[System.Double]()
                                try:
                                    spec_id = p.Definition.GetDataType()
                                    parsed = UnitFormatUtils.TryParse(doc.GetUnits(), spec_id, value, out_value)
                                except:
                                    parsed = False
                                if parsed:
                                    p.Set(out_value.Value)
                                    applied = True
                            if not applied:
                                errors.append("Row %d, %s: Revit could not apply '%s' using project units. No change was made." % (rownum,fi['header'],value))
                                continue
                        except Exception as parse_ex:
                            errors.append('Row %d, %s: unit parsing failed: %s' % (rownum,fi['header'],str(parse_ex)))
                            continue
                    elif p.StorageType==StorageType.ElementId:
                        # ElementId values are intentionally not guessed from display names.
                        skipped+=1
                        continue
                    after_value = raw_parameter_value(p)
                    if raw_values_differ(before_value, after_value):
                        changed += 1
                    else:
                        unchanged += 1
                except Exception as ex:
                    errors.append('Row %d, %s: %s' % (rownum,fi['header'],str(ex)))
        tx.Commit()
    except:
        tx.RollBack(); raise
    message='Import completed.\n\nSchedule: %s\nWorkbook worksheet: %s\nRows matched to Revit elements: %d / %d\nParameters matched: %d\nChanged parameter writes: %d\nUnchanged cells ignored: %d\nSkipped rows/parameters: %d' % (schedule.Name,worksheet_name,matched_rows,len(rows),matched_parameters,changed,unchanged,skipped)
    if changed == 0 and not errors:
        message += '\n\nNo values were written. Check that you edited a visible parameter cell, saved the workbook, and that the edited field is writable. Data Sync now checks both instance and type parameters; formula, calculated, read-only, and unsupported fields are skipped.'
    if errors: message+='\n\nIssues (first 10):\n'+'\n'.join(errors[:10])
    notify(message, bool(errors))


def main():
    if doc.IsFamilyDocument:
        notify('Open a Revit project (.rvt), not a family document.',True); return
    schedules=get_schedules()
    if not schedules:
        notify('No eligible element schedules were found in this project.\n\nPlease check that the project contains Schedule/Quantities views. If it does, this diagnostic build will report the collector count or API error instead of silently skipping schedules.',True); return
    form=Form(); form.Text='Lora | Schedule Link'; form.Width=620; form.Height=300
    form.FormBorderStyle=FormBorderStyle.FixedDialog; form.StartPosition=FormStartPosition.CenterScreen
    form.MaximizeBox=False; form.MinimizeBox=False; form.BackColor=System.Drawing.Color.FromArgb(23,21,31)
    title=Label(); title.Text='DATA SYNC'; title.Left=22; title.Top=18; title.Width=550; title.Height=30
    title.Font=System.Drawing.Font('Segoe UI', 15, System.Drawing.FontStyle.Bold); title.ForeColor=System.Drawing.Color.FromArgb(177,140,255); form.Controls.Add(title)
    subtitle=Label(); subtitle.Text='Transfer schedule data between Revit and Microsoft Excel (.xlsx).'; subtitle.Left=24; subtitle.Top=54; subtitle.Width=550; subtitle.Height=24
    subtitle.Font=System.Drawing.Font('Segoe UI', 9); subtitle.ForeColor=System.Drawing.Color.FromArgb(201,193,220); form.Controls.Add(subtitle)
    line=Label(); line.Left=24; line.Top=84; line.Width=555; line.Height=2; line.BackColor=System.Drawing.Color.FromArgb(72,62,91); form.Controls.Add(line)
    label=Label(); label.Text='Revit schedule'; label.Left=24; label.Top=103; label.Width=180; label.Height=22
    label.Font=System.Drawing.Font('Segoe UI', 9, System.Drawing.FontStyle.Bold); label.ForeColor=System.Drawing.Color.FromArgb(233,229,242); form.Controls.Add(label)
    combo=ComboBox(); combo.Left=24; combo.Top=128; combo.Width=555; combo.Height=28; combo.DropDownStyle=ComboBoxStyle.DropDownList; combo.BackColor=System.Drawing.Color.FromArgb(41,35,61); combo.ForeColor=System.Drawing.Color.FromArgb(233,229,242)
    for sc in schedules: combo.Items.Add(sc.Name)
    if schedules: combo.SelectedIndex=0
    form.Controls.Add(combo)
    exp=Button(); exp.Text='Export to Excel'; exp.Left=24; exp.Top=178; exp.Width=170; exp.Height=42
    exp.BackColor=System.Drawing.Color.FromArgb(41,35,61); exp.ForeColor=System.Drawing.Color.White; exp.FlatStyle=System.Windows.Forms.FlatStyle.Flat
    exp.Font=System.Drawing.Font('Segoe UI', 9, System.Drawing.FontStyle.Bold); form.Controls.Add(exp)
    imp=Button(); imp.Text='Import from Excel'; imp.Left=207; imp.Top=178; imp.Width=170; imp.Height=42
    imp.BackColor=System.Drawing.Color.FromArgb(99,75,160); imp.ForeColor=System.Drawing.Color.White; imp.FlatStyle=System.Windows.Forms.FlatStyle.Flat
    imp.Font=System.Drawing.Font('Segoe UI', 9, System.Drawing.FontStyle.Bold); form.Controls.Add(imp)
    cancel=Button(); cancel.Text='Close'; cancel.Left=489; cancel.Top=178; cancel.Width=90; cancel.Height=42
    cancel.Font=System.Drawing.Font('Segoe UI', 9); cancel.BackColor=System.Drawing.Color.FromArgb(53,47,68); cancel.ForeColor=System.Drawing.Color.FromArgb(233,229,242); cancel.FlatStyle=System.Windows.Forms.FlatStyle.Flat; form.Controls.Add(cancel)
    note=Label(); note.Text='Excel workbook includes filters, frozen headers and protected matching IDs. Keep ID columns and headings unchanged.'; note.Left=24; note.Top=236; note.Width=555; note.Height=32
    note.Font=System.Drawing.Font('Segoe UI', 8); note.ForeColor=System.Drawing.Color.FromArgb(201,193,220); note.ForeColor=System.Drawing.Color.FromArgb(201,193,220); form.Controls.Add(note)
    chosen={'action':None}
    exp.Click += lambda sender,args: (chosen.update({'action':'export'}), form.Close())
    imp.Click += lambda sender,args: (chosen.update({'action':'import'}), form.Close())
    cancel.Click += lambda sender,args: form.Close()
    lora_apply_winforms_theme(form)
    if form.ShowDialog()!=WinDialogResult.OK and chosen['action'] is None:
        # Close button and window close both return Cancel; continue only after an action button.
        pass
    if chosen['action'] is None: return
    schedule=schedules[combo.SelectedIndex]
    if chosen['action']=='export': export_schedule(schedule)
    else: import_schedule(schedule)

try:
    main()
except Exception as ex:
    notify('Schedule operation failed.\n\n%s\n\n%s' % (str(ex),traceback.format_exc()),True)
