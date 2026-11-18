# -*- coding: utf-8 -*-
"""Template-based native Revit family generation for Lora Tag Studio.

Important Revit API limitation: the public API does not expose a supported general
method to create arbitrary Label elements and define their label-field composition
from scratch. This engine therefore starts from a valid, category-appropriate RFT
(or a prepared seed RFA copied to RFA), preserves its native label/graphic content,
adds compatible shared family parameters, and saves/loads a real family. Dynamic
label composition requires prepared label-slot templates or a future UI-assisted
label authoring workflow.
"""
import os
import re
from pyrevit import DB

try:
    from System import Guid
except Exception:
    Guid = None


class FamilyGenerationError(Exception):
    pass


def _safe_name(value):
    value = re.sub(r'[^A-Za-z0-9 _.-]+', '_', str(value or '').strip())
    value = re.sub(r'\s+', ' ', value).strip(' .')
    if not value:
        raise FamilyGenerationError('Family name is empty or invalid.')
    return value


def _family_parameter_by_guid(family_manager, guid):
    if not guid:
        return None
    try:
        g = Guid(str(guid)) if Guid else guid
        return family_manager.get_Parameter(g)
    except Exception:
        return None


def _family_parameter_by_name(family_manager, name):
    try:
        for fp in family_manager.Parameters:
            try:
                if str(fp.Definition.Name).lower() == str(name).lower():
                    return fp
            except Exception:
                pass
    except Exception:
        pass
    return None


def _definition_guid(definition):
    try:
        return str(definition.GUID).lower()
    except Exception:
        return None


def _add_shared_parameter(family_doc, external_definition, group_id, is_instance):
    fm = family_doc.FamilyManager
    name = str(external_definition.Name)
    guid = _definition_guid(external_definition)
    existing = _family_parameter_by_guid(fm, guid) if guid else None
    if existing is None:
        existing = _family_parameter_by_name(fm, name)
    if existing is not None:
        try:
            existing_guid = str(existing.GUID).lower()
        except Exception:
            existing_guid = None
        if guid and existing_guid and guid != existing_guid:
            raise FamilyGenerationError(
                "The family already contains '{}' with a different shared-parameter identity. "
                "No existing parameter was replaced.".format(name))
        return existing, 'reused family parameter'
    try:
        parameter = fm.AddParameter(external_definition, group_id, bool(is_instance))
        return parameter, 'added shared family parameter'
    except Exception as ex:
        raise FamilyGenerationError(
            "Could not add shared parameter '{}' to the family. Revit reported: {}".format(name, ex))


def create_family_from_template(app, template_path, output_path, family_name,
                                parameter_specs, expected_category_name=None):
    """Create a real RFA based on an RFT, add shared family parameters, and save.

    This function does not create new label graphics. The source template's labels
    and graphics are preserved. Call only from a valid Revit API context.
    """
    if not template_path or not os.path.isfile(template_path):
        raise FamilyGenerationError('Choose a valid Revit family template (.rft).')
    if os.path.splitext(template_path)[1].lower() not in ('.rft', '.rfa'):
        raise FamilyGenerationError('Template source must be a Revit .rft or seed .rfa file.')
    output_path = os.path.abspath(output_path)
    if os.path.splitext(output_path)[1].lower() != '.rfa':
        output_path += '.rfa'
    if os.path.exists(output_path):
        raise FamilyGenerationError('Output already exists. Choose a new filename; the generator will not overwrite it.')
    folder = os.path.dirname(output_path)
    if not os.path.isdir(folder):
        raise FamilyGenerationError('Output folder does not exist: {}'.format(folder))

    family_doc = None
    report = {'family_name': _safe_name(family_name), 'output_path': output_path,
              'template_path': template_path, 'family_category': None,
              'parameters_added': [], 'parameters_reused': [], 'warnings': []}
    try:
        if os.path.splitext(template_path)[1].lower() == '.rft':
            family_doc = app.NewFamilyDocument(template_path)
        else:
            # A seed RFA is not a template input for NewFamilyDocument. It must be
            # opened through the family editor using OpenDocumentFile instead.
            family_doc = app.OpenDocumentFile(template_path)
            if not family_doc.IsFamilyDocument:
                raise FamilyGenerationError('The selected .rfa is not a family document.')
            # Never modify the user's original seed file in-place.
            seed_copy = output_path + '.seed-copy.rfa'
            try:
                from System.IO import File
                File.Copy(template_path, seed_copy, False)
                family_doc.Close(False)
                family_doc = app.OpenDocumentFile(seed_copy)
                report['warnings'].append('Seed RFA copied to a working file. Confirm category and content before use.')
            except Exception as ex:
                raise FamilyGenerationError('Could not create a safe working copy of the seed RFA: {}'.format(ex))

        if family_doc is None or not family_doc.IsFamilyDocument:
            raise FamilyGenerationError('Revit did not open a valid family document.')
        try:
            report['family_category'] = family_doc.OwnerFamily.FamilyCategory.Name
        except Exception:
            report['family_category'] = 'Unknown'
        if expected_category_name and report['family_category']:
            if str(expected_category_name).lower() not in str(report['family_category']).lower():
                report['warnings'].append(
                    "Template family category is '{}'; expected a category related to '{}'. "
                    "Please verify the template is the correct tag-family type.".format(
                        report['family_category'], expected_category_name))

        tx = DB.Transaction(family_doc, 'Lora - Add shared parameters to family')
        tx.Start()
        try:
            for spec in parameter_specs:
                definition = spec['definition']
                param, action = _add_shared_parameter(family_doc, definition, spec['group_id'], spec['is_instance'])
                item = {'name': str(definition.Name), 'guid': _definition_guid(definition), 'action': action}
                if action.startswith('added'):
                    report['parameters_added'].append(item)
                else:
                    report['parameters_reused'].append(item)
            tx.Commit()
        except Exception:
            try:
                tx.RollBack()
            except Exception:
                pass
            raise

        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = False
        family_doc.SaveAs(output_path, save_options)
        report['saved'] = True
        report['label_authoring'] = 'not_generated'
        report['warnings'].append(
            'Native family saved with source-template labels/graphics preserved. The Revit public API does not provide a general supported method to compose arbitrary new tag-label fields programmatically; selected fields are not automatically inserted into label graphics by this stage.')
        return report
    except FamilyGenerationError:
        raise
    except Exception as ex:
        raise FamilyGenerationError('Family generation failed: {}'.format(ex))
    finally:
        if family_doc is not None:
            try:
                family_doc.Close(False)
            except Exception:
                pass


class LoraFamilyLoadOptions(DB.IFamilyLoadOptions):
    """IFamilyLoadOptions implementation for pyRevit's .NET runtime."""
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True


def load_family_into_project(project_doc, family_path):
    """Load generated RFA. Caller must confirm the operation first."""
    if not os.path.isfile(family_path):
        raise FamilyGenerationError('Generated family file could not be found.')
    options = LoraFamilyLoadOptions()
    try:
        ok = project_doc.LoadFamily(family_path, options)
        if not ok:
            # Revit may return false when the family is already loaded; find it by name.
            pass
        target_name = os.path.splitext(os.path.basename(family_path))[0]
        collector = DB.FilteredElementCollector(project_doc).OfClass(DB.Family)
        for family in collector:
            try:
                if str(family.Name).lower() == target_name.lower():
                    return family
            except Exception:
                pass
        raise FamilyGenerationError("Revit did not return a loaded family named '{}'.".format(target_name))
    except FamilyGenerationError:
        raise
    except Exception as ex:
        raise FamilyGenerationError('Could not load family into project: {}'.format(ex))
